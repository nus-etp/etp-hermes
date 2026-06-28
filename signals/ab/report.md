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
| 2026-06-28 | 2 | 6 | 2 | 0 | 4 | 0.333 | 1 | 2 |

## Latest disagreements — 2026-06-28

## Kept only by v1 (candidate v2 misses) (0)

_none_

## Kept only by v2 (candidate v1 misses) (4)

### Carousell
- **Carousell Autos launches Singapore's first AI-powered car finder**
  https://press.carousell.com/2026/06/18/carousell-autos-launches-singapores-first-ai-powered-car-finder

### Patsnap
- **Patsnap Launches Inaugural Life Sciences Customer Advisory Board**
  https://www.patsnap.com/resources/blog/press_release/patsnap-launches-inaugural-life-sciences-customer-advisory-board
- **Patsnap Expands Hiro AI Conversational Search**
  https://www.patsnap.com/resources/blog/press_release/patsnap-expands-hiro-ai-conversational-search-2
- **Patsnap Expands Hiro AI Conversational Search**
  https://www.patsnap.com/resources/blog/press_release/patsnap-expands-hiro-ai-conversational-search

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **75** / 40 target (188%)
- v1 right (v2 missed): **22** · v2 right (v1 let noise through): **53**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: v2 judges significantly better than v1** (p=0.0004 < 0.05, n=75).
- Volume guardrail: **ok** — v2 kept 0.767× v1's volume (33 vs 43 items over 10 days), within [0.5, 2.0].
