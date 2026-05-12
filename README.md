# etp-hermes

Daily company intelligence pipeline. [hermes-agent](https://hermes-agent.nousresearch.com) runs in GitHub Actions, executes three sequential prompts, and commits the outputs to `signals/`.

## Setup

1. **Watchlist.** Edit `data/companies.json` — `{name, aliases?, description, sources?, identifiers?}`. `description` is what the LLM uses to judge relevance; use it to call out unrelated same-name entities and ticker collisions.
2. **Secret.** Settings → Secrets → Actions → `DEEPSEEK_API_KEY` (from platform.deepseek.com). The bootstrap script rewrites it as `OPENAI_API_KEY` in `~/.hermes/.env` — Hermes' `custom` provider reads that name.
3. **Test.** Actions → `hermes-sync` → Run workflow. The `layers` input defaults to `1,2,3`; set it to e.g. `3` to re-synthesize only.

## Layout

```
.github/workflows/hermes-sync.yml   # daily cron + manual dispatch (layers input)
scripts/bootstrap-hermes.sh         # mirrors hermes/ -> ~/.hermes/, writes .env
hermes/                             # mirrored to ~/.hermes/ (config, memories, skills)
prompts/
  ingest.md                         # Layer 1: deterministic feeds
  agent_supplement.md               # Layer 2: dynamic web/browser search
  synthesis.md                      # Layer 3: per-company LIVING_BRIEF
data/{companies,feeds}.json         # watchlist + firehose feeds
signals/
  seen-urls.txt                     # shared dedup state across L1+L2
  updates/<YYYY-MM-DD>.md           # L1 output, one per UTC day
  agent/<YYYY-MM-DD>.md             # L2 output, one per UTC day
  briefs/<slug>/LIVING_BRIEF.md     # L3 output, per-company rolling brief
```

`hermes/` is the Hermes runtime tree. Everything outside it stays repo-side and is read by the prompts via repo-relative paths. Not mirrored: `auth.json` (OAuth, unused with API keys), `sessions/`, `logs/`, `cron/` (we use GH Actions, not Hermes' scheduler).

## Pipeline

The workflow runs three Hermes invocations in order. Each layer is its own prompt — re-runnable independently via the `layers` workflow input.

### Layer 1 — data ingestion (`prompts/ingest.md`)

Deterministic feeds → `signals/updates/<UTC-date>.md`. Four phases:

1. **Firehose triage** — fetch each feed in `data/feeds.json`, substring-match new items (≤7 days, not in `seen-urls.txt`) against `name + aliases`. Cheap, high-recall.
2. **Per-company collection** — for companies with `sources`, fetch directly: RSS, GitHub org Atom, Lever jobs JSON, HTML newsroom pages. Bypasses substring match. Source taxonomy in `prompts/ingest.md`. Currently opted in: Carousell, Patsnap, Horizon Quantum Computing, NEU Battery Materials, polybee.
3. **Relevance pass** — LLM judges each `(headline, source, description)` against `c.description`. Firehose bias: drop. Per-company bias: keep, with drops for bot/chore GitHub events, evergreen Lever reqs, arXiv revisions, cross-source duplicates.
4. **Write** — kept items grouped by company into `signals/updates/<UTC-date>.md`; both kept and dropped dedup keys appended to `signals/seen-urls.txt`.

No search feeds (Google News, HN) at this layer — they don't scale per added company. That's what Layer 2 is for.

### Layer 2 — agent supplement (`prompts/agent_supplement.md`)

Dynamic web/browser search → `signals/agent/<UTC-date>.md`. Two cohorts:

- **Gap-fill** — companies with zero kept items across the last 7 days of `signals/updates/*.md`. Otherwise invisible to the pipeline. Hermes runs 1–2 targeted queries per company (web search scoped to last 7 days; company website if `identifiers.website` is set).
- **Deepen** — companies that appear in today's `signals/updates/<today>.md`. Hermes runs one query per company to find related coverage, valuation/investor context, or corroboration that the firehose didn't surface.

Budget: 50 ops total per run, gap-fill prioritized over deepen. Same relevance discipline as Layer 1; same shared `signals/seen-urls.txt`. Wrapped with `continue-on-error: true` — a Layer 2 failure doesn't block Layer 3.

### Layer 3 — synthesis (`prompts/synthesis.md`)

For every company named in today's `signals/updates/<date>.md` or `signals/agent/<date>.md`, update a rolling per-company brief at `signals/briefs/<slug>/LIVING_BRIEF.md`. Slug = lowercase-kebab-case of `c.name`.

Each brief has fixed sections: **Thesis** (2–3 sentence rolling assessment), **Profile** (slow-changing canonical facts from `companies.json`), **Recent signals** (most recent first, up to 20 inline), **Older signals** (overflow), **Open questions**. Update rules:

- New signals prepend to `Recent signals`; overflow spills into `Older signals`.
- `Thesis` is rewritten only when new signals materially shift trajectory; otherwise preserved verbatim.
- `Profile` touched only when a signal contradicts/extends a field.
- `Open questions` is append-and-cull.
- No-write if merged contents are byte-identical to existing file (keeps git history clean).

Scope: only companies touched today. No watchlist-wide refresh.

## Re-running individual layers

Workflow dispatch accepts a `layers` input. Examples:

- `1,2,3` (default) — full pipeline.
- `3` — re-synthesize briefs against today's existing `signals/updates/` and `signals/agent/` files. Useful when fixing the synthesis prompt without re-spending budget on ingestion.
- `1` — Layer 1 only (Layer 2 won't run, so synthesis would be missing the agent supplement — usually not what you want).

## Memory persistence

The workflow rsyncs `~/.hermes/memories/` → `hermes/memories/` and commits, so `MEMORY.md` / `USER.md` survive runs.

**Skills are not auto-synced** — `~/.hermes/skills/` mixes user skills with Hermes' bundled ~145k-line library. Copy authored skills into `hermes/skills/` manually.

Sessions, logs, and `state.db` are uploaded as a `hermes-logs-<run_id>` artifact (3-day retention) but not committed. Grab from the Actions run page when debugging.

## Local dry run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes -z "$(cat prompts/ingest.md)"
hermes -z "$(cat prompts/agent_supplement.md)"
hermes -z "$(cat prompts/synthesis.md)"
ls signals/updates/ signals/agent/ signals/briefs/
```
