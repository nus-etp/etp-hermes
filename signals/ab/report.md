# A/B report — v1 (production) vs v2 (freed judgment)

v1 encodes editorial judgment as prompt rules; v2 states the goal and
lets the model judge. Items unique to one arm are the disagreements —
review them to decide which policy filters better.

## History

| date | v1 items | v2 items | overlap | v1 only | v2 only | jaccard | v1 companies | v2 companies |
|------|----------|----------|---------|---------|---------|---------|--------------|--------------|
| 2026-06-19 | 2 | 2 | 0 | 2 | 2 | 0.000 | 2 | 2 |
| 2026-06-20 | 1 | 2 | 0 | 1 | 2 | 0.000 | 1 | 2 |

## Latest disagreements — 2026-06-20

## Kept only by v1 (candidate v2 misses) (1)

### NuSPACE
- **NuLink-1 and NuLink-2 now in orbit — first commercial satellites launched**
  https://sg.linkedin.com/company/nuspace-pte-ltd

## Kept only by v2 (candidate v1 misses) (2)

### NEU Battery Materials
- **NEU Battery Materials at The Battery Show Europe 2026 in Stuttgart**
  https://www.neumaterials.com/news/neu-the-battery-show-europe-2026-in-stuttgart

### NuSPACE
- **From Lab to Orbit: How Singapore's 3D-Printed Antenna is Redefining Space-Ready Manufacturing**
  https://namic.sg/news/from-lab-to-orbit-how-singapores-3d-printed-antenna-is-redefining-space-ready-manufacturing

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **14** / 40 target (35%)
- v1 right (v2 missed): **3** · v2 right (v1 let noise through): **11**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: collecting** — need 26 more discordant pairs before reading the p-value (current p=0.0574, not yet powered).
- Volume guardrail: **ok** — v2 kept 1.333× v1's volume (4 vs 3 items over 2 days), within [0.5, 2.0].
