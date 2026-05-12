You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 1 of 3** in the daily pipeline:
1. **Data ingestion (this prompt)** — deterministic feeds → `signals/updates/<date>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — dynamic web/browser search to fill gaps → `signals/agent/<date>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 1: only write under `signals/updates/` and append to `signals/seen-urls.txt`. Do not touch `signals/agent/` or `signals/briefs/`.

## Task

Produce a daily company-news digest for the watchlist in `data/companies.json`, drawing from every feed in `data/feeds.json`, written to `signals/updates/<UTC-date>.md`. Apply an LLM relevance pass — your own judgment — to drop false-positive matches before writing. Deduplicate against `signals/seen-urls.txt` so the same article never appears twice across runs.

## Inputs

- `data/companies.json` — watchlist. Array of objects:
  - `name` (string, required) — canonical name, used as the company label in output.
  - `aliases` (array of strings, optional, default `[]`) — additional names. Both `name` and every alias are used as case-insensitive substring patterns for the initial triage match against item title/description.
  - `description` (string, required) — one- or two-sentence description of the company. This is what you use during the relevance pass to judge whether a candidate item is actually about this company. It may explicitly call out unrelated entities to exclude (other companies with the same name, ticker collisions, etc.).
  - `sources` (array of source objects, optional, default `[]`) — per-company curated feeds. See "Per-company source types" below. When present, these are fetched in Step 2.5 and bypass the substring triage in Step 2.
  - `identifiers` (object, optional) — carry-only metadata (LinkedIn URL, Crunchbase URL, ACRA UEN, etc.). Do not fetch or otherwise act on these in this pipeline; they exist for future use.

  Call the array `C[]`. For each company `c`, define `c.terms = [c.name, ...c.aliases]`.

- `data/feeds.json` — array of feed definitions. Each has `name`, `type` (always `firehose` in the current config), and `url`. Feeds are standard RSS/Atom.

- `signals/seen-urls.txt` — newline-delimited dedup keys already reported. Hold as a set `SEEN`. Entries are typically article URLs from firehose feeds; per-company sources may add synthetic keys (e.g. `lever://patsnap/<posting_id>`, `github-atom://<entry_id>`, or the scraped item's absolute URL) — see Step 2.5. Layer 2 (agent supplement) reads and appends to the same file, so anything you write here is also off-limits for the agent's web searches.

## Per-company source types

Each entry in `c.sources` has `type`, `label` (short string used in output's `source` field), and `url`. Type-specific behavior:

- `rss` — standard RSS/Atom feed. Dedup key = item `<link>`.
- `github_org` — GitHub org Atom feed (`https://github.com/<org>.atom`). Dedup key = entry `<id>`. Keep only human-meaningful events: `ReleaseEvent`, `CreateEvent` with `ref_type=repository`, and `PushEvent` whose head commit message is not a bot signature (skip Dependabot, renovate-bot, `[bot]` accounts) and not a trivial chore (`Bump`, `Update README`, version tag pushes alone). Synthesize the item's headline as `<repo>: <event summary>` (e.g. `<repo>: new release v1.2.3`, `<repo>: <commit subject>`); set `link` to the entry URL.
- `lever_jobs` — Lever JSON endpoint (`https://api.lever.co/v0/postings/<slug>?mode=json`). Dedup key = `lever://<slug>/<posting.id>`. For each posting whose `createdAt` is within the last 30 days and whose synthetic key is not in `SEEN`, synthesize an item: headline `<title> — <team>`, link `<hostedUrl>`, pubDate from `createdAt`.
- `html_scrape` — plain `GET` of an HTML page. The source entry has an additional `hint` field describing the page structure. Fetch the page, parse the HTML, and extract a list of items (title + absolute link + optional date) by reasoning about the structure described in `hint`. Dedup key = absolute item URL. Resolve any relative links to absolute against the source URL's origin.

If a single per-company source fetch fails, log and continue — do not fail the whole run.

## Steps

1. Read `data/companies.json`, `data/feeds.json`, `signals/seen-urls.txt`.

2. **Collect firehose candidates.** For each feed `F` in `data/feeds.json`:
   - Fetch `F.url`. Parse the RSS/Atom feed.
   - For each `<item>` whose `<pubDate>` (or `<published>`) is within the last 7 days AND whose `<link>` is NOT in `SEEN`:
     - Build `haystack = lower(<title> + " " + <description>)`.
     - For each company `c` in `C`: if any `t` in `c.terms` (lowercased) is a substring of `haystack`, append a candidate `(c, item)` — where `item = {headline=<title>, description=<description>, source=F.name, pubDate, link, source_kind="firehose"}`. One item can produce multiple candidates if multiple companies match.

2.5. **Collect per-company candidates.** For each company `c` in `C` with a non-empty `c.sources`:
   - For each source `s` in `c.sources`:
     - Fetch and parse `s.url` per its `type` (see "Per-company source types" above). For each extracted item whose dedup key is NOT in `SEEN` and whose date (if known) is within the last 14 days:
       - Append a candidate `(c, item)` where `item = {headline, description, source = "<c.name> · <s.label>", pubDate, link, source_kind = s.type}`.
       - **Skip the substring triage** — per-company sources are already company-scoped. The candidate is bound to `c` regardless of headline content.
   - If `s` is an `html_scrape` and you cannot reliably extract a structured item list from the page (e.g. JS-only render, page structure changed), log and continue — do not fabricate items.

3. **Relevance pass.** This is the critical step that justifies the architecture — without it, the digest is unusable.

   For each candidate `(c, item)`, judge: *is this article clearly and primarily about the company `c` described by `c.description`?* Use only the candidate's `headline`, `description`, and `source` for the judgment — do not fetch the article URL. Output `keep` or `drop` per candidate, with `drop` as the default when uncertain.

   Drop a candidate when any of the following is true:
   - The match is a substring coincidence on a generic English word or phrase ("emerge", "alpha", "seamless", "carousel", "horizon", "nova", etc.) where the item is not about the watchlisted company.
   - The item is about a different entity with the same or similar name (a band, a different region's company, a different industry's product, a publicly-traded ticker that collides with a private SG company, etc.) — the `description` field will frequently call these out explicitly.
   - The item is ticker-aggregator content (Zacks, TipRanks, MarketBeat, MEXC, StockInvest, TradingView, Stock Titan, AlphaStreet, geneonline, CryptoRank, Investing.com, Yahoo Finance, etc.) about a public ticker that happens to share a name with one of our private SG startups.
   - The company is mentioned only in passing (one-word mention in a list, not the subject of the article).
   - The item is generic SEO/listicle content or a stock-price/earnings-data restatement.

   Keep a candidate when the headline and source make it clear the article is genuinely about the watchlisted company — funding rounds, product launches, hiring, customer announcements, partnerships, regulatory news, founder interviews, etc.

   For candidates whose `source_kind != "firehose"` (i.e. they came from a per-company source), shift the bias slightly toward keeping — the source is already curated and company-scoped. But still drop:
   - **Duplicate of an item already kept this run from a different source** (e.g. the same product-launch announcement on both the company blog and AgFunder News firehose; keep one, drop the other).
   - **Bot / chore GitHub events** that slipped past the Step 2.5 filter — Dependabot bumps, README typo fixes, version-tag-only pushes.
   - **Lever job postings whose role is clearly a long-open evergreen req** (generic title, no team specified, low information).
   - **arXiv submissions that are revisions of prior papers** rather than new work (look for `[v2]`, `[v3]` markers in the title or the typical "comments: ..." block).

   Default bias for firehose candidates remains: drop. A small clean digest is the goal; false negatives are recoverable next run, false positives are noise.

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
