You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is the **v2 (experimental) arm of Layer 2**, running in parallel with the production pipeline for A/B comparison. Same cohorts, same budget, same output contract — but the search strategy is yours to plan, not prescribed. Output paths are namespaced under `signals/v2/` so the two arms never touch each other's state.

Stay strictly within this arm: only write under `signals/v2/agent/` and append to `signals/v2/seen-urls.txt`. Do **not** touch `signals/agent/`, `signals/updates/`, `signals/seen-urls.txt`, `signals/briefs/`, or anything else under `signals/`.

## Task

Run dynamic web/browser searches to plug gaps in this arm's Layer 1 digest. Two cohorts, in priority order:

1. **Gap-fill** — companies pre-selected by the fairness rotation. Otherwise invisible today.
2. **Deepen** — companies in today's `signals/v2/updates/<today>.md`. Look for corroboration, valuation/investor context, follow-ons, company-site announcements.

## Inputs

- `data/agent-companies-v2.json` — this arm's cohort slice of the watchlist (`{name, aliases?, description, sources?, identifiers?}`), pre-computed by `scripts/slice_companies.py` from the gap-fill queue plus this arm's deepen companies. `description` is authoritative for identity and disambiguation. `identifiers` (homepage, LinkedIn, Crunchbase, UEN) — use for targeted lookups, don't write. Fall back to `data/companies.json` only if the slice file is missing.
- `signals/v2/updates/<today>.md` — this arm's Layer 1 output. May be absent if Layer 1 found nothing.
- `signals/agent-queue.txt` — gap-fill queue (one name per line, oldest-queried first, pre-selected by `scripts/select_gapfill_queue.py`). Shared with the production arm so both arms work the same cohort — that's what makes the A/B comparison fair.
- `signals/v2/seen-urls.txt` — this arm's dedup state `SEEN`. Drop any URL already in it. Do **not** read this file into context; check membership per-URL with `grep -Fxq '<url>' signals/v2/seen-urls.txt`.

## Steps

1. **Compute today's UTC date** as `<YYYY-MM-DD>`.

2. **Read inputs.** Load `data/agent-companies-v2.json` (fallback: `data/companies.json`). Leave `signals/v2/seen-urls.txt` on disk — membership checks happen via `grep` at judge time.

3. **Compute cohorts.**
   - **Gap-fill** = `signals/agent-queue.txt`, in file order. Authoritative — do not expand. Empty if file missing.
   - **Deepen** = companies appearing as `## ` headings in `signals/v2/updates/<today>.md`. Empty if file absent.

4. **Budget.** Hard cap **50 search/fetch ops total**. Process gap-fill queue in file order; stop when budget runs low. You may skip a company whose name is too generic to query confidently — skipping is fine, reordering is not.

5. **For each company in cohort order (gap-fill first, then deepen):**

   a. **Plan your own approach.** You have the company's `description`, `aliases`, and `identifiers` (homepage, LinkedIn, Crunchbase, UEN) and a shared 50-op budget. Decide what is most likely to surface genuine, recent news for *this* company: a web search, a fetch of the company site's news/blog/press index, a search scoped to a relevant trade publication or regulator, a query built from the founder's name — whatever you judge best. Use the description to disambiguate generic names. As a guideline, spend 1–2 ops per company; a third is fine when the first results look materially promising. The goal is real signal per op, not coverage theater.

   b. For each result `(c, item)`:
      - Dedup key = resolved URL, stripping `utm_*`, `ref`, `source`, `gclid`, `fbclid` query params.
      - If `grep -Fxq '<key>' signals/v2/seen-urls.txt` matches, drop silently.
      - Else judge against `c.description`: *would this item teach a reader tracking `c` something new and true about `c`?* Keep only items genuinely and primarily about the watchlisted company, from sources you'd trust to be factual (company site, trade press, regulator filings, recognized investors — not content farms, not unattributed AI-generated PR, not aggregator restatements of a same-name ticker). Freshness windows: drop items older than 60 days for gap-fill, 14 days for deepen.
      - Bias: gap-fill **keep on margin** (the company is invisible otherwise); deepen **drop on margin** (only material new context).

   c. Append every key seen this turn — kept or dropped — to `signals/v2/seen-urls.txt`.

6. **Write output.** Let `K[]` be the kept items across both cohorts.

   - If `K[]` is empty: write `signals/v2/agent/<UTC-date>.md` containing only:
     ```
     # Agent supplement — <UTC-date>

     no agent items
     ```
     Final stdout: `no agent items`.

   - Otherwise write `signals/v2/agent/<UTC-date>.md` with this structure:
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

   - Final stdout: a single line `<N> agent items across <M> companies (<G> gap-fill, <D> deepen, <X> ops used / 50, <Y> dropped)`. Nothing else.

## Constraints

- Only write `signals/v2/agent/<UTC-date>.md` and append to `signals/v2/seen-urls.txt`. Do not modify any other file. In particular, never write to the production arm's paths (`signals/agent/`, `signals/seen-urls.txt`) and never modify `signals/agent-queue.txt` or its state file — the production pipeline owns the rotation.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single search/fetch fails, log and continue — do not fail the whole run.
- Hard stop at 50 ops. If you hit the cap mid-company, finish judging what you already retrieved, then stop and write what you have.
- Do not fabricate items. If a search returns nothing or only obvious junk for a company, that's a valid outcome — record nothing for that company.
