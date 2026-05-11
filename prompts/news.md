You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

## Task

Produce a daily company-news digest for the watchlist in `data/companies.json`, drawing from every feed in `data/feeds.json`, written to `signals/news/<UTC-date>.md`. Apply an LLM relevance pass — your own judgment — to drop false-positive matches before writing. Deduplicate against `signals/news/seen-urls.txt` so the same article never appears twice across runs.

## Inputs

- `data/companies.json` — watchlist. Array of objects:
  - `name` (string, required) — canonical name, used as the company label in output.
  - `aliases` (array of strings, optional, default `[]`) — additional names. Both `name` and every alias are used as case-insensitive substring patterns for the initial triage match against item title/description.
  - `description` (string, required) — one- or two-sentence description of the company. This is what you use during the relevance pass to judge whether a candidate item is actually about this company. It may explicitly call out unrelated entities to exclude (other companies with the same name, ticker collisions, etc.).

  Call the array `C[]`. For each company `c`, define `c.terms = [c.name, ...c.aliases]`.

- `data/feeds.json` — array of feed definitions. Each has `name`, `type` (always `firehose` in the current config), and `url`. Feeds are standard RSS/Atom.

- `signals/news/seen-urls.txt` — newline-delimited URLs already reported. Hold as a set `SEEN`.

## Steps

1. Read `data/companies.json`, `data/feeds.json`, `signals/news/seen-urls.txt`.

2. **Collect candidates.** For each feed `F` in `data/feeds.json`:
   - Fetch `F.url`. Parse the RSS/Atom feed.
   - For each `<item>` whose `<pubDate>` (or `<published>`) is within the last 7 days AND whose `<link>` is NOT in `SEEN`:
     - Build `haystack = lower(<title> + " " + <description>)`.
     - For each company `c` in `C`: if any `t` in `c.terms` (lowercased) is a substring of `haystack`, append a candidate `(c, item)` — where `item = {headline=<title>, description=<description>, source=F.name, pubDate, link}`. One item can produce multiple candidates if multiple companies match.

3. **Relevance pass.** This is the critical step that justifies the architecture — without it, the digest is unusable.

   For each candidate `(c, item)`, judge: *is this article clearly and primarily about the company `c` described by `c.description`?* Use only the candidate's `headline`, `description`, and `source` for the judgment — do not fetch the article URL. Output `keep` or `drop` per candidate, with `drop` as the default when uncertain.

   Drop a candidate when any of the following is true:
   - The match is a substring coincidence on a generic English word or phrase ("emerge", "alpha", "seamless", "carousel", "horizon", "nova", etc.) where the item is not about the watchlisted company.
   - The item is about a different entity with the same or similar name (a band, a different region's company, a different industry's product, a publicly-traded ticker that collides with a private SG company, etc.) — the `description` field will frequently call these out explicitly.
   - The item is ticker-aggregator content (Zacks, TipRanks, MarketBeat, MEXC, StockInvest, TradingView, Stock Titan, AlphaStreet, geneonline, CryptoRank, Investing.com, Yahoo Finance, etc.) about a public ticker that happens to share a name with one of our private SG startups.
   - The company is mentioned only in passing (one-word mention in a list, not the subject of the article).
   - The item is generic SEO/listicle content or a stock-price/earnings-data restatement.

   Keep a candidate when the headline and source make it clear the article is genuinely about the watchlisted company — funding rounds, product launches, hiring, customer announcements, partnerships, regulatory news, founder interviews, etc.

   Bias toward dropping. A small clean digest is the goal; false negatives are recoverable next run, false positives are noise.

4. **Write outputs.** Let `K[]` be the candidates that passed the relevance pass.
   - If `K[]` is empty: write nothing. Final stdout: `no new items`.
   - Otherwise:
     - Append the `link` of every item in `K[]` (deduped — one append per unique URL even if the item matched multiple companies) to `signals/news/seen-urls.txt`. Append only — do not rewrite.
     - Also append the links of any candidates that were *dropped* in step 3 to `seen-urls.txt`. We don't want to spend LLM budget re-judging the same junk every day.
     - Write `signals/news/<YYYY-MM-DD>.md` (UTC date) containing the kept items, grouped by company (use `c.name` as the heading). Format per item:
       ```
       - **<headline>** — <source> · <pubDate>
         <link>
       ```
       If the file already exists (re-run same day), append a new `## Run at <UTC time>` section instead of overwriting.
   - Final stdout: a single line of the form `<N> new items across <M> companies from <K> feeds (<D> dropped by relevance)`. Nothing else.

## Constraints

- Do not modify any file outside `signals/news/`.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single feed fetch fails, log and continue. Don't fail the whole run on one bad request.
- Do not fetch article URLs to enrich the relevance judgment; the headline + description + source is what you have.
