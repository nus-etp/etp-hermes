<p align="center"><img src="docs/assets/hermenaut.svg" width="200" alt="Hermenaut mascot"></p>

# hermenaut

Daily company intelligence pipeline for the NUSX watchlist. [hermes-agent](https://hermes-agent.nousresearch.com) runs in GitHub Actions and commits dated updates and per-company living briefs to `signals/`, published at https://nus-etp.github.io/hermenaut.

## Why Hermenaut?

*Hermes*, the messenger god, plus *-naut*, the voyager (as in astronaut). The pipeline runs on hermes-agent and goes out every day to explore the watchlist's corner of the startup universe, gathering scattered signals (news, filings, hiring, launches) and bringing back a living brief for each company.

## Quick start

1. **Watchlist** — edit `data/companies.json`. `description`, `match_terms` and `exclude_terms` control relevance.
2. **Secrets** — `DEEPSEEK_API_KEY` (primary) or `NVIDIA_API_KEY` under Settings → Secrets → Actions.
3. **Run** — Actions → `hermes-sync` → Run workflow. The `layers` input (default `1,2,3,4`) re-runs individual layers.

Pipeline, layout, local runs and design notes live in [AGENTS.md](AGENTS.md).
