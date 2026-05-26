# Honcho self-hosted: feasibility & deployment notes

Feasibility exploration for running [Plastic Labs' Honcho](https://github.com/plastic-labs/honcho) self-hosted memory service to back **long conversations with long-term memory**. Captures architecture, resource needs, deployment options, and cost — to support a yes/no/where-to-host decision before any integration work begins.

Not a commitment to use Honcho. Read once, decide, then either delete or convert to an implementation plan.

---

## Use case

An agent that talks to a user (or users) across many turns over weeks/months and recalls prior context, derived peer representations, and summaries. Reply latency tolerance: **minutes to hours, not seconds** — async/batched conversations are acceptable.

This rules out live interactive chat (where Honcho's strengths still apply but the deployment story is very different).

## Architecture (verified from upstream `docker-compose.yml.example`)

Four containers, all required:

| Service | Image | Port | Role |
|---|---|---|---|
| `api` | built from source | 8000 | FastAPI REST + Python SDK. Synchronous read/write. |
| `deriver` | same image, `python -m src.deriver` | — | Async background worker: peer representations, summaries, peer cards, dreaming. |
| `database` | `pgvector/pgvector:pg15` | 5432 | Postgres 15 + pgvector. Persists via `pgdata` volume. |
| `redis` | `redis:8.2` | 6379 | Cache + deriver job queue. Required (both `api` and `deriver` depend on it). |

Both `api` and `deriver` depend on `database` and `redis` being **healthy** before start. `CACHE_ENABLED=true` by default in production.

**LLM provider**: any OpenAI-compatible endpoint that supports tool/function calling — OpenRouter, Together, Fireworks, Ollama, vLLM, LiteLLM, DeepSeek, OpenAI, Anthropic, Gemini. DeepSeek-Chat qualifies.

**No built-in snapshot/restore** — backup is `pg_dump`. Redis state is mostly ephemeral *except* the deriver job queue (see "Deriver drain barrier" below).

## Resource requirements

**Upstream-tested baseline**: 6 GB RAM + 80 GB disk on Ubuntu 22.04+ with hosted-LLM embeddings. With local embeddings (e.g., Ollama), step up to 8 GB RAM + 4 cores.

**Per-component RSS estimate**:

| Component | Idle | Loaded |
|---|---|---|
| Postgres 15 + pgvector | 200–400 MB | +500 MB on vector queries |
| Redis 8 | 50–100 MB | small growth with queue depth |
| `api` | 300–500 MB | 0.5–1 GB |
| `deriver` (1 worker) | 300–500 MB | 0.5–2 GB |
| **Total** | **~1–1.5 GB idle** | **~2–4 GB under load** |

Add ~300–500 MB per extra `DERIVER_WORKERS`.

**Disk growth**: ~10–20 KB per stored message (1536-dim float32 embedding + text + indices). 10k messages ≈ 100–200 MB. 100k ≈ 1–2 GB. `pg_dump --format=custom --compress=9` lands at 30–50% of live DB size.

**Scaling levers**:
- `DERIVER_WORKERS` (default `1`) — multiple worker threads in one process.
- Multiple `deriver` *containers* — coordinate via the Postgres job queue.
- `CACHE_ENABLED=false` — turns Redis off as a cache; queue still needs it.

## Three deployment shapes — pick exactly one

### Shape A — Live chat per workflow tick *(don't do this)*

Webhook fires a workflow on every user message. Workflow boots Postgres + Honcho, restores cache, sends one message, dumps, saves cache, exits.

Why it's bad: 60–90 s boot per turn (users walk away); per-turn cache contention; concurrent users overwriting each other's memory state on save. The GHA cache is not a transactional database — using it as one will silently corrupt memory the first time two turns interleave.

### Shape B — Batched async on a schedule **← matches our use case**

User messages accumulate in a queue (Slack channel, GitHub issue comments, a file inbox, an HTTP endpoint, a durable table). A scheduled tick — every 15 min, hourly, daily — drains the queue, processes each message through Honcho, posts replies, persists state, exits.

Works because:
- Boot cost amortized over many turns.
- Single writer per tick (enforce via workflow `concurrency: group: honcho-memory cancel-in-progress: false`).
- The "long conversation" feel is preserved — memory carries across batches — without per-turn latency pressure.

### Shape C — Long-running self-hosted host *(genuinely simpler than Shape B if you don't need ephemeral)*

A small always-on host runs the four containers continuously. Postgres + Honcho persist on disk. Cron (or a tiny HTTP webhook) triggers the drain script.

Works because:
- No `pg_dump`/`pg_restore` cycle — DB just stays on disk.
- No deriver-drain barrier before backup — deriver keeps running between ticks; ticks just enqueue work.
- No concurrency hazard on a cache.
- Per-turn latency drops to <500 ms if you ever want to evolve from batched to live.

**For our async-conversation use case, Shape C is strictly simpler than Shape B run on GHA, and the $7/mo VM cost is below the LLM-spend noise floor anyway.**

## Cost (USD/mo)

### Infra

| Approach | Spec | Price |
|---|---|---|
| GHA runner only (Shape B) | public repo runners are free; private repos get 2 000 free min/mo | **~$0** |
| **Hetzner CX32** (Shape C, recommended) | 4 vCPU / 8 GB / 80 GB | **~$7** |
| Hetzner CX42 (Shape C, headroom) | 8 vCPU / 16 GB / 160 GB | ~$18 |
| DigitalOcean Basic 2 vCPU / 4 GB | tight on RAM | $24 |
| Managed: Neon Postgres (PAYG) + Upstash Redis + Fly Machines | survives host loss; auto backups | $35–80 |
| AWS Fargate + RDS + ElastiCache | compliance/scale tier | $130–200+ |

### LLM API (this dominates at any real volume)

Per message: ~1 embedding call + 1–3 derivation completions. Embedding cost is rounding error; completion model picks the bill.

| Deriver LLM | per msg | 100/day | 1 k/day | 10 k/day |
|---|---|---|---|---|
| **DeepSeek-Chat** | ~$0.001 | $1.50–3 | **$15–30** | $150–300 |
| GPT-4o-mini | ~$0.001 | $2.40–4.50 | $24–45 | $240–450 |
| Claude Haiku 4.5 | ~$0.005–0.01 | $15–30 | $150–300 | $1.5–3 k |
| Claude Sonnet 4.6 | ~$0.02–0.05 | $60–150 | $600–1 500 | $6–15 k |

Use DeepSeek (or any cheap OpenAI-compatible model with function calling) for the deriver unless evals show quality matters here. Derivation is extractive/summarization-shaped; the quality gap on cheap models is smaller than for the user-facing model.

### Total scenarios

| Use case | Infra | LLM (DeepSeek) | **Total/mo** |
|---|---|---|---|
| Personal agent, ~30 msgs/day | Hetzner CX32 ($7) | $0.50–1 | **~$8** |
| Small team, ~300 msgs/day | CX32 or CX42 | $5–10 | **$15–30** |
| Light SaaS, ~3 k msgs/day | Managed tier (~$60) | $50–100 | **$110–180** |
| Mid SaaS, ~30 k msgs/day | AWS tier (~$150–200) | $500–1 000 | **$650–1 200** |

## Recommendation

For **long conversations with long-term memory at personal-to-small-team scale**:

1. **Run Honcho on a Hetzner CX32 (~$7/mo)** using upstream's `docker-compose.yml`. Don't use GHA runners — the cold-start dance and cache concurrency hazard are real, and the $7/mo box removes them entirely.
2. **Use DeepSeek-Chat for the deriver** unless evals show a quality reason to upgrade. It's the cheapest tool-calling-compatible model and derivation tasks are forgiving.
3. **Pick a durable queue source** (Slack channel, GitHub issues, dedicated table) — this is the integration shape, not "fire-and-forget HTTP." Whatever you pick has to survive between ticks.
4. **Set `concurrency: group: honcho-memory cancel-in-progress: false`** on any scheduled job that mutates Honcho state — even on a single VM, future-you might add a second tick source.
5. **Back up with cron'd `pg_dump`** to S3 / object storage / a private repo (gzipped plain SQL is fine at this scale). Once a day is plenty.

## What's not feasible

- **Running Honcho without Postgres.** No SQLite / embedded mode upstream. If that's a hard requirement, fork or wait.
- **Live <1 s chat on a GHA runner.** Cold-start kills UX even if persistence works.
- **Committing the live database to the repo.** Embeddings make diffs unreadable; size growth is unbounded. Snapshots to object storage or GHA cache only.
- **Skipping the deriver to "just use the storage."** Possible but at that point you've reinvented Postgres + pgvector with extra YAML.

## Open questions

Before any implementation:

- [ ] **Queue source**: where do user messages live between ticks? (Slack, GH issues, file inbox, HTTP, …)
- [ ] **Reply destination**: same channel as the queue, or different?
- [ ] **Tick cadence**: 15 min / hourly / daily?
- [ ] **Multi-user model**: one Honcho `peer` per user, or single peer with a shared agent? Affects representation quality.
- [ ] **Eval coverage**: how do we measure whether Honcho's memory is actually helping? Without an eval, "did it remember the right thing" is an opinion.
- [ ] **Migration path**: pin a Honcho image tag. Upstream is pre-1.0 — version bumps may require manual schema migration. Decide cadence.

## Alternative: skip Honcho

If derivation needs end up minimal — e.g., you just want "did I talk about this before" rather than full peer modeling — a 50-line script with `pgvector` directly (or `sqlite-vec` for true single-file portability) delivers 80% of the value with ~10% of the moving parts. Worth a half-day spike before committing to running four services.

---

*Notes derived from: upstream `plastic-labs/honcho` README + `docker-compose.yml.example`, [Honcho v3 self-hosting docs](https://honcho.dev/docs/v3/contributing/self-hosting), Hetzner/Fly/Neon/DigitalOcean published pricing, DeepSeek published pricing. Current as of May 2026.*
