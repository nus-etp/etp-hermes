# etp-hermes

Hosts a [hermes-agent](https://hermes-agent.nousresearch.com) deployment on GitHub Actions. A scheduled daily run executes `hermes -z` against a committed prompt, the agent writes outputs to `signals/`, the workflow commits them back.

## Setup

1. **Author your watchlist.** Edit `data/companies.json` — array of `{name, aliases?, exclude?}` objects. `aliases` add extra search/match terms; `exclude` is a case-insensitive substring blocklist applied to title + description + source (use it to kill ticker collisions, sub-brand bleed, and known junk sources).
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
  companies.json                    # watchlist: name + aliases + exclude rules
  feeds.json                        # feed sources (search + firehose)
signals/                            # repo-only: outputs committed each run
  news/
    seen-urls.txt                   # dedup state, appended across runs
    YYYY-MM-DD.md                   # daily digest
```

The split between `hermes/` and `prompts/` + `data/` is intentional: `hermes/` is exactly what `bootstrap-hermes.sh` copies into `~/.hermes/`. Everything outside `hermes/` stays repo-side and is referenced by the prompt's path expressions.

What we don't mirror to `~/.hermes/`: `auth.json` (OAuth, not used with API-key auth), `sessions/`, `logs/` (runtime/ephemeral), `cron/` (we bypass the internal scheduler — GH Actions cron is the timer).

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
