You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 1 of 3** in the daily pipeline:
1. **Data ingestion (this prompt)** — deterministic feeds → `signals/updates/<date>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — dynamic web/browser search to fill gaps → `signals/agent/<date>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 1: only write under `signals/updates/` and append to `signals/seen-urls.txt`. Do not touch `signals/agent/` or `signals/briefs/`.

## Task

Produce a daily company-news digest for the `data/companies.json` watchlist from every feed in `data/feeds.json`, written to `signals/updates/<UTC-date>.md`. Apply an LLM relevance pass to drop false-positives. Dedupe against `signals/seen-urls.txt`.

## Inputs

- `data/companies.json` — watchlist. Array of objects:
  - `name` (string, required) — canonical name, used as the output label.
  - `aliases` (array, default `[]`) — additional names. Used with `name` as case-insensitive substring patterns for the Step 2 triage.
  - `description` (string, required) — used in the relevance pass to judge whether a candidate item is actually about this company. May call out same-name entities to exclude.
  - `sources` (array, default `[]`) — per-company curated feeds (see below). Fetched in Step 2.5; bypass Step 2 triage.
  - `identifiers` (object, optional) — carry-only metadata (LinkedIn, Crunchbase, UEN). Do not fetch.

  Call the array `C[]`. For each company `c`, define `c.terms = [c.name, ...c.aliases]`.

- `data/feeds.json` — array of feed definitions. Each has `name`, `type` (always `firehose` in the current config), and `url`. Feeds are standard RSS/Atom.

- `data/changed-sources.json` — change whitelist from `scripts/preflight-feeds.py`. Schema:
  ```json
  {
    "generated_at": "<ISO8601 UTC>",
    "firehose":  ["<feed-url>", ...],
    "per_company": { "<company-name>": ["<source-url>", ...], ... }
  }
  ```
  **Only fetch URLs listed here.** URLs not listed = unchanged; skip entirely. `html_scrape` sources are always listed. If the file is missing, fetch everything (cold-start).

- `signals/seen-urls.txt` — newline-delimited dedup keys. Hold as `SEEN`. Article URLs for firehose; synthetic keys for per-company sources (`lever://patsnap/<posting_id>`, `github-atom://<entry_id>`, or absolute URL for scrapes — see Step 2.5).

## Per-company source types

Each entry in `c.sources` has `type`, `label` (short string used in output's `source` field), and `url`. Type-specific behavior:

- `rss` — standard RSS/Atom feed. Dedup key = item `<link>`.
- `github_org` — GitHub org Atom feed (`https://github.com/<org>.atom`). Dedup key = entry `<id>`. Keep only human-meaningful events: `ReleaseEvent`, `CreateEvent` with `ref_type=repository`, and `PushEvent` whose head commit message is not a bot signature (skip Dependabot, renovate-bot, `[bot]` accounts) and not a trivial chore (`Bump`, `Update README`, version tag pushes alone). Synthesize the item's headline as `<repo>: <event summary>` (e.g. `<repo>: new release v1.2.3`, `<repo>: <commit subject>`); set `link` to the entry URL.
- `lever_jobs` — Lever JSON endpoint (`https://api.lever.co/v0/postings/<slug>?mode=json`). Dedup key = `lever://<slug>/<posting.id>`. For each posting whose `createdAt` is within the last 30 days and whose synthetic key is not in `SEEN`, synthesize an item: headline `<title> — <team>`, link `<hostedUrl>`, pubDate from `createdAt`.
- `html_scrape` — plain `GET` of an HTML page. The source entry has an additional `hint` field describing the page structure. **Before fetching**, check `data/jina-items.json` (if it exists): if `per_company[<c.name>][<s.url>]` is present, use those items as-is — each is already `{headline, link, source_kind: "html_scrape", pre_extracted: true}`, optionally with `pubDate` and `label`. Skip the fetch and parse entirely. Only fetch and parse the HTML yourself if `s.url` appears in `extraction_failed`/`deferred`, or `data/jina-items.json` is missing. When you do fetch: parse the HTML and extract a list of items (title + absolute link + optional date) by reasoning about the structure described in `hint`. Dedup key = absolute item URL. Resolve any relative links to absolute against the source URL's origin.

If a single per-company source fetch fails, log and continue — do not fail the whole run.

## Steps

1. Read `data/companies.json`, `data/feeds.json`, `data/changed-sources.json` (if present), `signals/seen-urls.txt`.

2. **Collect firehose candidates.** Let `CHANGED_FIREHOSE` be the set of URLs in `data/changed-sources.json`'s `firehose` array (or, if the file is missing, every `F.url` in `data/feeds.json`). If `CHANGED_FIREHOSE` is empty, skip this whole step. Otherwise, for each feed `F` in `data/feeds.json` whose `F.url` is in `CHANGED_FIREHOSE`:
   - Fetch `F.url`. Parse the RSS/Atom feed.
   - For each `<item>` whose `<pubDate>` (or `<published>`) is within the last 7 days AND whose `<link>` is NOT in `SEEN`:
     - Build `haystack = lower(<title> + " " + <description>)`.
     - For each company `c` in `C`: if any `t` in `c.terms` (lowercased) is a substring of `haystack`, append a candidate `(c, item)` — where `item = {headline=<title>, description=<description>, source=F.name, pubDate, link, source_kind="firehose"}`. One item can produce multiple candidates if multiple companies match.

2.5. **Collect per-company candidates.** Let `CHANGED_PER_COMPANY` be the `per_company` object in `data/changed-sources.json` (or, if the file is missing, treat every per-company source as changed). If `CHANGED_PER_COMPANY` is empty, skip this whole step. Otherwise, for each company `c` in `C` whose `c.name` is a key in `CHANGED_PER_COMPANY`:
   - For each source `s` in `c.sources` whose `s.url` is in `CHANGED_PER_COMPANY[c.name]`:
     - Fetch and parse `s.url` per its `type` (see "Per-company source types" above). For each extracted item whose dedup key is NOT in `SEEN` and whose date (if known) is within the last 14 days:
       - Append a candidate `(c, item)` where `item = {headline, description, source = "<c.name> · <s.label>", pubDate, link, source_kind = s.type}`.
       - **Skip the substring triage** — per-company sources are already company-scoped. The candidate is bound to `c` regardless of headline content.
   - If `s` is an `html_scrape` and you cannot reliably extract a structured item list from the page (e.g. JS-only render, page structure changed), log and continue — do not fabricate items.

3. **Relevance pass.** For each candidate `(c, item)`, judge: *is this article clearly and primarily about `c` per `c.description`?* Use only `headline`, `description`, `source` — do not fetch the URL. Default to `drop` when uncertain.

   **Exception**: candidates flagged `pre_extracted: true` (sourced from `data/jina-items.json`) skip the relevance judgment entirely — accept as kept. They came from a curated per-company html_scrape page whose markdown was already cleaned by Jina Reader, and per-company sources are already keep-biased. They still need to pass the deduplication and bot/chore filters below.

   Drop when:
   - Substring coincidence on a generic word ("emerge", "alpha", "seamless", "carousel", "horizon", "nova") where the item isn't about the watchlisted company.
   - Different entity with the same/similar name (band, different region, different industry, public ticker colliding with a private SG company). `description` often calls these out.
   - Ticker-aggregator content (Zacks, TipRanks, MarketBeat, MEXC, StockInvest, TradingView, Stock Titan, AlphaStreet, geneonline, CryptoRank, Investing.com, Yahoo Finance) on same-name public tickers.
   - Mentioned only in passing (one-word in a list, not the subject).
   - Generic SEO/listicle, stock-price/earnings restatement.

   Keep when headline+source make clear the article is genuinely about the company — funding, launches, hiring, partnerships, regulatory news, founder interviews, etc.

   For `source_kind != "firehose"`, shift bias toward keeping (already curated). But still drop:
   - Duplicate of an item already kept this run from another source.
   - Bot/chore GitHub events that slipped past Step 2.5 (Dependabot, README typos, version-tag-only pushes).
   - Lever job postings that are evergreen reqs (generic title, no team, low information).
   - arXiv revisions (`[v2]`, `[v3]` markers).

   Default bias for firehose: drop. False negatives recover next run; false positives are noise.

4. **Write outputs.** Let `K[]` be the candidates that passed the relevance pass.
   - If `K[]` is empty: write nothing. Final stdout: `no new items`.
   - Otherwise:
     - Append the dedup key (item `link` for firehose / rss / html_scrape; synthetic key for github_org / lever_jobs as defined in "Per-company source types") of every item in `K[]` to `signals/seen-urls.txt`. Dedupe across multiple matches per item. Append only — do not rewrite.
     - Also append the dedup keys of any candidates that were *dropped* in step 3 to `signals/seen-urls.txt`. We don't want to spend LLM budget re-judging the same junk every day.
     - Write `signals/updates/<YYYY-MM-DD>.md` (UTC date) containing the kept items, grouped by company (use `c.name` as the heading). Format per item:
       ```
       - **<headline>** — <source> · <pubDate>
         <link>
       ```
       If the file already exists (re-run same day), append a new `## Run at <UTC time>` section instead of overwriting.
   - Final stdout: a single line of the form `<N> new items across <M> companies from <K> feeds (<D> dropped by relevance)`. Nothing else.

## Constraints

- Do not modify any file outside `signals/updates/` and `signals/seen-urls.txt`.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single feed fetch fails, log and continue. Don't fail the whole run on one bad request.
- Do not fetch article URLs to enrich the relevance judgment; the headline + description + source is what you have.
