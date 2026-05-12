# etp-hermes

Hosts a [hermes-agent](https://hermes-agent.nousresearch.com) deployment on GitHub Actions. A scheduled daily run executes `hermes -z` against a committed prompt, the agent writes outputs to `signals/`, the workflow commits them back.

## Setup

1. **Author your watchlist.** Edit `data/companies.json` — array of `{name, aliases?, description, sources?, identifiers?}` objects. `aliases` add extra substring patterns for the triage match. `description` is a one- or two-sentence company description that the agent uses to judge whether a candidate item is genuinely about the company (the "LLM relevance pass" — see Pipeline below). Use the description to explicitly call out unrelated entities to exclude (other companies with the same name, ticker collisions, etc.). `sources` (optional) is a per-company list of curated feeds (RSS, GitHub org Atom, Lever jobs JSON, HTML newsroom pages); see Pipeline phase 2.5 and the source-type taxonomy in `prompts/news.md`. `identifiers` (optional) is carry-only metadata (LinkedIn URL, ACRA UEN, etc.) — not fetched in this pipeline.
2. **Add the secret.** Repo Settings → Secrets and variables → Actions → New secret: `DEEPSEEK_API_KEY` (from platform.deepseek.com). The bootstrap script rewrites it as `OPENAI_API_KEY` in `~/.hermes/.env` because Hermes' "custom" provider reads that name.
3. **Test.** Push, then Actions tab → `hermes-sync` → **Run workflow**.

## Layout

```
.github/workflows/hermes-sync.yml   # daily schedule + manual dispatch
scripts/bootstrap-hermes.sh         # mirrors hermes/ -> ~/.hermes/ and writes .env
hermes/                             # mirror of ~/.hermes/ (Hermes runtime tree)
  config.yaml                       # provider, model, agent settings
  SOUL.md                           # optional: agent identity
  memories/                         # optional: MEMORY.md, USER.md
  skills/                           # optional: pre-authored skills
prompts/                            # repo-only: prompts fed to `hermes -z`
  news.md
data/                               # repo-only: inputs read by prompts
  companies.json                    # watchlist: name + aliases + description
  feeds.json                        # firehose RSS feeds
signals/                            # repo-only: outputs committed each run
  news/
    seen-urls.txt                   # dedup state, appended across runs
    YYYY-MM-DD.md                   # daily digest
```

The split between `hermes/` and `prompts/` + `data/` is intentional: `hermes/` is exactly what `bootstrap-hermes.sh` copies into `~/.hermes/`. Everything outside `hermes/` stays repo-side and is referenced by the prompt's path expressions.

What we don't mirror to `~/.hermes/`: `auth.json` (OAuth, not used with API-key auth), `sessions/`, `logs/` (runtime/ephemeral), `cron/` (we bypass the internal scheduler — GH Actions cron is the timer).

## Pipeline

The news prompt runs four phases each day:

1. **Firehose triage.** Fetch each firehose feed in `data/feeds.json`, walk new items (≤7 days, not in `seen-urls.txt`), and substring-match titles and descriptions against `name + aliases` of every company in `data/companies.json`. Cheap, high-recall, lots of false positives.
2. **Per-company collection (pilot).** For each company with a non-empty `sources` array, fetch its curated sources directly — RSS, GitHub org Atom (`https://github.com/<org>.atom`), Lever jobs JSON, and HTML newsroom pages — and emit candidates already-bound to that company (no substring match needed). Source taxonomy and dedup-key rules live in `prompts/news.md`. Currently active on 5 pilot companies (Carousell, Patsnap, Horizon Quantum Computing, NEU Battery Materials, polybee).
3. **Relevance pass.** The agent (an LLM) reads each candidate's `(headline, source, description)` and judges, using the company's `description`, whether the item is genuinely about that company. Bias for firehose candidates: drop. Bias for per-company candidates (already curated and company-scoped): keep, with explicit drop rules for bot/chore GitHub events, evergreen Lever reqs, arXiv version-revisions, and cross-source duplicates. This is what makes the digest readable with a 100+ company watchlist — the prior exclude-rule approach didn't scale past a handful of generic-named companies. The triage match keeps the LLM budget tractable; the LLM keeps the precision high.
4. **Write.** Surviving candidates get grouped by company and written to `signals/news/<UTC-date>.md`. *Both* surviving and dropped dedup keys are appended to `seen-urls.txt` so we don't re-judge the same junk every day.

Search feeds (Google News, HN) are intentionally absent: at 100+ companies they create N×search HTTP calls per run and rate-limit fast. The firehose-only model scales by company count for free — adding a 200th company costs nothing in fetch volume. Per-company sources do cost N fetches per company that opts in, which is why the pilot is small and we don't enrol the whole watchlist until the noise/signal tradeoff is validated.

## Agent learning across runs

The workflow rsyncs `~/.hermes/memories/` back to `hermes/memories/` after each run, then commits. This makes the agent's declarative memory durable:

- **Memory** (`hermes/memories/MEMORY.md`, `USER.md`) — facts the agent decides to remember via the `memory` tool. Loaded into the system prompt at session start.

**Skills are not auto-synced.** Hermes' `~/.hermes/skills/` contains both user-authored skills AND the bundled skill library (~530 skills, ~145k lines), with no clean separator beyond `.bundled_manifest`. A blind rsync would balloon the repo on every run. If you author a skill via `skill_manage` and want to keep it, copy it manually from `~/.hermes/skills/<your-skill>/` into `hermes/skills/`.

Sessions, logs, and `state.db` are also not committed — they're audit/debug artifacts, not learning. But the workflow uploads `~/.hermes/logs/` and `~/.hermes/sessions/` as a `hermes-logs-<run_id>` artifact on every run (including failed ones), with 3-day retention. Grab them from the Actions run page when you need to debug a digest. Hermes can still search past sessions on demand via the `session_search` tool *within* a run, but cross-run search would need `state.db` committed (avoided; SQLite-in-git diffs poorly).

## Local dry run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes -z "$(cat prompts/news.md)"
ls signals/news/
```
