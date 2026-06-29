You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 2 of 3** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md` (may not exist if Layer 1 produced no items).
2. **Agent supplement (this prompt)** — dynamic web/browser search to fill gaps. Output: `signals/agent/<today>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — runs after this and updates per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 2: only write under `signals/agent/` and append to `signals/seen-urls.txt`. Do not touch `signals/updates/` or `signals/briefs/`.

## Task

Run dynamic web/browser searches to plug gaps in Layer 1's deterministic digest. The search strategy is yours to plan, not prescribed. Two cohorts, in priority order:

1. **Gap-fill** — companies with zero kept items across the last 7 UTC days of `signals/updates/*.md`. Otherwise invisible today.
2. **Deepen** — companies in today's `signals/updates/<today>.md`. Look for corroboration, valuation/investor context, follow-ons, company-site announcements.

## Inputs

- `data/agent-companies.json` — your cohort's slice of the watchlist (`{name, aliases?, description, sources?, identifiers?}`), pre-computed by `scripts/slice_companies.py` from the gap-fill queue plus today's deepen companies. `description` is authoritative for identity and disambiguation. `identifiers` (homepage, LinkedIn, Crunchbase, UEN) — use for targeted lookups, don't write. Fall back to `data/companies.json` only if the slice file is missing.
- `signals/updates/*.md` — Layer 1 outputs. Today's may be absent if Layer 1 found nothing.
- `signals/seen-urls.txt` — shared dedup state `SEEN`. Drop any URL already in it. Do **not** read this file into context; check membership per-URL with `grep -Fxq '<url>' signals/seen-urls.txt`.

## Steps

1. **Compute today's UTC date** as `<YYYY-MM-DD>`. This is the date used by both Layer 1's output file and the file you write.

2. **Read inputs.** Load `data/agent-companies.json` (fallback: `data/companies.json`). Glob `signals/updates/*.md` and select files whose filename date is within the last 7 UTC days (inclusive of today). Leave `signals/seen-urls.txt` on disk — membership checks happen via `grep` at judge time.

3. **Compute cohorts.**
   - **Gap-fill** = `signals/agent-queue.txt` (one name per line, oldest-queried first, pre-selected by `scripts/select_gapfill_queue.py`). Authoritative — do not expand. Empty if file missing.
   - **Deepen** = companies appearing as `## ` headings in `signals/updates/<today>.md`. Empty if file absent.

4. **Budget.** Hard cap **100 search/fetch ops total**. Process gap-fill queue in file order; stop when budget runs low. You may skip a company whose name is too generic to query confidently — skipping is fine, reordering is not.

5. **For each company in cohort order (gap-fill first, then deepen):**

   a. **Plan your own approach.** You have the company's `description`, `aliases`, and `identifiers` (homepage, LinkedIn, Crunchbase, UEN) and a shared 100-op budget. Decide what is most likely to surface genuine, recent news for *this* company: a web search, a fetch of the company site's news/blog/press index, a search scoped to a relevant trade publication or regulator, a query built from the founder's name — whatever you judge best. Use the description to disambiguate generic names. As a guideline, spend 1–2 ops per company; a third is fine when the first results look materially promising. The goal is real signal per op, not coverage theater.

   b. For each result `(c, item)`:
      - Dedup key = resolved URL, stripping `utm_*`, `ref`, `source`, `gclid`, `fbclid` query params.
      - If `grep -Fxq '<key>' signals/seen-urls.txt` matches, drop silently.
      - Else judge against `c.description`: *would this item teach a reader tracking `c` something new and true about `c`?* Keep only items genuinely and primarily about the watchlisted company, from sources you'd trust to be factual (company site, trade press, regulator filings, recognized investors — not content farms, not unattributed AI-generated PR, not aggregator restatements of a same-name ticker). Freshness windows: drop items older than 60 days for gap-fill, 14 days for deepen.
      - Bias: gap-fill **keep on margin** (the company is invisible otherwise); deepen **drop on margin** (only material new context).

   c. Append every key seen this turn — kept or dropped — to `signals/seen-urls.txt`.

6. **Write output.** Let `K[]` be the kept items across both cohorts.

   - If `K[]` is empty: write `signals/agent/<UTC-date>.md` containing only:
     ```
     # Agent supplement — <UTC-date>

     no agent items
     ```
     Final stdout: `no agent items`.

   - Otherwise write `signals/agent/<UTC-date>.md` with this structure:
     ```
     # Agent supplement — <UTC-date>

     ## Gap-fill (companies with no signals in last 7 days)
     ### <Company name>
     - **<headline>** — <source-domain or label> · <pubDate>
       <url>

     ## Deepen (today's covered companies)
     ### <Company name>
     - **<headline>** — <source-domain or label> · <pubDate>
       <url>
     ```

     Group items by company within each cohort. Use `c.name` (canonical) as the `###` heading. Omit a cohort section entirely if it has no kept items. If the file already exists from an earlier run today, append a new `## Run at <UTC time>` section at the bottom rather than overwriting — same convention as Layer 1.

   - Final stdout: a single line `<N> agent items across <M> companies (<G> gap-fill, <D> deepen, <X> ops used / 100, <Y> dropped)`. Nothing else.

## Constraints

- Only write `signals/agent/<UTC-date>.md` and append to `signals/seen-urls.txt`. Do not modify any other file.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single search/fetch fails, log and continue — do not fail the whole run.
- Hard stop at 100 ops. If you hit the cap mid-company, finish judging what you already retrieved, then stop and write what you have.
- Do not fabricate items. If a search returns nothing or only obvious junk for a company, that's a valid outcome — record nothing for that company.
- Stripping tracking params from URLs: at minimum strip query string keys starting with `utm_`, plus `ref`, `source`, `gclid`, `fbclid`. Keep the rest of the URL intact.
