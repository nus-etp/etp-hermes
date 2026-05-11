# etp-hermes

Hosts a [hermes-agent](https://hermes-agent.nousresearch.com) deployment on GitHub Actions. A scheduled daily run executes `hermes -z` against a committed prompt, the agent writes outputs to `signals/`, the workflow commits them back.

## Setup

1. **Author your watchlist.** Edit `data/companies.txt` (one company per line).
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
  companies.txt
signals/                            # repo-only: outputs committed each run
  news/
    seen-urls.txt                   # dedup state, appended across runs
    YYYY-MM-DD.md                   # daily digest
```

The split between `hermes/` and `prompts/` + `data/` is intentional: `hermes/` is exactly what `bootstrap-hermes.sh` copies into `~/.hermes/`. Everything outside `hermes/` stays repo-side and is referenced by the prompt's path expressions.

What we don't mirror to `~/.hermes/`: `auth.json` (OAuth, not used with API-key auth), `sessions/`, `logs/` (runtime/ephemeral), `cron/` (we bypass the internal scheduler — GH Actions cron is the timer).

## Local dry run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes -z "$(cat prompts/news.md)"
ls signals/news/
```
