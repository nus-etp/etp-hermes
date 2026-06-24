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

## Latest disagreements — 2026-06-24

## Kept only by v1 (candidate v2 misses) (8)

### Duluin
- **Duluin named Startup of the Year at ASEAN Startup Innovation Weekend 2026, Phnom Penh**
  https://aseanstartupinnovationweekend.com/awards-2026

### Finan
- **SoBanHang targets SME digitisation with new funding**
  https://ibsintelligence.com/ibsi-news/sobanhang-targets-sme-digitisation-with-new-funding
- **Vietnamese merchant app SoBanHang (Finan) raises $3.8M Pre-Series A**
  https://www.backscoop.com/newsletter-posts/vietnamese-market-app-sobanhang-raises-fresh-funds
- **Tech in Asia: SoBanHang serves 800k merchants, raises funding for AI-powered SME finance tools**
  https://www.techinasia.com/news/sobanhang-funding-pre-series-a

### Flexxon
- **X-PHY SSD Named Finalist at the 2026 UK Cyber OSPAs**
  https://x-phy.com/x-phy-ssd-named-finalist-at-the-2026-uk-cyber-ospas
- **X-PHY Deepfake Detector Wins Global InfoSec Award for Innovative AI Security and Safety at RSAC 2026**
  https://x-phy.com/x-phy-deepfake-detector-wins-global-infosec-award-for-innovative-ai-security-and-safety-at-rsac-2026

### Horizon Quantum Computing
- **Horizon Quantum Announces First Quarter 2026 Financial Results**
  https://investors.horizonquantum.com/news-releases/news-release-details/horizon-quantum-announces-first-quarter-2026-financial-results
- **Horizon Quantum and AQT to Advance Real-World Quantum Applications with Strategic Hardware–Software Collaboration**
  https://www.horizonquantum.com/resources/newsroom/horizon-quantum-and-aqt-to-advance-real-world-quantum-applications-with-strategic-hardware-software-collaboration

## Kept only by v2 (candidate v1 misses) (6)

### BEEX
- **BeeX Launches BETTA, the Most Powerful Autonomous Underwater Drone**
  https://beex.sg/news/beex-launches-betta-the-most-powerful-autonomous-underwater-drone
- **BeeX, SIT launch test site for AI underwater inspection systems**
  https://www.theedgesingapore.com/digitaledge/technopreneurs/beex-sit-launch-test-site-ai-underwater-inspection-systems
- **BeeX Launches Live Underwater Drone Test-Zone with Opening of Autonomous Marine Foundry at SIT Punggol Campus**
  https://beex.sg/news/beex-opens-autonomous-marine-foundry-and-live-test-zone-at-sit-punggol

### Finan
- **Vietnamese fintech SoBanHang (Finan) raises further $3.8 million to expand micro-business management/embedded banking tools**
  https://fintechnews.sg/131334/vietnam/sobanhang-funding-pre-series-a

### Horizon Quantum Computing
- **Horizon Quantum Computing deploys Singapore's first quantum computer for commercial use**
  https://www.cnbc.com/2025/12/04/horizon-quantum-software-startup-deploys-singapores-first-quantum-computer-for-commercial-use.html
- **Horizon Quantum Announces Dublin as Its Second Quantum Computer Testbed Location**
  https://www.horizonquantum.com/resources/newsroom/horizon-quantum-announces-dublin-as-its-second-quantum-computer-testbed-location

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **48** / 40 target (120%)
- v1 right (v2 missed): **16** · v2 right (v1 let noise through): **32**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: v2 judges significantly better than v1** (p=0.0293 < 0.05, n=48).
- Volume guardrail: **ok** — v2 kept 1.15× v1's volume (23 vs 20 items over 6 days), within [0.5, 2.0].
