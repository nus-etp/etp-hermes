You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 3 of 3** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md` (may not exist if nothing new).
2. **Agent supplement** (`prompts/agent_supplement.md`) — already ran. Output: `signals/agent/<today>.md`.
3. **Synthesis (this prompt)** — for every company touched by Layers 1–2 today, update a rolling per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 3: only write under `signals/briefs/<slug>/`. Do not modify `signals/updates/`, `signals/agent/`, `signals/seen-urls.txt`, or `data/`.

## Task

The briefs are the durable, per-company unit of analysis. Daily digests get buried in git history; a brief is the thing you'd open if asked "what's the state of company X right now?" Your job is to merge today's new signals into each touched company's living brief — surgically, preserving prior content unless a new signal materially changes it.

## Inputs

- `signals/updates/<UTC-date>.md` — today's Layer 1 output (may be absent or contain only "no new items").
- `signals/agent/<UTC-date>.md` — today's Layer 2 output (may be absent or contain "no agent items").
- `data/companies.json` — watchlist. For each touched company `c`, pull `c.name`, `c.description`, `c.aliases`, `c.identifiers` for the brief's profile section, and `c.funding_rounds` (+ optional `c.funding_notes`) for the brief's funding history section.
- Existing `signals/briefs/<slug>/LIVING_BRIEF.md` per touched company (may not exist on first touch).

## Slug derivation

`slug(c.name)` = lowercase, replace every run of non-`[a-z0-9]` characters with a single `-`, trim leading/trailing `-`. Examples: `Carousell` → `carousell`; `Horizon Quantum Computing` → `horizon-quantum-computing`; `NEU Battery Materials` → `neu-battery-materials`. The slug is authoritative — never store it in `companies.json`; always derive from `c.name`.

## Steps

1. **Compute today's UTC date** as `<UTC-date>` (format `YYYY-MM-DD`). Compute today's UTC timestamp as `<UTC-timestamp>` (format `YYYY-MM-DD HH:MM UTC`).

2. **Collect today's signals.**
   - Read `signals/updates/<UTC-date>.md` if it exists. Parse it as: H2 (`## `) = company name (canonical, matches `c.name` in `data/companies.json`); below each H2, a list of items in the format `- **<headline>** — <source> · <pubDate>\n  <link>`. There may also be `## Run at <time>` subheadings — those are not company names; treat them as section dividers and continue parsing the H2s that follow.
   - Read `signals/agent/<UTC-date>.md` if it exists. Structure: H2 = cohort (`## Gap-fill ...` or `## Deepen ...`); H3 = company name; items below as in Layer 1. Skip the cohort H2s; collect items grouped by the H3 company name.
   - Build `TOUCHED` = set of distinct company names mentioned today across both files. For each name, also build `NEW_SIGNALS[c.name]` = list of `(headline, source, pubDate, link)` tuples merged from both files, deduped by URL.

3. **If `TOUCHED` is empty**, write nothing and exit with stdout `no companies touched today`.

4. **For each company `c` in `TOUCHED`:**

   a. Look up `c` in `data/companies.json` by matching `c.name` exactly. If not found, skip the company and log — don't write a brief for an unrecognized name.

   b. Compute `slug = slug(c.name)`. Brief path = `signals/briefs/<slug>/LIVING_BRIEF.md`.

   c. **If the brief does not exist**, this is the first-time write. Generate all sections from scratch:
      - **Header**: `# <c.name> — LIVING BRIEF`, then `_Last updated: <UTC-timestamp>_`, then `![Infographic](infographic.png)` on its own line. The image line is unconditional — Layer 4 generates the PNG later in the run; if it hasn't yet (or fails), GitHub renders a broken-image placeholder until the next infographic run fills it in.
      - **Thesis**: 2–3 sentences derived from `c.description` and today's signals. Frame what the company is and the trajectory the signals suggest.
      - **Profile**: bullets pulled from `c.description` (sector, region, what they do) plus `c.identifiers` (LinkedIn, Crunchbase, UEN, website if present). Include only fields that are actually present in `companies.json` — don't invent.
      - **Funding history**: render from `c.funding_rounds` (see "Funding history rendering rules" below). Omit the section entirely if `c.funding_rounds` is empty or absent.
      - **Recent signals**: today's `NEW_SIGNALS[c.name]` as bullets, most recent first, in the format `- **<UTC-date>** — <one-line summary> — [<source-short>](<url>)`. The summary must be your own one-line synthesis, NOT a copy of the headline.
      - **Older signals**: empty section (`_none_`).
      - **Open questions**: 1–3 questions that today's signals raise but don't answer (e.g. "What's the round's valuation?", "Who led?", "Is the new hire replacing a departure?"). Skip the section if you have nothing concrete to ask.

   d. **If the brief already exists**, read it and merge:
      - **Header**: update the `_Last updated:_` line to `<UTC-timestamp>`. Keep the H1 verbatim. Ensure the line `![Infographic](infographic.png)` is present immediately after the `_Last updated:_` line; if absent (legacy briefs written before Layer 4 existed), insert it.
      - **Thesis**: keep verbatim *unless* today's signals materially shift the company's trajectory (new market, new funding stage, pivot, major exec change, acquisition, shutdown). If you rewrite, the new thesis must implicitly justify itself by referencing the kind of signal that drove the change — but write naturally, not as a list of citations.
      - **Profile**: touch only when a today's signal contradicts or extends a field (e.g. funding round → stage/valuation; new office → region; founder departure → key people). Otherwise keep verbatim.
      - **Funding history**: re-render the section from `c.funding_rounds` (see "Funding history rendering rules" below). `companies.json` is authoritative — if the rendered section differs from what's on disk (e.g. a new round was added to the JSON), replace the existing section with the freshly rendered one. If `c.funding_rounds` is empty/absent, drop the section. Do **not** add rounds inferred from today's signals into the brief here — that belongs in `data/companies.json` first, then it flows into the brief on the next run.
      - **Recent signals**: prepend today's `NEW_SIGNALS[c.name]` bullets to the existing list. If a today's URL is already present in `Recent signals` or `Older signals`, skip it (URL-level dedup against the existing brief). Then enforce the cap: if `Recent signals` now has more than 20 bullets, move the oldest excess down into `Older signals` (still bulleted, same format, oldest at the bottom of `Older signals`).
      - **Open questions**: append new questions raised by today's signals; remove any existing question that today's signals clearly answer. If after editing the section is empty, replace it with `_none open_`.

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
- **<YYYY-MM-DD>** — … — [<source-short>](<url>)

## Older signals
_none_

## Open questions
- <question>
- <question>
```

Omit Profile bullets for fields you don't have. Omit the entire **Funding history** section if `c.funding_rounds` is empty or absent. If `Older signals` is empty render it as `_none_`. If `Open questions` is empty render it as `_none open_`.

## Funding history rendering rules

`c.funding_rounds` is an array of objects with: `date` (`YYYY-MM-DD` | `YYYY-MM` | `YYYY` | null), `stage`, `amount` (display string, may be null), `amount_usd` (number, may be null), `lead_investors` (array of strings, may be empty), `investors` (array of strings, may be empty), `source` (URL, required).

- **Order**: oldest at the top, most recent at the bottom (chronological). Treat null `date` as oldest-known and place last among null-dated entries.
- **Bullet format**: `- **<date-or-"date unknown">** — <stage>, <amount-or-"undisclosed"> — <lead-investors-comma-joined>; <other-investors-comma-joined> — [source](<url>)`. If `lead_investors` is empty, drop the leading "; " prefix and just render the investors. If both lists are empty, write "investors undisclosed". Truncate the investor list to the first 5 names and append "et al." if longer.
- **Source label**: derive a short host label from the URL (e.g. `techcrunch.com`, `pier71.sg`, `nus.edu.sg`). Use the bare host, no `www.`.
- **Total line**: after the bullet list, sum `amount_usd` across all rounds (skip nulls) and render `_Total disclosed: $<X>M._` (one decimal place, rounded). If every round's `amount_usd` is null, omit the total line.
- **`funding_notes`**: if `c.funding_notes` is present and the company has rounds, ignore it (the rounds speak for themselves). If `c.funding_notes` is present and `c.funding_rounds` is empty, also omit the section — the notes are diagnostic and not for the brief.

## Constraints

- Only create/modify files matching `signals/briefs/<slug>/LIVING_BRIEF.md`. Do not modify any other file.
- Do not commit anything — the workflow handles git operations after you exit.
- Do not fetch URLs to enrich the synthesis. You work from headline + source + pubDate + the existing brief + `c.description`. If a one-line summary needs the article body, write a more cautious summary that stays defensible from the headline alone.
- Do not write a brief for a company name that's not in `data/companies.json` — log and skip.
- Never overwrite a brief with the same content as already on disk (the no-write check above).
