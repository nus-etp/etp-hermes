You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 3 of 3** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md` (may not exist if nothing new).
2. **Agent supplement** (`prompts/agent_supplement.md`) — already ran. Output: `signals/agent/<today>.md`.
3. **Synthesis (this prompt)** — for every company touched by Layers 1–2 today, update a rolling per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 3: only write under `signals/briefs/<slug>/`. Do not modify `signals/updates/`, `signals/agent/`, `signals/seen-urls.txt`, or `data/`.

## Task

Merge today's new signals into each touched company's living brief. Preserve prior content unless a new signal materially changes it.

## Inputs

- `signals/updates/<UTC-date>.md` — today's Layer 1 output (may be absent or contain only "no new items").
- `signals/agent/<UTC-date>.md` — today's Layer 2 output (may be absent or contain "no agent items").
- `data/touched-companies.json` — the touched companies' slice of the watchlist, pre-computed by `scripts/slice_companies.py` from today's updates/agent headings. Same schema as `companies.json`. For each touched company `c`, pull `c.name`, `c.description`, `c.aliases`, `c.identifiers` for the brief's profile section, and `c.funding_rounds` (+ optional `c.funding_notes`) for the brief's funding history section. Fall back to `data/companies.json` only if the slice file is missing — do not read the full watchlist otherwise.
- Existing `signals/briefs/<slug>/LIVING_BRIEF.md` per touched company (may not exist on first touch).

## Slug derivation

`slug(c.name)` = lowercase, replace every run of non-`[a-z0-9]` characters with a single `-`, trim leading/trailing `-`. Examples: `Carousell` → `carousell`; `Horizon Quantum Computing` → `horizon-quantum-computing`; `NEU Battery Materials` → `neu-battery-materials`. The slug is authoritative — never store it in `companies.json`; always derive from `c.name`.

## Steps

1. **Compute today's UTC date** as `<UTC-date>` (format `YYYY-MM-DD`). Compute today's UTC timestamp as `<UTC-timestamp>` (format `YYYY-MM-DD HH:MM UTC`).

2. **Collect today's signals.**
   - Read `signals/updates/<UTC-date>.md` if it exists. Parse it as: H2 (`## `) = company name (canonical, matches `c.name` in `data/touched-companies.json`); below each H2, a list of items in the format `- **<headline>** — <source> · <pubDate>\n  <link>`. There may also be `## Run at <time>` subheadings — those are not company names; treat them as section dividers and continue parsing the H2s that follow.
   - Read `signals/agent/<UTC-date>.md` if it exists. Structure: H2 = cohort (`## Gap-fill ...` or `## Deepen ...`); H3 = company name; items below as in Layer 1. Skip the cohort H2s; collect items grouped by the H3 company name.
   - Build `TOUCHED` = set of distinct company names mentioned today across both files. For each name, also build `NEW_SIGNALS[c.name]` = list of `(headline, source, pubDate, link)` tuples merged from both files, deduped by URL.

3. **If `TOUCHED` is empty**, write nothing and exit with stdout `no companies touched today`.

4. **For each company `c` in `TOUCHED`:**

   a. Look up `c` in `data/touched-companies.json` by matching `c.name` exactly. If not found, skip the company and log — don't write a brief for an unrecognized name.

   b. Compute `slug = slug(c.name)`. Brief path = `signals/briefs/<slug>/LIVING_BRIEF.md`.

   c. **If the brief does not exist**, this is the first-time write. Generate all sections from scratch:
      - **Header**: `# <c.name> — LIVING BRIEF`, then `_Last updated: <UTC-timestamp>_`, then `![Infographic](infographic.png)` on its own line (unconditional — Layer 4 fills the PNG later).
      - **Thesis**: 2–3 sentences derived from `c.description` and today's signals.
      - **Profile**: bullets from `c.description` (sector, region) plus `c.identifiers` (LinkedIn, Crunchbase, UEN, website). Include only fields actually present — don't invent. For `Sector:`, follow the "Sector tagging" rules below.
      - **Funding history**: render from `c.funding_rounds` (see "Funding history rendering rules"). Omit if empty/absent.
      - **Recent signals**: today's `NEW_SIGNALS[c.name]` as cards, most recent first. Top-level bullet: `- **<signal-date>** — <one-line synthesis, your own words> — [<source-short>](<url>)` (NOT the headline verbatim). `<signal-date>` is the item's publication date and **must** be strict ISO `YYYY-MM-DD` (or the literal `date unknown` if no date is available) — never a month name or `Month D, YYYY` form (write `2026-05-11`, never `May 11, 2026` or `April 2026`; if only month/year is known, use the first of the month, e.g. `2026-04-01`). Optionally followed by indented sub-bullets enriched from the fetched body (see "URL fetching" and "Signal card extraction" below). If fetch skipped/failed, emit top-level bullet only.
      - **Older signals**: `_none_`.
      - **Open questions**: 1–3 questions today's signals raise but don't answer. Skip if none.

   d. **If the brief already exists**, read it and merge:
      - **Header**: update `_Last updated:_` to `<UTC-timestamp>`. Keep the H1 verbatim. Ensure `![Infographic](infographic.png)` follows `_Last updated:_`; insert if missing (legacy briefs).
      - **Thesis**: keep verbatim *unless* today's signals materially shift trajectory (new market, new funding stage, pivot, exec change, acquisition, shutdown). If you rewrite, write naturally — not as a list of citations.
      - **Profile**: touch only when a today's signal contradicts or extends a field. Otherwise keep verbatim. Exception: if `c.sector` is present on the company object and the on-disk `Sector:` bullet does not match it, update the bullet to match `c.sector` verbatim (silent correction, no SECTOR_PROPOSAL needed).
      - **Funding history**: re-render from `c.funding_rounds`. `companies.json` is authoritative — replace the on-disk section if it differs. Omit if empty/absent. Do **not** add rounds inferred from today's signals — that goes in `data/companies.json` first.
      - **Recent signals**: prepend today's `NEW_SIGNALS[c.name]` cards. URL-level dedup against the existing brief (skip if URL already in `Recent signals` or `Older signals`; never re-fetch on-disk URLs). Cap: 20 **top-level** bullets — sub-bullets don't count. If exceeded, demote oldest excess to `Older signals`, moving each card as a single subtree (top bullet + its sub-bullets), oldest at the bottom.
      - **Open questions**: append new questions; remove any that today's signals clearly answer. If empty after editing, render `_none open_`.

   e. **No-write check**: if after the merge the brief file's contents are byte-identical to the existing file, **do not write**. This preserves git history and avoids meaningless commits.

   f. **Write the merged brief** to `signals/briefs/<slug>/LIVING_BRIEF.md`, creating the directory if needed. Overwrite the existing file in a single write — do not append.

5. **Final stdout**: a single line `<W> briefs updated, <C> created, <S> skipped (no changes)`. Nothing else.

## Brief template (use exactly these section headings)

```markdown
# <Company name> — LIVING BRIEF
_Last updated: <YYYY-MM-DD HH:MM UTC>_
![Infographic](infographic.png)

## Thesis
<2–3 sentences. Rolling assessment of what this company is and where it's going.>

## Profile
- Sector: …
- Region: …
- Founded: …
- Stage / funding: …
- Key people: …
- Identifiers: <LinkedIn URL>, <Crunchbase URL>, <UEN>, <website>

## Funding history
- **<date>** — <stage>, <amount> — <lead>; <other investors> — [source](<url>)
- **<date>** — <stage>, <amount> — <lead>; <other investors> — [source](<url>)

_Total disclosed: <sum of amount_usd as $X.XM>._  <!-- optional, omit if no amounts -->

## Recent signals
- **<YYYY-MM-DD>** — <one-line synthesis, your own words> — [<source-short>](<url>)
  - Summary: <2–3 sentence neutral expansion in the brief's voice>
  - People: <Name (role)>, <Name (role)>
  - Counterparties: <Lead investor / Customer / Partner / Acquirer / Regulator>
  - Numbers: <disclosed figures: round size, valuation, headcount, ARR, customer count, units>
  - Quote: "<verbatim excerpt from the fetched body>" — <attributed speaker>
- **<YYYY-MM-DD>** — … — [<source-short>](<url>)

## Older signals
_none_

## Open questions
- <question>
- <question>
```

Omit Profile bullets you don't have. Omit `Funding history` if `c.funding_rounds` is empty/absent. Render empty `Older signals` as `_none_`, empty `Open questions` as `_none open_`. Omit any sub-bullet with no evidence — no empty placeholders. If URL was not fetched or fetch failed, emit top-level bullet only. Every `## Recent signals` and `## Older signals` top-level bullet **must** open with `- **<YYYY-MM-DD>** — ` (strict ISO date) or `- **date unknown** — `; no other date form is valid (`- **April 2026** — ` and `- **May 11, 2026** — ` both fail the brief template check).

## Funding history rendering rules

`c.funding_rounds` is an array of objects with: `date` (`YYYY-MM-DD` | `YYYY-MM` | `YYYY` | null), `stage`, `amount` (display string, may be null), `amount_usd` (number, may be null), `lead_investors` (array of strings, may be empty), `investors` (array of strings, may be empty), `source` (URL, required).

- **Order**: oldest at the top, most recent at the bottom (chronological). Treat null `date` as oldest-known and place last among null-dated entries.
- **Bullet format**: `- **<date-or-"date unknown">** — <stage>, <amount-or-"undisclosed"> — <lead-investors-comma-joined>; <other-investors-comma-joined> — [source](<url>)`. If `lead_investors` is empty, drop the leading "; " prefix and just render the investors. If both lists are empty, write "investors undisclosed". Truncate the investor list to the first 5 names and append "et al." if longer.
- **Source label**: derive a short host label from the URL (e.g. `techcrunch.com`, `pier71.sg`, `nus.edu.sg`). Use the bare host, no `www.`.
- **Total line**: after the bullet list, sum `amount_usd` across all rounds (skip nulls) and render `_Total disclosed: $<X>M._` (one decimal place, rounded). If every round's `amount_usd` is null, omit the total line.
- **`funding_notes`**: if `c.funding_notes` is present and the company has rounds, ignore it (the rounds speak for themselves). If `c.funding_notes` is present and `c.funding_rounds` is empty, also omit the section — the notes are diagnostic and not for the brief.

## URL fetching for signal enrichment

Fetch article bodies for kept URLs in today's `NEW_SIGNALS[c.name]` to produce the sub-bullets. Bounded, prioritized, fails closed.

- **Eligibility**: only today's new URLs about to be written. **Never re-fetch URLs already on disk.**
- **Budget**: 20 fetches per run total across all companies. Once spent, remaining URLs render top-level only.
- **Priority** when eligible URLs > 20:
  1. `signals/agent/<UTC-date>.md` Deepen-cohort URLs first.
  2. Then `signals/updates/<UTC-date>.md` URLs in file order.
  3. Tie-break: prefer trade press (`techcrunch.com`, `agfundernews.com`, `e27.co`, `vulcanpost.com`, `press.<company>.com`).
- **Host skip-list — never fetch, do not consume budget**: `news.google.com`, `linkedin.com`, `x.com`, `twitter.com`, `facebook.com`, `bloomberg.com`, `straitstimes.com`, `wsj.com`, `ft.com`, `nikkei.com`, `theinformation.com`. Render top-level bullet only.
- **Per-fetch timeout**: 15s. No retries.
- **Failure** = HTTP error, timeout, body < 500 chars of extractable text, or `subscribe to continue` / `JavaScript is required` sentinel. Emit no sub-bullets, **count toward budget**, move on.

## Signal card extraction rules

When a fetch succeeds, emit sub-bullets in this order; omit any whose field has no evidence:

1. **Summary** — 2–3 neutral sentences. Required.
2. **People** — `Name (role)` comma-joined. Cap 4. Skip "a spokesperson" / "person familiar with the matter".
3. **Counterparties** — lead investor, acquirer, customer, regulator, partner. Cap 4. Skip co-investors already in `Funding history`.
4. **Numbers** — disclosed only: deal size, valuation (label pre/post), headcount, revenue/ARR, customers, units shipped; market size only if quoted by the company. Cap 6 tokens.
5. **Quote** — at most one, only if it adds signal beyond Summary. **Must be a verbatim contiguous substring of the fetched body.** If not guaranteed, omit. No paraphrases-in-quotes. No quotes from headline or memory.

Do **NOT** extract: "About <Company>" trailers; generic market-size claims not attributed to the company; multiple paraphrases of the same number; analyst forecasts unrelated to the company; cap-table content already in `Funding history`; image captions, ads, related-articles lists, share-button labels.

If two cards cover the same announcement (different outlets), keep both top-level bullets but reduce the second's sub-bullets to a single `Summary: Corroborates the <date> announcement; no new facts.`

## Sector tagging

`data/sectors.json` is the canonical sector taxonomy — a flat array of atomic sector strings (e.g. `"Agritech"`, `"AI"`, `"Climate tech"`).

**Rules (in priority order):**

1. **If `c.sector` is present** on the company object: write it verbatim as the `Sector:` bullet. Do not paraphrase or append qualifiers.
2. **If `c.sector` is absent**: pick the 1–2 closest entries from `data/sectors.json` that describe the company. Join two entries with ` / ` (e.g. `Agritech / Biotechnology`). Do not invent entries not in the list; do not use more than two.
3. **Emit a proposal to stdout** (one line, does not appear in the brief):
   ```
   SECTOR_PROPOSAL: <slug> → "<derived value>"
   ```
   Emit this whenever `c.sector` was absent (rule 2 applied). This signals that the human should review and add `"sector": "<value>"` to `data/companies.json`.

**Do not emit a SECTOR_PROPOSAL when `c.sector` was already present** — it was intentional.

## Constraints

- Only create/modify files matching `signals/briefs/<slug>/LIVING_BRIEF.md`. Do not modify any other file.
- Do not commit anything — the workflow handles git operations after you exit.
- URL fetching is permitted **only** as defined in "URL fetching for signal enrichment". The top-level date bullet must stay defensible from headline + source alone so skipped/failed fetches degrade to a one-liner.
- Do not write a brief for a company name that's not in `data/touched-companies.json` — log and skip.
- Never overwrite a brief with the same content as already on disk (the no-write check above).
