# FEEDS.md — free information sources ranked by ROI

Candidate free sources for `data/feeds.json` (firehose) or per-company `sources` entries. Scope: feeds that drop into the existing handlers in `prompts/ingest.md` (`firehose` / `sitemap_feed` / `rss` / `html_scrape` / `github_org` / `lever_jobs`). Sources that need a new handler are listed at the bottom and excluded from the tiers.

The watchlist is dominated by NUS GRIP deep-tech spinouts (quantum, biotech, climate, AI, hardware), and 124 of 157 companies have no dedicated source today — they survive on substring matches against the 6 generalist firehoses. This list targets three gaps: **Singapore government / regulatory**, **academic preprints**, and **additional regional outlets**.

Every URL below was probed from a local network with a generic User-Agent on **2026-05-21**. Status is recorded honestly: `OK` = served real feed/HTML, `CF` = blocked by Cloudflare bot challenge (may pass from the GHA runner — needs a real-world test), `404`/`500` = endpoint dead.

---

## Active via `sitemap_feed` handler (already in `data/feeds.json`)

The repo now ships a `sitemap_feed` source type. Headline derived from the article `<title>` when reachable; falls back to slug-derived headline when the article URL returns a WAF challenge (both sites below sit behind Imperva for HTML pages, though their sitemaps pass through cleanly).

| Source | Sitemap URL | URL prefix | Signal added |
|---|---|---|---|
| NUS Enterprise | `https://enterprise.nus.edu.sg/post-sitemap.xml` | `https://enterprise.nus.edu.sg/news/` | Direct upstream of the GRIP cohort — funding rounds, cohort news |
| EDB Insights | `https://www.edb.gov.sg/en.sitemap.xml` | `https://www.edb.gov.sg/en/business-insights/insights/` | Government / inbound-investment commentary; ~1,395 timestamped articles |

---

## Tier S — verified, drop-in firehose RSS

| Source | URL | Handler | Signal added | Status |
|---|---|---|---|---|
| Crunchbase News | `https://news.crunchbase.com/feed/` | firehose | Funding rounds outside SEA — covers global rounds that Tech in Asia / Vulcan miss | OK (`application/rss+xml`) |
| arXiv quant-ph | `http://export.arxiv.org/rss/quant-ph` | firehose | Quantum preprints — Horizon Quantum, SpeQtral, Atomionics, Anyon etc. | OK |
| arXiv q-bio | `https://arxiv.org/rss/q-bio` | firehose | Quantitative biology preprints — biotech GRIP cohort | OK |
| arXiv cs.AI | `http://export.arxiv.org/rss/cs.AI` | firehose | AI/ML preprints — Patsnap, AI cohort, founder-authored papers | OK (very high volume — ~1MB/day, strong relevance pass mandatory) |

**ROI rationale:** All four return valid RSS today. arXiv categories are noisy at the item level but excellent for catching founder-published research before press coverage. Crunchbase News is the cleanest funding-round signal currently absent from the feed mix.

---

## Tier A — likely valuable, **needs real-world verification on the GHA runner**

These returned a Cloudflare or anti-bot challenge from a local probe. They may serve fine from a GitHub Actions runner (different IP reputation, the runner often passes JS-less CF challenges); they may also be permanently blocked. Test before adding.

| Source | URL | Handler | Signal added | Status |
|---|---|---|---|---|
| e27 | `https://e27.co/feed/` | firehose | Pan-SEA startup news — twin to Tech in Asia | CF 403 |
| KrAsia | `https://kr-asia.com/feed` | firehose | Broader Asia coverage (China/HK/Korea) — cross-border deals | CF/HTML (no RSS body) |
| The Edge Singapore | `https://www.theedgesingapore.com/rss` | firehose | SG corporate/finance — catches deals BT misses | CF 403 |
| bioRxiv (bioengineering) | `https://www.biorxiv.org/biorxiv_xml.php?subject=bioengineering` | firehose | Life-science preprints from founder-authors | CF 403 |
| ChemRxiv | `https://chemrxiv.org/engage/api-gateway/chemrxiv/public/rss` | firehose | Chemistry/materials preprints — battery / materials cohort | CF 403 |
| DealStreetAsia | `https://www.dealstreetasia.com/feed` | firehose | SEA private-market deal headlines (long-form paywalled, headlines free) | 503 — "Temporarily Disabled to mitigate bot attacks" |

**Verification step before adding any of these:** open a throwaway branch, add the feed to `data/feeds.json`, push, and inspect the next workflow run's logs to confirm a real RSS body comes back. If 403, drop it.

---

## Tier B — html_scrape, verified reachable

Singapore government / institutional pages with no public RSS. All returned a real HTML body of meaningful size (not a 200-byte JS shell). Each needs a per-page `hint` string for the `html_scrape` handler.

| Source | URL | Handler | Signal added | Status |
|---|---|---|---|---|
| MAS news index | `https://www.mas.gov.sg/news` | html_scrape (firehose role) | Fintech licensing, sandbox grads, regulatory grants — direct hit for fintech GRIP cohort. MAS has **no public RSS endpoint** (the often-cited `/news/rss` returns the page HTML, not a feed). | OK, 246 KB |
| IMDA media releases | `https://www.imda.gov.sg/news-and-events/media-room/media-releases` | html_scrape | Digital/AI grants, sandbox, infocomm policy | OK, 95 KB |
| HSA announcements | `https://www.hsa.gov.sg/announcements` | html_scrape | Medtech / pharma approvals — biotech GRIP cohort | OK, 152 KB |
| NRF Singapore news | `https://www.nrf.gov.sg/news` | html_scrape | Grant program announcements (slow cadence) | OK, 27 KB |
| A*STAR News | `https://www.a-star.edu.sg/News-and-Events/news` | html_scrape | Lab spinouts, A*STAR-cohort partnerships | OK, 7 KB — small page, may be JS-augmented; verify the `hint` extracts items |

---

## Tier C — niche / situational

| Source | URL | Handler | Signal added | Status |
|---|---|---|---|---|
| SGX company announcements | `https://www.sgx.com/securities/company-announcements` | html_scrape | Only relevant once a GRIP company IPOs | OK 15 KB — likely a JS app shell; may not be reliably scrapable |
| arXiv cs.LG | `http://export.arxiv.org/rss/cs.LG` | firehose | ML preprints — very high volume, low per-company hit rate | OK |

---

## Excluded — needs new handler or no accessible endpoint

Listed here so the next pass doesn't re-research them. **Do not add to `feeds.json` as-is.** Updated 2026-05-21 after URL-hunt round.

### No accessible endpoint — give up

- **Enterprise Singapore newsroom** — fully Sitecore SPA. Tried `/about-us/newsroom`, `/about-us/newsroom/news`, `/about-us/newsroom/media-releases`, `/our-stories`, `/about-us/news-and-events`, `/sitemap_index.xml`, `/sitemap-news.xml` — **all return the 39 KB SPA 404 shell** (server returns HTTP 404 but with the homepage HTML). `robots.txt` advertises `/sitemap.xml` but that URL serves the homepage SPA, not a sitemap. The site's listing pages render via `api.search.gov.sg`, which rejects off-origin requests with `403 Forbidden`. No path forward without browser automation. **Stop hunting.**

### Excluded because they need a different new handler

- **SEC EDGAR** — useful only if a watchlisted company lists on US markets; currently zero. JSON+RSS available but needs a new handler. Park until first US listing.
- **Greenhouse jobs API** — many SG startups use Greenhouse (mirror of Lever). Worth a follow-up: build a `greenhouse_jobs` handler that mirrors `lever_jobs` exactly.
- **USPTO PatentsView / Google Patents** — patent activity is a real signal but needs an assignee-search handler.
- **LinkedIn / Twitter (X)** — no stable free RSS; scraping is brittle and ToS-risky.

---

## ROI scoring axes used above

- **Signal density** — expected kept items per 100 fetched. arXiv categories score low here (heavy relevance-pass drops); MAS / HSA / NRF score high (every item is real news, just filtered to watchlist mentions).
- **Gap fill** — does it cover a category currently absent from `feeds.json`? Regulatory feeds and academic preprints are pure gap-fill; another generalist news outlet is not.
- **Noise risk** — how aggressive the relevance pass must be. arXiv cs.AI / cs.LG is the worst offender (~1 MB of unrelated CS daily). Tier S already accounts for this.
- **Handler fit** — drop-in (`firehose`) > known-pattern (`html_scrape` + hint) > needs new handler. Anything in the last bucket is out of scope and listed under *Excluded*.

## How to use this file

1. Pick a source from Tier S → add directly to `data/feeds.json` as a `firehose` entry. No code change, no new handler.
2. Pick a source from Tier A → add to a throwaway branch first, watch the next GHA run to confirm the response is real RSS. If 403, drop it.
3. Pick a source from Tier B → add to `data/feeds.json` with `"type": "html_scrape"` and a hand-written `hint` describing the page's article-list structure (see `prompts/ingest.md` lines 41–48 for the contract).
4. Re-verify URLs before adding — endpoints in the Singapore government estate move without redirects.
