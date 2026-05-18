# Public-attention metrics

SocialBlade-style daily snapshots for each watchlisted company, refreshed by the
`hermes-sync` workflow.

- Time series → `data/metrics/<slug>.jsonl` (one record per UTC day, append-only).
- Charts → `signals/metrics/<slug>.png`, rolling 90-day window.
- A company with no non-zero history in any series gets no PNG.

## Sources (free, public, rate-limited)

| Field | Source | Window | When collected |
|---|---|---|---|
| `github.{stars,followers,repos}` | `api.github.com` | snapshot | company has a `github_org` source in `companies.json` |
| `lever.open` | Lever public board JSON | snapshot | company has a `lever_jobs` source |
| `hn_30d` | HN Algolia search (`nbHits` for `"<name>"`) | rolling 30 days | all companies |
| `gdelt_7d` | GDELT DOC 2.0 (`articles` length, capped at 250) | rolling 7 days | all companies |

A missing field means "no applicable source for this company." A `null` value
means "endpoint was tried but failed or returned nothing." A numeric value is a
real count.

## Re-run / extend

```bash
python3 scripts/collect_metrics.py                 # all companies, today
python3 scripts/collect_metrics.py --only Carousell # just one
python3 scripts/render_metrics.py                  # regenerate all PNGs
```

Adding a new source: extend `collect_one` in `scripts/collect_metrics.py` and
add a new panel tuple to the list in `render_company` in `scripts/render_metrics.py`.
