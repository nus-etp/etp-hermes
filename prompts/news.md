You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

## Task

Produce a daily company-news digest for the watchlist in `data/companies.json`, drawing from every feed in `data/feeds.json`, written to `signals/news/<UTC-date>.md`. Deduplicate against `signals/news/seen-urls.txt` so the same article never appears twice across runs.

## Inputs

- `data/companies.json` — watchlist. Array of objects:
  - `name` (string, required) — canonical name, used as the company label in output.
  - `aliases` (array of strings, optional, default `[]`) — additional names. Both `name` and every alias are used as search queries and as firehose-match substrings.
  - `exclude` (array of strings, optional, default `[]`) — case-insensitive substring blocklist. If any appears in an item's `<title>`, `<description>`, or source, drop the item for that company.

  Call the array `C[]`. For each company `c`, define `c.terms = [c.name, ...c.aliases]`.

- `data/feeds.json` — array of feed definitions. Each has `name`, `type` (`search` or `firehose`), an optional `format` (`rss` or `hn_algolia`; defaults to `rss`), and either `url_template` (with `{q}` placeholder, for `search`) or `url` (for `firehose`).

- `signals/news/seen-urls.txt` — newline-delimited URLs already reported. Hold as a set `SEEN`.

## Parsing

Item-field extraction depends on `F.format`:

- `rss` (default): standard RSS/Atom. Use `<title>`, `<link>`, `<description>` (or `<summary>`), `<pubDate>` (or `<published>`), and `<source>` if present.
- `hn_algolia`: JSON body with a `hits[]` array. For each hit, treat the fields as:
  - headline = `hit.title`
  - link = `https://news.ycombinator.com/item?id=` + `hit.objectID`
  - pubDate = `hit.created_at` (ISO 8601). Use `hit.created_at_i` (unix seconds) for the 7-day window check — more reliable than parsing the string.
  - description = `""` (the search API does not return body text)
  - source = `"Hacker News"`

## Steps

1. Read `data/companies.json`, `data/feeds.json`, `signals/news/seen-urls.txt`.

2. For each feed `F` in `data/feeds.json`:

   **If `F.type == "search"`** — one fetch per (company, term):
   - For each company `c` in `C`, for each `t` in `c.terms`:
     - URL-encode `t` with surrounding quotes (i.e. encode `"<t>"`) and substitute for `{q}` in `F.url_template`.
     - Fetch the resulting URL. Parse the RSS.
     - For each `<item>` whose `<pubDate>` is within the last 7 days AND whose `<link>` is NOT in `SEEN` AND which passes the exclude check for `c` (see below): capture `(company=c.name, headline=<title>, source=<source> or F.name, pubDate, link)`. Add the link to `SEEN`.

   **If `F.type == "firehose"`** — one fetch for the whole feed, filter client-side:
   - Fetch `F.url`. Parse the RSS.
   - For each `<item>` whose `<pubDate>` is within the last 7 days AND whose `<link>` is NOT in `SEEN`:
     - For each company `c` in `C`: if any `t` in `c.terms` appears (case-insensitive substring) in the item's `<title>` OR `<description>`, AND the item passes the exclude check for `c`, capture `(company=c.name, headline=<title>, source=F.name, pubDate, link)`. If multiple companies match, emit one entry per company. Add the link to `SEEN` once after processing the item.

   **Exclude check for company `c` against an item**: build `haystack = lower(title + " " + description + " " + source)`. If any `e` in `c.exclude` (lowercased) is a substring of `haystack`, the item fails the check for `c`.

3. If at least one new item was captured:
   - Append every new link (one per line) to `signals/news/seen-urls.txt`. Append only — do not rewrite.
   - Write `signals/news/<YYYY-MM-DD>.md` (UTC date) containing the new items, grouped by company (use `c.name` as the heading). Format per item:
     ```
     - **<headline>** — <source> · <pubDate>
       <link>
     ```
     If the file already exists (re-run same day), append a new `## Run at <UTC time>` section instead of overwriting.

4. If no new items: write nothing.

## Constraints

- Do not modify any file outside `signals/news/`.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single feed or company fetch fails, log and continue. Don't fail the whole run on one bad request.
- Final stdout reply: a single line of the form `<N> new items across <M> companies from <K> feeds` or `no new items`. Nothing else.
