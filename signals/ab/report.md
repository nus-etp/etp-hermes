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

## Latest disagreements — 2026-06-23

## Kept only by v1 (candidate v2 misses) (1)

### Belli AI
- **Belli takes the stage at The Pitch by Deel**
  https://www.belli.ai/blog

## Kept only by v2 (candidate v1 misses) (1)

### Patsnap
- **Patsnap Said to Confidentially File for HK, Singapore Dual IPO**
  https://www.bloomberg.com/news/articles/2026-06-15/patsnap-said-to-confidentially-file-for-hk-singapore-dual-ipo

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **34** / 40 target (85%)
- v1 right (v2 missed): **15** · v2 right (v1 let noise through): **19**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: collecting** — need 6 more discordant pairs before reading the p-value (current p=0.6076, not yet powered).
- Volume guardrail: **ok** — v2 kept 1.417× v1's volume (17 vs 12 items over 5 days), within [0.5, 2.0].
