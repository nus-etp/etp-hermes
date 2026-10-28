---
name: verify-site
description: Launch and drive the etp-hermes static website (site/apps/web — company directory, living-brief company pages, daily digests) in a headless Chrome to prove a change works the way a visitor sees it on GitHub Pages. Use after editing anything under site/, scripts/build-data.mjs inputs (data/companies.json, signals/briefs, signals/updates), or when asked to screenshot/check the site.
---

# verify-site

The user-facing product is the static Next.js export in `site/apps/web`, deployed by `.github/workflows/pages.yml` to GitHub Pages under the base path `/etp-hermes/`. Everything it shows is baked at build time by `site/apps/web/scripts/build-data.mjs` from `data/companies.json`, `signals/briefs/*/LIVING_BRIEF.md` (+ `infographic.png`) and `signals/updates/*.md`. There is no backend, no auth, no database: a data change is only visible after a rebuild.

Other surfaces (not covered here): the Python pipeline scripts under `scripts/` (verify with `tests/scripts`, `uv run pytest tests/static tests/scripts -q`) and the `hermes -z` layers (need API keys, write to `signals/`; don't run them to verify site changes).

All helpers live in `.claude/skills/verify-site/scripts/` (run from the repo root as below). Each run gets its own `RUN_DIR`; pass it to every later command.

## Launch

```bash
.claude/skills/verify-site/scripts/launch.sh            # pnpm install + full build (~2 min), then serve
.claude/skills/verify-site/scripts/launch.sh --skip-build   # serve the existing site/apps/web/out as-is
```

It prints:

```
RUN_DIR=$TMPDIR/verify-etp-hermes-site/<utc-stamp>-<pid>
BASE_URL=http://127.0.0.1:<free port>/etp-hermes
```

What it does: `pnpm --filter @acme/web build` (runs `build-data.mjs` → `next build` static export to `site/apps/web/out`), copies `out/` into `RUN_DIR/www/etp-hermes/` (an immutable snapshot, so a later rebuild can't change what you're driving), and starts `python3 -m http.server` on a free 127.0.0.1 port. Ready = the index HTML contains `companies tracked`; it exits non-zero otherwise.

- Use `--skip-build` only when you know `out/` reflects your change. After editing site code or `data/`/`signals/`, do the full build — `next dev` is *not* used because it skips the static export and basePath behaviour Pages depends on.
- `site/package.json` asks for Node 24; Node 26 builds fine with a warning.
- Isolation: any number of runs can be served side by side (separate ports and snapshots). Builds are **not** isolated — two concurrent builds write the same `out/` and `src/data/*.generated.json`. Don't build while another agent builds.

## Doctor

Run first, and whenever anything looks off:

```bash
.claude/skills/verify-site/scripts/doctor.sh "$RUN_DIR"
```

Read-only. Checks: server pid alive, port owned by that pid, index shows `<totalCompanies> companies tracked` matching the snapshot's `meta.json`, served HTML references the snapshot's build id, a `/etp-hermes/_next/static/chunks/*.js` chunk returns 200 (else nothing hydrates — the directory filters would be dead). Also prints the git HEAD the snapshot was built from vs. the current HEAD (if they differ, you're driving stale code — relaunch) and whether the `chrome-devtools` daemon is ours. Exit 0 = worth driving.

## Drive

Harness: the `chrome-devtools` CLI (`/opt/homebrew/bin/chrome-devtools`, chrome-devtools-mcp 1.3.0), headless.

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /            # open BASE_URL/ ; any path, e.g. /c/carousell/ or /digests/
chrome-devtools take_snapshot 2>/dev/null                              # a11y tree with uids
chrome-devtools fill <uid> "quantum" 2>/dev/null
chrome-devtools click <uid> 2>/dev/null
chrome-devtools navigate_page --type url --url "$(cat "$RUN_DIR/base_url")/digests/" 2>/dev/null
chrome-devtools evaluate_script '() => location.href' 2>/dev/null
chrome-devtools take_screenshot --fullPage true --filePath "$RUN_DIR/evidence/<name>.png" 2>/dev/null
```

- `browser.sh` starts the daemon (`start --headless --isolated`) only if none is running and marks it as ours; it opens the page in an isolated browser context named after the run. If doctor says the daemon was **not** started by this run, someone else owns it: don't `chrome-devtools start`/`stop` it, just use `browser.sh` (new page) and close your page when done.
- uids (`uid=1_26`) change on every snapshot. Find them by stable labels, then act:
  `uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'searchbox "Search companies' | grep -o 'uid=[^ ]*' | cut -d= -f2)`
- Stable handles in this app: searchbox `aria-label="Search companies, aliases, descriptions"`; switch `"Living brief available"` (`role=switch`, `aria-checked`); filter chips are `button`s with `aria-pressed` inside `<fieldset>`s with legends `Sector`/`Region`/`Funding stage`/`Source type`; results region `section[aria-label="Company directory"]` (count text in its first `<p>`, cards are `article h3`); sort `<select>` labelled `Sort`; nav links `Directory` / `Daily digests`; back links `Back to directory` / `All digests`.
- Every directory filter is mirrored into the URL query (`?q=`, `brief=1`, `sector=`, `region=`, `stage=`, `source=`, `sort=`), so asserting `location.href` + the count text is the cheapest state check.
- Every internal URL ends with `/` (`trailingSlash: true`) and lives under `/etp-hermes/`.
- Independent oracle: `site/apps/web/src/data/*.generated.json` is what the build baked in. Compute the expected result from it (python one-liner) and compare with what the page shows — don't just trust the page.
- Strip the CLI noise with `2>/dev/null` (Node `ExperimentalWarning`, "Update available").

## Evidence

Write everything to `$RUN_DIR/evidence/` (survives cleanup). For a proof:

- Screenshot **before** and **after** the action (`take_screenshot --filePath "$RUN_DIR/evidence/<feature>-NN-<step>.png"`), plus the `evaluate_script` JSON of the resulting state (URL, count text, card names) saved alongside (`> "$RUN_DIR/evidence/<feature>-NN.json"`).
- Drive the real visitor path: type in the search box, click chips/links. Don't hand-craft query strings as the action under test (deep-linking is fine as a *separate* check of URL-state restore).
- Cross-check against the generated JSON oracle, and for data/brief changes also confirm the input made it into the build: `grep` the changed text in `site/apps/web/src/data/*.generated.json` and in `RUN_DIR/www/etp-hermes/<route>/index.html`.
- Check `chrome-devtools list_console_messages` for hydration errors and `list_network_requests` for 404s (infographic PNGs, chunks) on the page you proved.
- `--skip-build` is a test-mode shortcut: it proves nothing about your change unless you rebuilt. Say which you used.

## Cleanup

```bash
.claude/skills/verify-site/scripts/cleanup.sh "$RUN_DIR"
```

Kills only the `http.server` pid this run recorded, stops the chrome-devtools daemon only if this run started it, deletes `RUN_DIR/www` (the ~90MB snapshot). Keeps `RUN_DIR/evidence/` and lists it. If you opened pages on a daemon you don't own, `chrome-devtools close_page <pageId>` them before cleanup. Never `pkill` by name. The build outputs in `site/apps/web/{out,public/briefs,src/data/*.generated.json}` are gitignored; leave them.

## Feature map

`features/README.md` — one file per user-facing feature with how to reach it, how to drive it, and the end state that proves it. A proof that touches one entry point is incomplete when the map lists others for the changed feature.
