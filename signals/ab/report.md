# A/B report — v1 (production) vs v2 (freed judgment)

v1 encodes editorial judgment as prompt rules; v2 states the goal and
lets the model judge. Items unique to one arm are the disagreements —
review them to decide which policy filters better.

## History

| date | v1 items | v2 items | overlap | v1 only | v2 only | jaccard | v1 companies | v2 companies |
|------|----------|----------|---------|---------|---------|---------|--------------|--------------|
| 2026-06-19 | 2 | 2 | 0 | 2 | 2 | 0.000 | 2 | 2 |
| 2026-06-20 | 1 | 2 | 0 | 1 | 2 | 0.000 | 1 | 2 |
| 2026-06-21 | 1 | 0 | 0 | 1 | 0 | 0.000 | 1 | 0 |
| 2026-06-22 | 6 | 11 | 0 | 6 | 11 | 0.000 | 2 | 3 |
| 2026-06-23 | 2 | 2 | 1 | 1 | 1 | 0.333 | 2 | 2 |
| 2026-06-24 | 8 | 6 | 0 | 8 | 6 | 0.000 | 4 | 3 |
| 2026-06-25 | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 |
| 2026-06-26 | 20 | 3 | 0 | 20 | 3 | 0.000 | 10 | 1 |
| 2026-06-27 | 1 | 1 | 1 | 0 | 0 | 1.000 | 1 | 1 |

## Latest disagreements — 2026-06-27

## Kept only by v1 (candidate v2 misses) (0)

_none_

## Kept only by v2 (candidate v1 misses) (0)

_none_

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **71** / 40 target (178%)
- v1 right (v2 missed): **19** · v2 right (v1 let noise through): **52**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: v2 judges significantly better than v1** (p=0.0001 < 0.05, n=71).
- Volume guardrail: **ok** — v2 kept 0.659× v1's volume (27 vs 41 items over 9 days), within [0.5, 2.0].
