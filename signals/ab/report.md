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

## Latest disagreements — 2026-06-22

## Kept only by v1 (candidate v2 misses) (6)

### Addlly AI
- **Addlly AI Named SuperAI Genesis Top 50 Startup, Strengthening Its Position as a Trusted Enterprise GEO Platform**
  https://www.freep.com/press-release/story/198143/addlly-ai-named-superai-genesis-top-50-startup-strengthening-its-position-as-a-trusted-enterprise-geo-platform

### BEEX
- **BeeX, SIT launch test site for AI underwater inspection systems**
  https://www.theedgesingapore.com/digitaledge/technopreneurs/beex-sit-launch-test-site-ai-underwater-inspection-systems
- **BeeX Launches Live Underwater Drone Test-Zone with Opening of Autonomous Marine Foundry at SIT Punggol Campus**
  https://www.beex.sg/news/beex-opens-autonomous-marine-foundry-and-live-test-zone-at-sit-punggol
- **Successful Adaptive Autonomous Inspection at 4-Legged Jacket Platform for Major O&G Company**
  https://www.beex.sg/case-studies/successful-adaptive-autonomous-inspection-at-4-legged-jacket-platform-for-major-o-g-company
- **Enhancing Inspection Efficiency in Marine Construction with Autonomous Solutions**
  https://www.beex.sg/case-studies/meet-a-ikanbilis-the-autonomous-underwater-drone-transforming-how-moorings-are-installed
- **BeeX Provides Integrated Inspection Approach for Offshore Industry Giant**
  https://www.beex.sg/case-studies/beex-provides-integrated-inspection-approach-for-offshore-industry-giant

## Kept only by v2 (candidate v1 misses) (11)

### Agrolitik
- **Agrolitik named finalist at Hult Prize Singapore Nationals 2026**
  https://www.facebook.com/NUSEnterprise/posts/the-hult-prize-singapore-nationals-2026-has-wrapped-and-what-a-day-it-wascongrat/1362229692603059

### BEEX
- **Levels Of Autonomy In Unmanned Underwater Vehicles: Use Cases, Limitations, And A.IKANBILIS' Integrated Autonomy**
  https://beex.sg/blogs/levels-of-autonomy-in-unmanned-underwater-vehicles-use-cases-limitations-and-a-ikanbilis-integrated-autonomy
- **BeeX, SIT launch test site for AI underwater inspection systems**
  https://sg.finance.yahoo.com/news/beex-sit-launch-test-ai-003417097.html

### RO+
- **TechInnovation 2023**
  https://www.roplus.sg/2023/11/05/techinnovation-2023
- **ITAP 2023**
  https://www.roplus.sg/2023/11/05/itap-2023
- **Computer Vision – Cereal Box Picking**
  https://www.roplus.sg/2023/11/05/computer-vision-cereal-box-picking
- **Computer Vision – Automated Fryer Station**
  https://www.roplus.sg/2023/11/05/computer-vision-automated-fryer-station
- **NRP's Technology Advisory Panel (TAP) Visit**
  https://www.roplus.sg/2023/11/05/nrps-technology-advisory-panel-tap-visit
- **Vacuum Gripper – Pouched Products Picking**
  https://www.roplus.sg/2023/07/03/vacuum-gripper-pouched-products-picking
- **ATXSG InnovFest 2023**
  https://www.roplus.sg/2023/07/03/atxsg-innovfest-2023
- **Seminar 2023 – From Research to Commercialization with Partners – Robotics and AI**
  https://www.roplus.sg/2023/07/03/fha-food-and-beverage-2023

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **32** / 40 target (80%)
- v1 right (v2 missed): **15** · v2 right (v1 let noise through): **17**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: collecting** — need 8 more discordant pairs before reading the p-value (current p=0.8600, not yet powered).
- Volume guardrail: **ok** — v2 kept 1.5× v1's volume (15 vs 10 items over 4 days), within [0.5, 2.0].
