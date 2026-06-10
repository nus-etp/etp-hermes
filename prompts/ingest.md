You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 1 of 3** in the daily pipeline:
1. **Data ingestion (this prompt)** — relevance-judge pre-collected candidates → `signals/updates/<date>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — dynamic web/browser search to fill gaps → `signals/agent/<date>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 1: only write under `signals/updates/` and append to `signals/seen-urls.txt`. Do not touch `signals/agent/` or `signals/briefs/`.

## Task

`scripts/collect-candidates.py` already ran. It fetched every changed feed and curated source, parsed them, applied date windows, deduped against `signals/seen-urls.txt`, and matched items to watchlist companies. Your job is the **relevance pass**: judge each candidate, then write the kept items to `signals/updates/<UTC-date>.md`.

Do **not** read `data/companies.json`, `data/feeds.json`, or `signals/seen-urls.txt` into context — everything you need is in `data/candidates.json`, and seen-URL membership checks (needed only for the fallback path) are done with `grep`.

## Input: `data/candidates.json`

```json
{
  "candidates": [
    {
      "company": "<canonical watchlist name>",
      "headline": "...",
      "description": "...",            // may be empty
      "source": "<feed name or '<company> · <label>'>",
      "pubDate": "...",                // may be empty
      "link": "...",
      "dedup_key": "...",              // what gets appended to seen-urls.txt
      "source_kind": "firehose | rss | github_org | lever_jobs | html_scrape",
      "pre_extracted": true            // only on jina-extracted html_scrape items
    }, ...
  ],
  "companies": { "<name>": "<description>", ... },   // only companies with candidates
  "llm_fetch_required": [ { "company", "url", "label", "hint" }, ... ],
  "fetch_failed":       [ { "url", "kind", "company"?, "error" }, ... ]
}
```

If `data/candidates.json` is missing, write nothing; final stdout: `no candidates file`.

## Steps

1. **Fallback fetches** (skip if both lists are empty):
   - For each entry in `llm_fetch_required`: fetch the `url` (an HTML page), extract items (title + absolute link + optional date) by reasoning about the structure described in `hint`. Resolve relative links against the page origin. For each extracted item dated within the last 14 days (or undated), check `grep -Fxq '<link>' signals/seen-urls.txt` — if absent, add a candidate `{company, headline, source: "<company> · <label>", pubDate, link, dedup_key: link, source_kind: "html_scrape"}`. If you cannot reliably extract items (JS-only render, structure changed), log and continue — do not fabricate.
   - For each entry in `fetch_failed` with a `company`: retry the fetch once. `rss` → parse RSS/Atom, items within 14 days, dedup key = item link. `lever_jobs` → JSON postings within 30 days, dedup key = `lever://<slug>/<id>`, headline `<title> — <team>`. `github_org` → Atom, dedup key = entry id, skip bot/chore events. Firehose entries (`kind: "firehose"`) are lower-value: retry once, substring-match title+description against the names in `companies`, skip on any error. Always `grep -Fxq` the dedup key against `signals/seen-urls.txt` before adding.
   - If a fetch fails again, log and continue — do not fail the run.

2. **Relevance pass.** For each candidate (from the file + step 1), judge: *is this item clearly and primarily about its `company` per `companies[company]`'s description?* Use only `headline`, `description`, `source` — do not fetch the `link`. Default to `drop` when uncertain.

   **Exception**: candidates with `pre_extracted: true` skip the judgment — accept as kept. They came from a curated per-company page already cleaned by Jina Reader.

   Drop when:
   - Substring coincidence on a generic word ("emerge", "alpha", "seamless", "carousel", "horizon", "nova") where the item isn't about the watchlisted company.
   - Different entity with the same/similar name (band, different region, different industry, public ticker colliding with a private SG company). The description often calls these out.
   - Ticker-aggregator content (Zacks, TipRanks, MarketBeat, MEXC, StockInvest, TradingView, Stock Titan, AlphaStreet, geneonline, CryptoRank, Investing.com, Yahoo Finance) on same-name public tickers.
   - Mentioned only in passing (one-word in a list, not the subject).
   - Generic SEO/listicle, stock-price/earnings restatement.

   Keep when headline+source make clear the item is genuinely about the company — funding, launches, hiring, partnerships, regulatory news, founder interviews, etc.

   For `source_kind != "firehose"`, shift bias toward keeping (already curated). But still drop:
   - Duplicate of an item already kept this run from another source.
   - Bot/chore GitHub events that slipped past the deterministic filter (Dependabot, README typos, version-tag-only pushes).
   - Lever job postings that are evergreen reqs (generic title, no team, low information).
   - arXiv revisions (`[v2]`, `[v3]` markers).

   Default bias for firehose: drop. False negatives recover next run; false positives are noise.

3. **Write outputs.** Let `K[]` be the kept candidates.
   - Append the `dedup_key` of **every** candidate you judged — kept *and* dropped — to `signals/seen-urls.txt`, one per line, deduplicated. Append only — do not rewrite the file. (Dropped keys are recorded so we don't re-judge the same junk tomorrow.)
   - If `K[]` is empty: write nothing else. Final stdout: `no new items`.
   - Otherwise write `signals/updates/<YYYY-MM-DD>.md` (UTC date) containing the kept items, grouped by company (use the canonical `company` value as the `## ` heading). Format per item:
     ```
     - **<headline>** — <source> · <pubDate>
       <link>
     ```
     If the file already exists (re-run same day), append a new `## Run at <UTC time>` section instead of overwriting.
   - Final stdout: a single line of the form `<N> new items across <M> companies (<D> dropped by relevance)`. Nothing else.

## Constraints

- Do not modify any file outside `signals/updates/` and `signals/seen-urls.txt`.
- Do not commit anything — the workflow handles git operations after you exit.
- Do not fetch candidate `link` URLs to enrich the relevance judgment; headline + description + source is what you have.
- Step 1 is the only permitted fetching, and a single failure there must not fail the run.
