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
- Jina Reader prefetch: `scripts/jina-reader.py` runs between preflight and Layer 1. For every html_scrape URL preflight marked changed, it fetches `https://r.jina.ai/<url>` (with optional `JINA_API_KEY` Bearer for the 100/day free tier), caches the markdown under `data/jina-cache/` (gitignored, GHA cache key `jina-cache-*`), and runs a heading→link heuristic to write structured items into `data/jina-items.json`. The ingest prompt's `html_scrape` branch reads `jina-items.json` first and Step 3 skips items flagged `pre_extracted: true`; URLs in `extraction_failed`/`deferred` fall back to the LLM HTML-parse path. Daily call budget: `JINA_DAILY_BUDGET` (default 80). Fails open everywhere — missing key, HTTP error, or extraction miss all degrade to the existing prompt path. For local runs invoke `python3 scripts/jina-reader.py` after preflight.
- API key: `bootstrap-hermes.sh` writes the `DEEPSEEK_API_KEY` secret into `~/.hermes/.env` as `OPENAI_API_KEY` (what Hermes' `custom` provider reads). Don't rename the secret.
- Provider failover: `hermes/config.yaml` lists `fallback_providers: [{provider: xiaomi, model: mimo-v2.5}]`. When the primary `deepseek` provider is exhausted mid-turn (rate-limit, billing, 5xx, or connection failure after `agent.api_max_retries`), Hermes' `-z`/oneshot path (`hermes_cli/oneshot.py` reads `fallback_providers` → passes it as the agent's `_fallback_chain`) swaps to the next entry. Failover is **turn-scoped** — each new turn restores `deepseek` first. The bundled `xiaomi` provider profile reads `XIAOMI_API_KEY` from `~/.hermes/.env`, seeded by `bootstrap-hermes.sh` from the `XIAOMI_API_KEY` GHA secret. Fails open: if the secret is absent the chain entry resolves to no client and is skipped, so forks without it run on `deepseek` alone, unchanged. To swap the fallback model, edit the `model:` under `fallback_providers` (native catalog: `mimo-v2-flash`/`mimo-v2.5`/`mimo-v2.5-pro`/`mimo-v2-pro`/`mimo-v2-omni`).
- State roundtrip: each workflow run restores the prior `hermes backup` zip from GitHub Actions cache (`hermes-state-*`), imports it with `hermes import --force`, runs the layers, then re-runs `hermes backup` and saves the (sanitized) zip back to the cache. The `~/.hermes/memories/` → `hermes/memories/` rsync + commit is still done as a belt-and-suspenders fallback so a lost cache doesn't cold-start the agent. `scripts/sync-hermes-local.sh` mirrors the same pattern with a gitignored `.hermes-state.zip` in the repo root.
- Skills are **not** rsynced — `~/.hermes/skills/` mixes user skills with Hermes' ~145k-line bundled library. Copy authored skills into `hermes/skills/` manually.
- Sessions/logs: uploaded as a `hermes-logs-<run_id>` artifact (3-day retention), never committed.
- Langfuse traces: hermes-agent ships a bundled `observability/langfuse` plugin that streams each turn (LLM call + tool calls) to Langfuse Cloud live, with no repo-side ingest script. Activation is three things, all already wired: `hermes/config.yaml` lists the plugin under `plugins.enabled`, `bootstrap-hermes.sh` writes `HERMES_LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`BASE_URL` into `~/.hermes/.env` when those GHA secrets are present, and the "Install ddgs + langfuse into hermes venv" workflow step `uv pip install`s the SDK into hermes' Python interpreter. The plugin fails open: missing SDK or missing creds → hooks no-op silently, hermes runs unchanged (so forks without the secrets are unaffected). Tunables (set via GHA secret or `~/.hermes/.env`): `HERMES_LANGFUSE_ENV`, `HERMES_LANGFUSE_RELEASE`, `HERMES_LANGFUSE_SAMPLE_RATE`, `HERMES_LANGFUSE_MAX_CHARS`, `HERMES_LANGFUSE_DEBUG`.
- Monthly Langfuse usage rollup: `scripts/langfuse_usage.py` reads usage back *out* of Langfuse (the inverse of the trace-emitting plugin/evals above). For a given UTC month it queries the public Daily Metrics API (`GET /api/public/metrics/daily`, Basic-auth with the public/secret key pair) — once unfiltered for the grand total, then once per environment in `LANGFUSE_USAGE_ENVIRONMENTS` (default `production,eval`) — aggregates the per-day rows by environment + model, and writes `data/langfuse-usage/<YYYY-MM>.json` (committed, idempotent per month) plus `signals/langfuse-usage.md` (a month-over-month history table built from *every* snapshot on disk + a detail view of the latest month). Pure stdlib (urllib), no SDK dependency. Reuses the same `HERMES_LANGFUSE_{PUBLIC_KEY,SECRET_KEY,BASE_URL}` secrets — base URL defaults to `https://cloud.langfuse.com`. Fails open on *missing* creds (prints notice, exits 0 so forks/local don't break); a real HTTP error when creds are present exits 1 so the workflow surfaces it. `.github/workflows/langfuse-usage.yml` runs it on the 1st of each month (06:00 UTC, collecting the just-closed previous month) + `workflow_dispatch` with an optional `month` input, then commits the JSON + Markdown. Unit-tested with mocked HTTP in `tests/scripts/test_langfuse_usage.py` (runs on every PR via the evals `fast` job). Local: `python3 scripts/langfuse_usage.py --month YYYY-MM`.
- `seen-urls.txt` is append-only. No search feeds (Google News, HN) — intentional, doesn't scale.
- `identifiers` in `companies.json` is carry-only metadata; not fetched.
- Briefs embed `![Infographic](infographic.png)` directly under the `_Last updated:_` line. Synthesis writes this line unconditionally; if Layer 4 didn't run (or failed for that slug), the image renders as a 404 placeholder until the next successful infographic run. This decouples Layer 3 from Layer 4's success — synthesis never waits on `image_generate`.
- Evals live in `tests/` (pytest). Three suites: `tests/static/` (data schema, brief template, slug rule — fast, no API), `tests/scripts/` (unit tests for the Python helpers with mocked HTTP), `tests/evals/` (LLM-behaviour evals gated on `DEEPSEEK_API_KEY`; the `test_batch_integration.py` integration eval additionally requires `HERMES_BATCH_EVAL=1` and a local hermes-agent install at `~/.hermes/hermes-agent/batch_runner.py`). Static + scripts run on every PR via `.github/workflows/evals.yml`; the LLM suite runs nightly at 12:30 UTC and is skipped on PRs. Local: `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest tests/static tests/scripts -q`.
- Eval Langfuse observability: the nightly LLM suite traces every `chat()`/`judge()` call (prompt, completion, token usage, latency) and emits per-test scores (`precision`/`recall`/`template_pass`/`no_fabrication_pass`/`sub_bullet_clean` + a uniform `passed`/`duration_s`) to Langfuse for trend history. Implemented in `tests/evals/_obs.py` (fail-open shim) + the `eval_obs` fixture and `pytest_runtest_makereport` hook in `tests/evals/conftest.py`; scores are emitted *before* threshold asserts so regressions are recorded even when a test fails. Reuses the same `HERMES_LANGFUSE_{PUBLIC_KEY,SECRET_KEY,BASE_URL}` secrets as the production plugin (`HERMES_LANGFUSE_ENV=eval`), and `langfuse` is `uv pip install`ed only in the `llm` job — the `fast`/PR job and local runs stay langfuse-free and the shim no-ops (no SDK or no creds → fail open, suite unchanged). The pytest thresholds remain the hard pass/fail gate; Langfuse is purely additive. `tests/scripts/test_eval_obs.py` unit-tests the shim's fail-open + routing on every PR.
