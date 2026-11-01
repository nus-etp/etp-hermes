<p align="center"><img src="docs/assets/hermeneut.svg" width="200" alt="Hermeneut mascot"></p>

# hermeneut

Daily company intelligence pipeline for the NUS ETP watchlist. [hermes-agent](https://hermes-agent.nousresearch.com) runs in GitHub Actions and commits dated updates and per-company living briefs to `signals/`, published at https://nus-etp.github.io/hermeneut.

## Why Hermeneut?

A *hermeneut* is an interpreter, and the word shares a root with *hermeneutics*, the art of interpretation, which tradition ties to Hermes, the messenger god. The pipeline runs on hermes-agent and does the interpreter's job: it reads the day's scattered signals (news, filings, hiring, launches) and turns them into a living brief for each company.

## Quick start

1. **Watchlist** — edit `data/companies.json`. `description`, `match_terms` and `exclude_terms` control relevance.
2. **Secrets** — `DEEPSEEK_API_KEY` (primary) or `NVIDIA_API_KEY` under Settings → Secrets → Actions.
3. **Run** — Actions → `hermes-sync` → Run workflow. The `layers` input (default `1,2,3,4`) re-runs individual layers.

Pipeline, layout, local runs and design notes live in [AGENTS.md](AGENTS.md).
