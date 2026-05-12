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

Tune behavior by editing the prompt and per-company `description` strings (that's where ticker collisions, same-name entities, and generic-word disambiguation are encoded).

## Non-obvious

- API key: `bootstrap-hermes.sh` writes the `DEEPSEEK_API_KEY` secret into `~/.hermes/.env` as `OPENAI_API_KEY` (what Hermes' `custom` provider reads). Don't rename the secret.
- Memory roundtrip: the workflow rsyncs `~/.hermes/memories/` → `hermes/memories/` and commits.
- Skills are **not** rsynced — `~/.hermes/skills/` mixes user skills with Hermes' ~145k-line bundled library. Copy authored skills into `hermes/skills/` manually.
- Sessions/logs: uploaded as a `hermes-logs-<run_id>` artifact (3-day retention), never committed.
- `seen-urls.txt` is append-only. No search feeds (Google News, HN) — intentional, doesn't scale.
- `identifiers` in `companies.json` is carry-only metadata; not fetched.
