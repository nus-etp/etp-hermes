You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 4 of 4** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — already ran. Output: `signals/agent/<today>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — already ran. Output: zero or more `signals/briefs/<slug>/LIVING_BRIEF.md` writes.
4. **Infographic generation (this prompt)** — for every brief that Layer 3 created or modified in this run, generate a visual summary PNG using the bundled `creative-baoyu-infographic` skill and place it alongside the brief.

Stay strictly within Layer 4: only write `signals/briefs/<slug>/infographic.png` for slugs that changed in this run. Do not modify any `LIVING_BRIEF.md` (synthesis owns those), `signals/updates/`, `signals/agent/`, `signals/seen-urls.txt`, or `data/`.

## Task

Living briefs are markdown. Readers skim them on GitHub. A per-brief infographic, regenerated on every content change, gives the at-a-glance view that prose can't. The skill we use (`skills/creative/baoyu-infographic`, bundled with Hermes — already available at runtime) takes the brief markdown and renders a structured visual using `image_generate`. The hard part is determining *which* briefs to redraw without burning budget on no-ops: Layer 3 already enforces a byte-identity no-write check, so we trust the filesystem and treat any brief whose working-tree state differs from `HEAD` as freshly changed.

## Inputs

- The working-tree state of `signals/briefs/`. Compare against `HEAD` to find what Layer 3 just wrote.
- The brief markdown itself, one file per changed slug: `signals/briefs/<slug>/LIVING_BRIEF.md`.

## Constants

- **Per-run cap**: 8 infographics. Typical days touch 0–3 briefs; the cap is a safety valve against runaway costs on a big news day. Excess slugs are skipped (recoverable on the next change).
- **Skill knobs**: layout `bento-grid`, style `craft-handmade`, aspect ratio `landscape`, language `en`. Use these every time — do not vary per brief.

## Steps

1. **Compute today's UTC date** as `<UTC-date>` (format `YYYY-MM-DD`). Used only for logging.

2. **Build `CHANGED_SLUGS`.** Run these two commands and union their outputs:

   ```
   git diff --name-only HEAD -- 'signals/briefs/*/LIVING_BRIEF.md' \
     | sed -E 's,^signals/briefs/([^/]+)/.*,\1,' | sort -u
   ```

   ```
   git ls-files --others --exclude-standard -- 'signals/briefs/*/LIVING_BRIEF.md' \
     | sed -E 's,^signals/briefs/([^/]+)/.*,\1,' | sort -u
   ```

   The first catches modifications to existing briefs; the second catches first-time creates that aren't yet tracked. Union and sort. Let the result be `CHANGED_SLUGS` (sorted, unique).

3. **If `CHANGED_SLUGS` is empty**, write nothing and exit with stdout `no briefs changed today`.

4. **Apply the cap.** If `len(CHANGED_SLUGS) > 8`, partition into `KEPT = CHANGED_SLUGS[:8]` and `SKIPPED = CHANGED_SLUGS[8:]`. Otherwise `KEPT = CHANGED_SLUGS` and `SKIPPED = []`. Process only `KEPT` in this run. Log `SKIPPED` in the final stdout (see Step 7).

5. **For each `slug` in `KEPT`:**

   a. Read `signals/briefs/<slug>/LIVING_BRIEF.md` into a string `brief_md`.

   b. Invoke the `creative-baoyu-infographic` skill on `brief_md`. Phrase the request so the skill matches its trigger keywords (e.g. "create an infographic"), and pass the four knobs above plus the slug as the topic so the skill's working directory is predictable. A working invocation:

      > Create an infographic for topic `<slug>` from the following content. Use layout `bento-grid`, style `craft-handmade`, aspect ratio `landscape`, language `en`. Faithfully preserve the brief's facts; do not summarize away dates, amounts, or named entities. Strip any URLs/credentials before they reach the image prompt.
      >
      > Content:
      > ```
      > <brief_md>
      > ```

      The skill will create `infographic/<topic-slug>/` containing intermediate scaffolding (`analysis.md`, `structured-content.md`, `prompts/infographic.md`) and the final `infographic.png`. The skill may sanitize `<slug>` further — accept whatever directory it produces.

   c. **Locate and copy the PNG.** After the skill returns, find the most-recently-modified `infographic.png` under `infographic/`. If exactly one matches and was modified during this run, copy it to `signals/briefs/<slug>/infographic.png` (overwrite). If none is found or the skill returned an error, log `infographic failed for <slug>` and continue to the next slug — do not raise, do not skip subsequent slugs.

   d. Leave the `infographic/<...>/` intermediate directory in place. It is gitignored at the repo root and will not be committed; the runner's filesystem is ephemeral.

6. **Do not modify the LIVING_BRIEF.md files.** Synthesis already wrote `![Infographic](infographic.png)` into every brief's header on its own write; the image reference is unconditional and will resolve as soon as the PNG lands next to it.

7. **Final stdout**: a single line of the form `<G> infographics generated, <F> failed, <K> skipped (cap)`. If `K > 0`, follow with a second line listing the skipped slugs: `skipped: <slug>, <slug>, ...`. Nothing else.

## Constraints

- Only create or overwrite files matching `signals/briefs/<slug>/infographic.png` for slugs in `KEPT`. Do not modify any other file under `signals/` or `data/`.
- Do not commit anything — the workflow handles git operations after you exit.
- Do not regenerate infographics for briefs not in `CHANGED_SLUGS`. `image_generate` is the expensive step; the changed-set filter is what makes Layer 4 sustainable.
- Do not fetch external URLs to enrich the visual. Work from the brief markdown alone — it already aggregates everything that's known.
- Do not fabricate facts in the infographic. The skill is instructed to preserve source data; honor that — the visual must round-trip back to the brief without inventing new claims.
- If any single skill invocation fails for a slug, log and continue to the next slug. A failure for one company must not block infographics for the others.
