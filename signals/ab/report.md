# A/B report — v1 (production) vs v2 (freed judgment)

v1 encodes editorial judgment as prompt rules; v2 states the goal and
lets the model judge. Items unique to one arm are the disagreements —
review them to decide which policy filters better.

## History

| date | v1 items | v2 items | overlap | v1 only | v2 only | jaccard | v1 companies | v2 companies |
|------|----------|----------|---------|---------|---------|---------|--------------|--------------|
| 2026-06-19 | 2 | 2 | 0 | 2 | 2 | 0.000 | 2 | 2 |

## Latest disagreements — 2026-06-19

## Kept only by v1 (candidate v2 misses) (2)

### Hivebotics
- **Hivebotics' Abluo robot cuts toilet cleaning time by 50% with AI and robotic arms**
  https://app.dealroom.co/news/feed/hivebotics-abluo-robot-cuts-toilet-cleaning-time-by-50-with-ai-and-robotic-arms

### NEU Battery Materials
- **NEU Battery Materials at The Battery Show Europe 2026 in Stuttgart**
  https://www.neumaterials.com/news/neu-the-battery-show-europe-2026-in-stuttgart

## Kept only by v2 (candidate v1 misses) (2)

### Hivebotics
- **Hivebotics Expands Global Footprint with Robot Cleaning Trials**
  https://www.linkedin.com/pulse/hivebotics-expands-global-footprint-robot-cleaning-lfj2c

### Horizon Quantum Computing
- **Horizon Quantum Announces Dublin as Its Second Quantum Computer Testbed Location, Bringing A Frontier Quantum System to Ireland**
  https://www.horizonquantum.com/resources/newsroom/horizon-quantum-announces-dublin-as-its-second-quantum-computer-testbed-location-bringing-a-frontier-quantum-system-to-ireland

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **4** / 40 target (10%)
- v1 right (v2 missed): **1** · v2 right (v1 let noise through): **3**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: collecting** — need 36 more discordant pairs before reading the p-value (current p=0.6250, not yet powered).
- Volume guardrail: **ok** — v2 kept 1.0× v1's volume (2 vs 2 items over 1 days), within [0.5, 2.0].
