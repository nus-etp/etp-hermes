You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

## Task

Produce a daily company-news digest for the watchlist in `data/companies.txt`, written to `signals/news/<UTC-date>.md`. Deduplicate against `signals/news/seen-urls.txt` so the same article never appears twice across runs.

## Steps

1. Read `data/companies.txt`. Skip blank lines and lines starting with `#`. The result is your company list `C[]`.

2. Read `signals/news/seen-urls.txt`. Treat each non-blank line as a URL already reported. Hold this as a set `SEEN`.

3. For each company `c` in `C`:
   - URL-encode `c` (keep the surrounding quotes — search for `"<c>"`).
   - Fetch `https://news.google.com/rss/search?q="<c>"&hl=en-SG&gl=SG&ceid=SG:en`.
   - Parse the RSS. For each `<item>` whose `<pubDate>` is within the last 7 days AND whose `<link>` is NOT in `SEEN`:
     - Capture: company name, headline (`<title>`), source (`<source>`), pubDate, link.
     - Add the link to `SEEN`.

4. If at least one new item was captured across all companies:
   - Append every new link (one per line) to `signals/news/seen-urls.txt`. Do not rewrite the file; append only.
   - Write `signals/news/<YYYY-MM-DD>.md` (UTC date) containing the new items, grouped by company. Format per item:
     ```
     - **<headline>** — <source> · <pubDate>
       <link>
     ```
     If `signals/news/<YYYY-MM-DD>.md` already exists (re-run on same day), append a new `## Run at <UTC time>` section rather than overwriting.

5. If no new items: write nothing. Final reply: `no new items`.

## Constraints

- Do not modify any file outside `signals/news/`.
- Do not commit anything — the workflow handles git operations after you exit.
- If a Google News request fails for one company, log and continue with the rest. Don't fail the whole run.
- Final stdout reply: a single line of the form `<N> new items across <M> companies` or `no new items`. Nothing else.
