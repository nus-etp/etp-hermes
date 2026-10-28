# Company directory

The home page: hero stats (Companies / Living briefs / Daily digests) and a client-side filterable grid of every watchlist company. Source: `src/app/page.tsx`, `src/app/_components/company-directory.tsx`, `company-card.tsx`.

## Sub-features

- Free-text search over name + aliases + description (active at ≥2 chars, case-insensitive substring); matches highlighted with `<mark>` in cards; `Clear search` (×) button.
- `Living brief available` switch → `?brief=1`.
- Multi-select chip filters: Sector, Region, Funding stage, Source type → `?sector=`, `?region=`, `?stage=`, `?source=` (comma-joined). Within a group = OR, across groups = AND.
- Sort select: Relevance (brief first, then latest signal, then funding, then name), A–Z, Latest funding, Latest signal, Last updated → `?sort=`.
- Count line `<n> of <total> companies · <k> filter(s) applied`; `Clear all filters`; empty state `No matches` with `Reset filters`.
- URL-state restore: loading `/?q=…&sector=…` reproduces the filtered view.
- Card click → `/c/<slug>/`.

## How to get to it (user POV)

Open the site root, or click `Directory` / the `etp-hermes` logo in the header from any page.

## Driving it with chrome-devtools

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /
E="$RUN_DIR/evidence"
chrome-devtools take_screenshot --filePath "$E/directory-01-before.png" 2>/dev/null
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'searchbox "Search companies' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools fill "$uid" "quantum" 2>/dev/null
chrome-devtools evaluate_script '() => ({url: location.href, count: document.querySelector("section[aria-label=\"Company directory\"] p").innerText, cards: [...document.querySelectorAll("section[aria-label=\"Company directory\"] article h3")].map(h => h.innerText), marks: document.querySelectorAll("mark").length})' 2>/dev/null > "$E/directory-02.json"
chrome-devtools take_screenshot --filePath "$E/directory-02-search-quantum.png" 2>/dev/null
```

Chip / switch: `grep -m1 'button "Singapore"'` or `grep -m1 'switch "Living brief available"'` in the snapshot, then `chrome-devtools click <uid>`. Sort: `grep -m1 'combobox'` then `chrome-devtools fill <uid> "A–Z"` (option label).

Oracle (expected search hits):

```bash
python3 -c "import json,sys; q=sys.argv[1].lower(); c=json.load(open('site/apps/web/src/data/companies.generated.json')); print(len(c), sorted(x['name'] for x in c if q in (x['name']+' '+' '.join(x['aliases'])+' '+x['description']).lower()))" quantum
```

Proof: URL gained `?q=quantum`, the count line reads `<oracle count> of <total> companies · 1 filter applied`, card names equal the oracle set (order follows the sort), and `mark` elements exist. Proved 2026-10-28: 4 of 488 — pQCee, Horizon Quantum Computing, Qarbotech, Qubit Dynamics.

## Gotchas

- One-character queries are ignored by design (`ql.length >= 2`); they don't count as a filter.
- Filter state lives only in the URL (nuqs); there is no localStorage. A reload keeps it, navigating to `/` clears it.
- Chip option lists are derived from the data at build time; a sector string with a typo shows up as its own chip, not as a bug in the UI.
- The directory is inside `<Suspense>`: a static HTML fetch (curl) shows `Loading directory…` only — directory behaviour must be proved in the browser, after hydration.
- `filtered.length` text is in a `<span>` followed by ` of …`; read the whole `<p>` innerText, not the span.
