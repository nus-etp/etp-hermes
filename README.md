# etp-hermes

Daily company-news digest. [hermes-agent](https://hermes-agent.nousresearch.com) runs in GitHub Actions, executes `hermes -z` against `prompts/news.md`, and commits the output to `signals/news/`.

## Setup

1. **Watchlist.** Edit `data/companies.json` — `{name, aliases?, description, sources?, identifiers?}`. `description` is what the LLM uses to judge relevance; use it to call out unrelated same-name entities and ticker collisions.
2. **Secret.** Settings → Secrets → Actions → `DEEPSEEK_API_KEY` (from platform.deepseek.com). The bootstrap script rewrites it as `OPENAI_API_KEY` in `~/.hermes/.env` — Hermes' `custom` provider reads that name.
3. **Test.** Actions → `hermes-sync` → Run workflow.

## Layout

```
.github/workflows/hermes-sync.yml   # daily cron + manual dispatch
scripts/bootstrap-hermes.sh         # mirrors hermes/ -> ~/.hermes/, writes .env
hermes/                             # mirrored to ~/.hermes/ (config, memories, skills)
prompts/news.md                     # prompt fed to `hermes -z`
data/{companies,feeds}.json         # watchlist + firehose feeds
signals/news/                       # daily digest + seen-urls.txt dedup state
```

`hermes/` is the Hermes runtime tree. Everything outside it stays repo-side and is read by the prompt via repo-relative paths. Not mirrored: `auth.json` (OAuth, unused with API keys), `sessions/`, `logs/`, `cron/` (we use GH Actions, not Hermes' scheduler).

## Pipeline

`prompts/news.md` runs four phases:

1. **Firehose triage** — fetch each feed in `data/feeds.json`, substring-match new items (≤7 days, not in `seen-urls.txt`) against `name + aliases`. Cheap, high-recall.
2. **Per-company collection (pilot)** — for companies with `sources`, fetch directly: RSS, GitHub org Atom, Lever jobs JSON, HTML newsroom pages. Bypasses substring match. Source taxonomy and dedup keys in `prompts/news.md`. Pilot: Carousell, Patsnap, Horizon Quantum Computing, NEU Battery Materials, polybee.
3. **Relevance pass** — LLM judges each `(headline, source, description)` against `c.description`. Firehose bias: drop. Per-company bias: keep, with drops for bot/chore GitHub events, evergreen Lever reqs, arXiv revisions, cross-source duplicates.
4. **Write** — kept items grouped by company into `signals/news/<UTC-date>.md`; both kept and dropped dedup keys appended to `seen-urls.txt` so we don't re-judge junk.

No search feeds (Google News, HN) — N×search calls don't scale past ~10 companies. Firehose-only is free per added company; per-company `sources` cost N fetches per opted-in company, hence the small pilot.

## Memory persistence

The workflow rsyncs `~/.hermes/memories/` → `hermes/memories/` and commits, so `MEMORY.md` / `USER.md` survive runs.

**Skills are not auto-synced** — `~/.hermes/skills/` mixes user skills with Hermes' bundled ~145k-line library. Copy authored skills into `hermes/skills/` manually.

Sessions, logs, and `state.db` are uploaded as a `hermes-logs-<run_id>` artifact (3-day retention) but not committed. Grab from the Actions run page when debugging.

## Local dry run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes -z "$(cat prompts/news.md)"
ls signals/news/
```
