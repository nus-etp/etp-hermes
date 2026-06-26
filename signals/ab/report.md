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

## Latest disagreements — 2026-06-26

## Kept only by v1 (candidate v2 misses) (20)

### CBE Eco-Solutions
- **CBE Eco-Solutions Pte Ltd — Company profile**
  https://sgpgrid.com/company-details/cbe-ecosolutions-pte-ltd

### LittleLives
- **LittleLives — Preschool SaaS company profile**
  https://sg.linkedin.com/company/littlelives
- **LittleLives careers — Join the team**
  https://littlelives-talent.freshteam.com/jobs

### MADCash
- **MADCash — MAIA Awards profile**
  https://www.maiawards.org/MAIApedia/madcash-2
- **Micro Funding For Malaysian Women Entrepreneurs — About MADCash**
  https://getmadcash.com/about-us

### MangaChat
- **MangaChat — Youth wellness platform (LinkedIn)**
  https://sg.linkedin.com/company/mangachat
- **MangaChat — EMMET INSIGHT profile**
  https://emmetinsight.com/mangachat
- **MangaChat — Company website**
  https://sg.mangachat.com

### Marymount Labs
- **Marymount Labs — Turn Care Plans into Patient Action**
  https://www.marymountlabs.com

### NEU Battery Materials
- **NEU Battery Materials — NUS College of Design and Engineering partner profile**
  https://cde.nus.edu.sg/research/industry-partnership/neu-battery-materials

### Otrafy
- **Otrafy — Smart Supplier Management platform**
  https://www.otrafy.com
- **Otrafy — Mind Fund portfolio**
  https://www.mindfund.com/portfolio/otrafy

### Peris.ai
- **Peris.ai — Agentic-AI Cybersecurity Platform**
  https://www.peris.ai
- **Peris.ai — Crunchbase Company Profile**
  https://www.crunchbase.com/organization/peris-ai

### Pinhome
- **Indonesian startup founder bootstrapped a company out of her garage (CNBC)**
  https://www.cnbc.com/indonesia-startup-founder-bootstrapped-garage
- **Pinhome — Genesis Alternative Ventures portfolio**
  https://www.genesisventures.co/portfolio/pinhome
- **Pinhome — LinkedIn**
  https://www.linkedin.com/company/pinhome

### pQCee
- **pQCee — Be Quantum Ready (LinkedIn)**
  https://sg.linkedin.com/company/pqcee
- **How this quantum cybersecurity startup is enabling businesses to stay ahead (SGInnovate)**
  https://www.sginnovate.com/investments/pqcee
- **pQCee | PKI Consortium**
  https://pkic.org/about/membership/members/pqcee

## Kept only by v2 (candidate v1 misses) (3)

### LittleLives
- **You Deserve Better: No more juggling WhatsApp, emails, and paper forms**
  https://littlelives-blog.ghost.io/you-deserve-better-no-more-juggling-whatsapp-emails-and-paper-forms
- **Medication, allergies and 'Did the teacher remember?'**
  https://littlelives-blog.ghost.io/medication-allergies-and-did-the-teacher-remember
- **From paper forms to push notifications**
  https://littlelives-blog.ghost.io/from-paper-forms-to-push-notifications

## Significance (McNemar, blind-judge labels)

- Discordant pairs labeled: **71** / 40 target (178%)
- v1 right (v2 missed): **19** · v2 right (v1 let noise through): **52**
- Unlabeled disagreements awaiting judge: 0
- **Verdict: v2 judges significantly better than v1** (p=0.0001 < 0.05, n=71).
- Volume guardrail: **ok** — v2 kept 0.65× v1's volume (26 vs 40 items over 8 days), within [0.5, 2.0].
