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

## Latest disagreements — 2026-06-21

## Kept only by v1 (candidate v2 misses) (1)

### polybee
- **How Polybee found growth, innovation and collaboration in Australia**
  https://international.austrade.gov.au/en/news-and-analysis/success-stories/how-Polybee-found-growth-innovation-and-collaboration-in-Australia

## Kept only by v2 (candidate v1 misses) (0)

_none_

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **15** / 40 target (38%)
- v1 right (v2 missed): **3** · v2 right (v1 let noise through): **12**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: collecting** — need 25 more discordant pairs before reading the p-value (current p=0.0352, not yet powered).
- Volume guardrail: **ok** — v2 kept 1.0× v1's volume (4 vs 4 items over 3 days), within [0.5, 2.0].
