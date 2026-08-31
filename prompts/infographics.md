You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 4 of 4** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — already ran. Output: `signals/agent/<today>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — already ran. Output: zero or more `signals/briefs/<slug>/LIVING_BRIEF.md` writes.
4. **Infographic generation (this prompt)** — for every brief carrying a **genuinely new signal** this run, generate a visual summary PNG using the bundled `creative-baoyu-infographic` skill and place it alongside the brief. Remaining cap slots backfill briefs whose infographic is missing from a prior failure.

A deterministic pre-step, `scripts/select_infographic_queue.py`, has already decided which briefs qualify and written the answer to `data/infographic-queue.json`. You do **not** compute the changed set yourself — a raw working-tree-vs-`HEAD` diff over-fires, because `normalize_briefs.py` reorders/rotates signal blocks and synthesis bumps `_Last updated:_`, appends "Also reported by:" sources to existing signals, and retires open questions, none of which change what the image depicts. The pre-step counts only a genuinely new dated signal (in either the Recent or Older section, keyed by date + headline with corroboration stripped) or a changed `### Hiring` roll-up.

Stay strictly within Layer 4: only write `signals/briefs/<slug>/infographic.png` for slugs the pre-step queued as changed or missing-backfill. Do not modify any `LIVING_BRIEF.md` (synthesis owns those), `signals/updates/`, `signals/agent/`, `signals/seen-urls.txt`, `signals/dropped-urls.txt`, or `data/`.

## Task

For every brief carrying a genuinely new signal this run, regenerate `infographic.png` next to it via the bundled `creative-baoyu-infographic` skill. The changed set is precomputed in `data/infographic-queue.json` (`changed`) — do not recompute it from a git diff. Then process the pre-step's backfill list (`missing`): any brief directory that has a `LIVING_BRIEF.md` but no `infographic.png` is a prior Layer 4 failure (the brief embeds the image unconditionally, so it renders broken until regenerated).

## Inputs

- `data/infographic-queue.json` — `{"changed": [<slug>, ...], "missing": [<slug>, ...]}`, written by `scripts/select_infographic_queue.py`. `changed` = briefs carrying a new signal this run; `missing` = missing-infographic backfill, oldest-first, already stripped of anything in `changed`.
- The brief markdown itself, one file per queued slug: `signals/briefs/<slug>/LIVING_BRIEF.md`.

## Constants

- **Per-run cap**: 8 infographics. Excess slugs are skipped (recoverable — they show up as missing-infographic backfill on the next run).
- **Skill knobs**: layout `bento-grid`, style `craft-handmade`, aspect `landscape`, language `en`. Same every time.

## Steps

1. **Compute today's UTC date** as `<UTC-date>` (format `YYYY-MM-DD`). Used only for logging.

2. **Read `CHANGED_SLUGS`** — the `changed` array from `data/infographic-queue.json` (briefs that gained a genuinely new signal this run, decided by the deterministic pre-step). Do not derive it from a git diff.

   ```
   python3 -c "import json;print('\n'.join(json.load(open('data/infographic-queue.json'))['changed']))"
   ```

3. **Read `MISSING_SLUGS`** — the `missing` array from the same file (briefs with a `LIVING_BRIEF.md` but no `infographic.png`, oldest-first, already de-duped against `changed`):

   ```
   python3 -c "import json;print('\n'.join(json.load(open('data/infographic-queue.json'))['missing']))"
   ```

   If `data/infographic-queue.json` is absent (pre-step didn't run), treat both lists as empty.

4. **If both `CHANGED_SLUGS` and `MISSING_SLUGS` are empty**, write nothing and exit with stdout `no briefs changed today`.

5. **Apply the cap.** `QUEUE = CHANGED_SLUGS + MISSING_SLUGS` (changed-this-run briefs take priority; backfill fills the remaining slots). If `len(QUEUE) > 8`, partition into `KEPT = QUEUE[:8]` and `SKIPPED = QUEUE[8:]`. Otherwise `KEPT = QUEUE` and `SKIPPED = []`. Process only `KEPT` in this run. Log `SKIPPED` in the final stdout (see Step 8) — skipped backfill slugs stay missing and will be picked up on a later run.

6. **For each `slug` in `KEPT`:**

   a. Read `signals/briefs/<slug>/LIVING_BRIEF.md` into a string `brief_md`.

   b. Invoke `creative-baoyu-infographic` on `brief_md`. Use trigger phrasing and pass slug as topic:

      > Create an infographic for topic `<slug>` from the following content. Use layout `bento-grid`, style `craft-handmade`, aspect ratio `landscape`, language `en`. Faithfully preserve the brief's facts; do not summarize away dates, amounts, or named entities. Strip URLs/credentials before they reach the image prompt.
      >
      > Content:
      > ```
      > <brief_md>
      > ```

      The skill creates `infographic/<topic-slug>/` with intermediates and the final `infographic.png`. Accept whatever directory the skill produces.

   c. Find the most-recently-modified `infographic.png` under `infographic/`. If exactly one matches and was modified during this run, copy it to `signals/briefs/<slug>/infographic.png` (overwrite). On error or no match, log `infographic failed for <slug>` and continue.

   d. Leave the `infographic/<...>/` intermediates in place (gitignored, ephemeral runner).

7. **Do not modify LIVING_BRIEF.md.** Synthesis already wrote the `![Infographic](infographic.png)` reference.

8. **Final stdout**: a single line of the form `<G> infographics generated, <F> failed, <K> skipped (cap)`. If `K > 0`, follow with a second line listing the skipped slugs: `skipped: <slug>, <slug>, ...`. Nothing else.

## Constraints

- Only create/overwrite `signals/briefs/<slug>/infographic.png` for `KEPT` slugs. No other files.
- Do not commit — the workflow handles git.
- Do not regenerate infographics for briefs outside `CHANGED_SLUGS` ∪ `MISSING_SLUGS` — a brief that already has its `infographic.png` and didn't change this run is never touched.
- Do not fetch external URLs. Work from the brief markdown alone.
- Do not fabricate facts. The visual must round-trip back to the brief.
- Single-slug failure: log and continue to the next slug.
