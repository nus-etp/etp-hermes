You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 2 of 3** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md` (may not exist if Layer 1 produced no items).
2. **Agent supplement (this prompt)** — dynamic web/browser search to fill gaps. Output: `signals/agent/<today>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — runs after this and updates per-company `signals/briefs/<slug>/LIVING_BRIEF.md`.

Stay strictly within Layer 2: only write under `signals/agent/` and append to `signals/seen-urls.txt`. Do not touch `signals/updates/` or `signals/briefs/`.

## Task

Layer 1 produces a deterministic digest from RSS firehose + per-company curated sources. It misses things — long-tail companies whose names never trip the firehose substring match, and shallow coverage on companies that did get a hit but where the firehose only caught one angle.

Your job is to run **timestamped, dynamic web/browser searches** to plug those gaps, applying the same drop-rule discipline as Layer 1. Two cohorts, in priority order:

1. **Gap-fill cohort** — companies with zero kept items across the last 7 UTC days of `signals/updates/*.md`. These are otherwise invisible to the pipeline today.
2. **Deepen cohort** — companies that appear in today's `signals/updates/<today>.md`. Layer 1 already found something for them; you look for related coverage, corroboration, valuation/investor context, follow-on analyses, the company's own website announcement, etc.

## Inputs

- `data/companies.json` — watchlist. Same structure used by Layer 1: `{name, aliases?, description, sources?, identifiers?}`. The `description` is your primary tool for disambiguation in queries and for the relevance judgment. `identifiers` may include a homepage URL, LinkedIn, Crunchbase, ACRA UEN — use these to build targeted queries, but treat them as carry-only metadata (do not write to them).
- `signals/updates/*.md` — Layer 1 outputs, one file per UTC date. Today's is `signals/updates/<UTC-date>.md` (may be absent if Layer 1 found nothing).
- `signals/seen-urls.txt` — shared dedup state. Hold as a set `SEEN`. If you would return a URL already in `SEEN`, drop it silently.

## Steps

1. **Compute today's UTC date** as `<YYYY-MM-DD>`. This is the date used by both Layer 1's output file and the file you write.

2. **Read inputs.** Load `data/companies.json` and `signals/seen-urls.txt`. Glob `signals/updates/*.md` and select files whose filename date is within the last 7 UTC days (inclusive of today).

3. **Compute cohorts.**
   - **Gap-fill** is pre-selected for you by `scripts/select_gapfill_queue.py`, which runs before this layer. Read `signals/agent-queue.txt` — one canonical company name per line, ordered by ascending last-queried date (least-recently-queried first). This is the **authoritative** gap-fill cohort for this run — do not expand it with other companies from `data/companies.json`. If the file is missing or empty, treat gap-fill as empty for this run.
   - **Deepen** = `{ c : c.name appears as ## heading in signals/updates/<today>.md }` (empty if today's file doesn't exist).

4. **Budget.** You have a hard cap of **50 search/fetch operations total** across both cohorts. Prefer gap-fill over deepen when allocating — gap-fill companies are otherwise invisible. **Process the gap-fill queue in file order** (top-to-bottom in `signals/agent-queue.txt`) and stop when budget runs low; the ordering already encodes fairness. Within that order, you may still **skip** a company where you cannot construct a high-confidence query (e.g. one-word generic name with no aliases or sector hints) — skipping is fine; reordering is not.

5. **For each company in cohort order (gap-fill first, then deepen):**

   a. Form 1–2 targeted queries. Examples that work well:
      - Gap-fill: `"<canonical name>" Singapore site:linkedin.com OR site:vulcanpost.com` scoped to last 7 days.
      - Gap-fill: company homepage check — if `identifiers.website` is present, fetch the `/news` or `/press` or `/blog` index.
      - Deepen: `"<canonical name>" funding OR raised OR partnership` for the past 30 days; or competitor / sector context for the specific event Layer 1 surfaced.
      - Use `c.description` to add disambiguating qualifiers ("deep tech", "battery", "quantum", etc.) when the name is generic.

   b. Run the search using the web/browser tools available to you. For each result candidate `(c, item)`:
      - Compute a stable dedup key — prefer the resolved article URL. If the URL has tracking params (`utm_*`, `?ref=`, `gclid`, etc.), strip them before using as the key.
      - If the key is in `SEEN`, drop it silently — Layer 1 or a previous Layer 2 run already handled it.
      - Otherwise, judge relevance against `c.description` using the same rules as Layer 1's relevance pass:
        - **Drop** if it's a ticker-aggregator (Zacks, TipRanks, MarketBeat, etc.) hitting a same-name public ticker; a different-entity same-name collision; passing-mention listicle; generic SEO content; or the article is clearly older than 60 days for gap-fill / 14 days for deepen.
        - **Drop** if the source is low-trust spam (content farms, AI-generated press releases without primary attribution). Prefer the company's own site, established trade press, regulator filings, recognized investors' posts.
        - **Keep** if the article is genuinely and primarily about the watchlisted company.
      - Default bias for gap-fill: **keep on the margin** — these companies are invisible without you, so a moderate-confidence hit is worth surfacing. Default bias for deepen: **drop on the margin** — Layer 1 already covered the company, you only add value with materially new context.

   c. After judging, append **every** key seen this turn — kept or dropped — to `signals/seen-urls.txt` (append-only, one key per line). This is identical to Layer 1's discipline and prevents re-judging the same junk tomorrow.

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

   - Final stdout: a single line `<N> agent items across <M> companies (<G> gap-fill, <D> deepen, <X> ops used / 50, <Y> dropped)`. Nothing else.

## Constraints

- Only write `signals/agent/<UTC-date>.md` and append to `signals/seen-urls.txt`. Do not modify any other file.
- Do not commit anything — the workflow handles git operations after you exit.
- If a single search/fetch fails, log and continue — do not fail the whole run.
- Hard stop at 50 ops. If you hit the cap mid-company, finish judging what you already retrieved, then stop and write what you have.
- Do not fabricate items. If a search returns nothing or only obvious junk for a company, that's a valid outcome — record nothing for that company.
- Stripping tracking params from URLs: at minimum strip query string keys starting with `utm_`, plus `ref`, `source`, `gclid`, `fbclid`. Keep the rest of the URL intact.
