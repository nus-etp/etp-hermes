# etp-hermes

Hosts a [hermes-agent](https://hermes-agent.nousresearch.com) deployment on GitHub Actions. Daily scheduled run executes due cron jobs, then commits outputs to `signals/`.

## Setup

1. Install Hermes locally and run `hermes setup` to author `~/.hermes/config.yaml` and add cron jobs (`/cron add ...`).
2. Copy the authored files into this repo:
   - `~/.hermes/config.yaml` → `hermes/config.yaml`
   - `~/.hermes/cron/jobs.json` → `hermes/cron/jobs.json`
   - Optional: `SOUL.md`, `MEMORY.md`, `USER.md`, `AGENTS.md`, `skills/` → under `hermes/`
3. Add LLM provider secrets to the repo's GitHub Secrets (Settings → Secrets and variables → Actions):
   - `DEEPSEEK_API_KEY` — from platform.deepseek.com. The bootstrap script rewrites it as `OPENAI_API_KEY` inside `~/.hermes/.env` because Hermes' "custom" provider reads that name.
4. Push. Trigger a test run from the Actions tab via **Run workflow** on `hermes-sync`.

## Layout

| Path | Purpose |
|---|---|
| `.github/workflows/hermes-sync.yml` | Daily schedule (13:00 UTC) + manual dispatch |
| `scripts/bootstrap-hermes.sh` | Restores `hermes/` into `$HOME/.hermes/` and writes `.env` from env |
| `hermes/config.yaml` | LLM provider + agent config (no secrets) |
| `hermes/cron/jobs.json` | Scheduled job definitions consumed by `hermes cron tick` |
| `signals/` | Output sink — committed back to the repo each run |

## Local dry run

```bash
export DEEPSEEK_API_KEY=...
bash scripts/bootstrap-hermes.sh
hermes cron tick
ls ~/.hermes/cron/output/
```
