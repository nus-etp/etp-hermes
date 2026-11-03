# Metrics dashboard

A sortable, filterable table of hard public numbers per company, ranked by default on a composite score. Source: `src/app/metrics/page.tsx`, `src/app/_components/metrics-table.tsx`; data `metrics.generated.json` (built in `build-data.mjs`).

## Sub-features

- Six summary tiles over the visible rows: Disclosed funding, Open roles, GitHub stars, Median age, Investor-listed, Signals (90d).
- Columns: `#` (rank), then sortable header buttons `Company`, `Composite`, `Disclosed funding`, `Rounds`, `Latest round`, `Open roles`, `GitHub ★`, `HN 30d`, `Signals 90d`, `Age`, `Headcount`, `Investor lists`.
- Default sort: `Composite` descending. Composite = mean percentile (among companies with a value > 0) of funding, open roles, stars, HN 30d, headcount floor and investor listings, ×100, 1 decimal; a missing metric counts as 0. Percentiles are over all companies, inactive included, so filters never change a score.
- Clicking the active header flips direction (`?dir=asc|desc`); a new header switches to it (`?sort=<key>`, `dir` dropped; default direction asc for `Company`, desc otherwise). Active `<th>` carries `aria-sort`.
- Blank cells mean no data and sort last in both directions; `#` is blank on those rows.
- HN counts for generic names (`ambiguousName`) are struck through (`td.line-through`), excluded from the HN sort and from the composite.
- Search box `Search companies` (name only, ≥2 chars) → `?q=`; `Country` select → `?country=`; `Include struck-off` checkbox → `?inactive=1` (inactive companies are hidden by default).
- Count line `<n> of <total> · data as of <asOf>`.

## How to get to it (user POV)

Click `Metrics` in the header from any page.

## Driving it with chrome-devtools

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /
E="$RUN_DIR/evidence"
uid() { chrome-devtools take_snapshot 2>/dev/null | grep -m1 "$1" | grep -o 'uid=[^ ]*' | cut -d= -f2; }
chrome-devtools click "$(uid 'link "Metrics"')" 2>/dev/null
J='() => ({url: location.pathname+location.search, sort: [...document.querySelectorAll("th[aria-sort]")].filter(t=>t.getAttribute("aria-sort")!=="none").map(t=>t.innerText+":"+t.getAttribute("aria-sort")), count: [...document.querySelectorAll("p")].find(p=>p.innerText.includes("data as of"))?.innerText, top5: [...document.querySelectorAll("tbody tr")].slice(0,5).map(r=>[r.cells[1].querySelector("a")?.innerText, r.cells[2].innerText.trim()]), struck: document.querySelectorAll("tbody td.line-through").length})'
chrome-devtools evaluate_script "$J" 2>/dev/null > "$E/metrics-01-default.json"
chrome-devtools take_screenshot --filePath "$E/metrics-01-default.png" 2>/dev/null
chrome-devtools click "$(uid 'button "HN 30d"')" 2>/dev/null
chrome-devtools evaluate_script "$J" 2>/dev/null > "$E/metrics-02-hn.json"
chrome-devtools click "$(uid 'checkbox "Include struck-off"')" 2>/dev/null
chrome-devtools evaluate_script "$J" 2>/dev/null > "$E/metrics-03-inactive.json"
```

Search / country: `chrome-devtools fill "$(uid 'searchbox "Search companies"')" quantum`, `chrome-devtools fill "$(uid 'combobox "Country"')" Singapore`.

Oracle (independent re-implementation of the composite):

```bash
python3 -c "
import json;d=json.load(open('site/apps/web/src/data/metrics.generated.json'));C=d['companies']
M=[lambda c:c.get('fundingUsd'),lambda c:c.get('jobsOpen'),lambda c:c.get('githubStars'),lambda c:None if c.get('ambiguousName') else c.get('hn30d'),lambda c:c.get('employeesFloor'),lambda c:len(c['backers'])]
def pr(v):
 p=sorted(x for x in v if isinstance(x,(int,float)) and x>0);n=len(p);f={};l={}
 for i,x in enumerate(p): f.setdefault(x,i);l[x]=i
 return [((f[x]+l[x]+2)/2/n if isinstance(x,(int,float)) and x>0 else 0) for x in v]
R=[pr([m(c) for c in C]) for m in M]
S=[(round(sum(r[i] for r in R)/6*1000)/10,sum(r[i]>0 for r in R),c) for i,c in enumerate(C)]
V=[s for s in S if not s[2]['inactive']];top=sorted([s for s in V if s[1]],key=lambda s:(-s[0],s[2]['name']))
print(len(V),'of',len(C),'| top5',[(s[2]['name'],s[0]) for s in top[:5]],'| struck',sum(1 for c in C if not c['inactive'] and c.get('ambiguousName')))
print('hn top3',[c['name'] for c in sorted([c for c in C if not c['inactive'] and not c.get('ambiguousName') and c.get('hn30d') is not None],key=lambda c:(-c['hn30d'],c['name']))[:3]])"
```

Proof: default load shows `Composite:descending`, count `<visible> of <total>`, top-5 names and scores equal the oracle, struck count equals the oracle; after `HN 30d` the URL is `?sort=hn` and the top rows equal the oracle's HN order; after `Include struck-off` the URL gains `inactive=1` and the count is `<total> of <total>`. Proved 2026-11-03: `474 of 515 · data as of 2026-11-03`, Patsnap 55.7 / BEEX 36.5 / Carousell 31.7 / pQCee 30.8 / Constructor.io 29.1, 50 struck; HN top remarkably / Snowball / Librarian; second click `?sort=hn&dir=asc`; struck-off on → `515 of 515`, 56 struck; `quantum` + `Singapore` → `?q=quantum&country=Singapore`, `1 of 515`, Horizon Quantum Computing.

## Gotchas

- The table is inside `<Suspense>` (`Loading metrics…`); prove it in the browser after hydration, not with curl.
- Search matches the company name only (unlike the directory, which also searches aliases and descriptions).
- An HN value of `0` displays as `–` but is still a value: it ranks (and sorts first ascending) rather than sinking with the blanks.
- Name ties: JS uses `localeCompare`, Python sorts by code point; compare as sets when values tie.
- The header's `<n> companies tracked` comes from `meta.totalCompanies`, not the metrics count.
