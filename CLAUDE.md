# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A scheduled hermes-agent deployment. No app to build/test — the "product" is the daily Markdown digest in `signals/news/`. Behavior is configured by editing the prompt and data files; there is no source code.

## Layout

`scripts/bootstrap-hermes.sh` copies `hermes/` verbatim into `$HOME/.hermes/`. Everything outside `hermes/` is repo-side, referenced by the prompt at runtime.

- `hermes/` → `~/.hermes/` (config, memories, optional skills)
- `prompts/news.md` — prompt fed to `hermes -z`
- `data/{companies,feeds}.json` — watchlist + firehose feeds
- `signals/news/` — outputs; the **only** path the agent may write
- `.github/workflows/hermes-sync.yml` — daily 13:00 UTC cron

## Local run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes -z "$(cat prompts/news.md)"
```

## Pipeline (defined in `prompts/news.md`)

1. **Firehose triage** — substring-match new feed items against `name + aliases`.
2. **Per-company collection** — fetch curated `sources` (rss / github_org / lever_jobs / html_scrape) for opted-in companies.
3. **LLM relevance pass** — judge each candidate against `c.description`. Firehose bias: drop. Per-company bias: keep.
4. **Write** — group kept items into `signals/news/<UTC-date>.md`; append both kept *and* dropped dedup keys to `seen-urls.txt`.
5. **Infographic generation** — `prompts/infographics.md` runs after synthesis. For every brief modified or created in this run (computed from `git diff` against `HEAD` plus untracked-file enumeration), invoke the bundled `creative-baoyu-infographic` skill and copy the resulting PNG to `signals/briefs/<slug>/infographic.png`. Capped at 8 per run. Skill intermediates under `infographic/` are gitignored.

Tune behavior by editing the prompt and per-company `description` strings (that's where ticker collisions, same-name entities, and generic-word disambiguation are encoded).

## Non-obvious

- Change-detection preflight: `scripts/preflight-feeds.py` runs before Layer 1 and sends conditional HTTP requests (ETag/Last-Modified + body-SHA256 fallback) against every preflightable source URL. It writes `data/feed-cache.json` (per-URL state, gitignored, kept across runs via the GHA `feed-cache-*` cache key) and `data/changed-sources.json` (the URL whitelist the ingest prompt is allowed to fetch). `html_scrape` sources are not preflighted — they're always listed as changed. For local runs, invoke `python3 scripts/preflight-feeds.py` before `hermes -z` to get the same skip behavior.
- API key: `bootstrap-hermes.sh` writes the `DEEPSEEK_API_KEY` secret into `~/.hermes/.env` as `OPENAI_API_KEY` (what Hermes' `custom` provider reads). Don't rename the secret.
- State roundtrip: each workflow run restores the prior `hermes backup` zip from GitHub Actions cache (`hermes-state-*`), imports it with `hermes import --force`, runs the layers, then re-runs `hermes backup` and saves the (sanitized) zip back to the cache. The `~/.hermes/memories/` → `hermes/memories/` rsync + commit is still done as a belt-and-suspenders fallback so a lost cache doesn't cold-start the agent. `scripts/sync-hermes-local.sh` mirrors the same pattern with a gitignored `.hermes-state.zip` in the repo root.
- Skills are **not** rsynced — `~/.hermes/skills/` mixes user skills with Hermes' ~145k-line bundled library. Copy authored skills into `hermes/skills/` manually.
- Sessions/logs: uploaded as a `hermes-logs-<run_id>` artifact (3-day retention), never committed.
- `seen-urls.txt` is append-only. No search feeds (Google News, HN) — intentional, doesn't scale.
- `identifiers` in `companies.json` is carry-only metadata; not fetched.
- Briefs embed `![Infographic](infographic.png)` directly under the `_Last updated:_` line. Synthesis writes this line unconditionally; if Layer 4 didn't run (or failed for that slug), the image renders as a 404 placeholder until the next successful infographic run. This decouples Layer 3 from Layer 4's success — synthesis never waits on `image_generate`.
