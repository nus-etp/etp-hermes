You are running as a non-interactive agent inside a GitHub Actions runner. Your working directory is the etp-hermes repo root. All paths below are relative to that.

This is **Layer 4 of 4** in the daily pipeline:
1. **Data ingestion** (`prompts/ingest.md`) — already ran. Output: `signals/updates/<today>.md`.
2. **Agent supplement** (`prompts/agent_supplement.md`) — already ran. Output: `signals/agent/<today>.md`.
3. **Synthesis** (`prompts/synthesis.md`) — already ran. Output: zero or more `signals/briefs/<slug>/LIVING_BRIEF.md` writes.
4. **Infographic generation (this prompt)** — for every brief that Layer 3 created or modified in this run, generate a visual summary PNG using the bundled `creative-baoyu-infographic` skill and place it alongside the brief.

Stay strictly within Layer 4: only write `signals/briefs/<slug>/infographic.png` for slugs that changed in this run. Do not modify any `LIVING_BRIEF.md` (synthesis owns those), `signals/updates/`, `signals/agent/`, `signals/seen-urls.txt`, or `data/`.

## Task

For every brief Layer 3 just created or modified, regenerate `infographic.png` next to it via the bundled `creative-baoyu-infographic` skill. Use the working-tree-vs-`HEAD` diff to detect what changed (Layer 3's no-write check guarantees only real content edits show up).

## Inputs

- The working-tree state of `signals/briefs/`. Compare against `HEAD` to find what Layer 3 just wrote.
- The brief markdown itself, one file per changed slug: `signals/briefs/<slug>/LIVING_BRIEF.md`.

## Constants

- **Per-run cap**: 8 infographics. Excess slugs are skipped (recoverable next change).
- **Skill knobs**: layout `bento-grid`, style `craft-handmade`, aspect `landscape`, language `en`. Same every time.

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

   First catches modifications; second catches untracked creates. Union, sort, dedupe → `CHANGED_SLUGS`.

3. **If `CHANGED_SLUGS` is empty**, write nothing and exit with stdout `no briefs changed today`.

4. **Apply the cap.** If `len(CHANGED_SLUGS) > 8`, partition into `KEPT = CHANGED_SLUGS[:8]` and `SKIPPED = CHANGED_SLUGS[8:]`. Otherwise `KEPT = CHANGED_SLUGS` and `SKIPPED = []`. Process only `KEPT` in this run. Log `SKIPPED` in the final stdout (see Step 7).

5. **For each `slug` in `KEPT`:**

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

6. **Do not modify LIVING_BRIEF.md.** Synthesis already wrote the `![Infographic](infographic.png)` reference.

7. **Final stdout**: a single line of the form `<G> infographics generated, <F> failed, <K> skipped (cap)`. If `K > 0`, follow with a second line listing the skipped slugs: `skipped: <slug>, <slug>, ...`. Nothing else.

## Constraints

- Only create/overwrite `signals/briefs/<slug>/infographic.png` for `KEPT` slugs. No other files.
- Do not commit — the workflow handles git.
- Do not regenerate infographics for briefs outside `CHANGED_SLUGS`.
- Do not fetch external URLs. Work from the brief markdown alone.
- Do not fabricate facts. The visual must round-trip back to the brief.
- Single-slug failure: log and continue to the next slug.
