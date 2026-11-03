# Company page

One statically generated page per watchlist company. Source: `src/app/c/[slug]/page.tsx`, `src/app/_components/markdown.tsx`; data from `briefs.generated.json`, `companies.generated.json`, `funding.generated.json`, `sources.generated.json`.

## Sub-features

- Header: `h1` company name, `Living brief` badge (when `hasBrief`), `aka <aliases>`, description, fact pills (Sector, Region, Latest funding, Latest signal).
- With a brief: infographic `<img alt="<Name> infographic">` from `/hermenaut/briefs/<slug>/infographic.png`, then the rendered `LIVING_BRIEF.md` starting at `## Thesis` (title, `_Last updated:_` and the embedded infographic line are stripped).
- Without a brief: `Sources` pill links (external, `target=_blank`) and a `Funding history` table (Date/Stage/Amount/Investors).
- `Back to directory` link.

## How to get to it (user POV)

Click any card in the directory (`/`); digests do not link to company pages. Brief example: `horizon-quantum-computing`; no-brief examples with both sources and funding rows: `cellivate-technologies`, `curated-culture`, `enlipsium` (a no-brief company with neither, e.g. `1cool`, renders an empty body and proves nothing).

## Driving it with chrome-devtools

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'searchbox "Search companies' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools fill "$uid" "Horizon Quantum" 2>/dev/null
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'link "Horizon Quantum Computing' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools click "$uid" 2>/dev/null
chrome-devtools evaluate_script '() => { const img = document.querySelector("main img"); return {url: location.href, h1: document.querySelector("h1")?.innerText, h2s: [...document.querySelectorAll("main h2")].map(h => h.innerText), img: img && {src: img.src, loaded: img.complete && img.naturalWidth > 0}} }' 2>/dev/null > "$RUN_DIR/evidence/company-01.json"
chrome-devtools take_screenshot --fullPage true --filePath "$RUN_DIR/evidence/company-01-horizon.png" 2>/dev/null
```

The image is `loading="lazy"`: scroll it into view (`evaluate_script '() => document.querySelector("main img").scrollIntoView()'`) before asserting `loaded`.

Proof: URL is `/hermenaut/c/<slug>/`, `h1` equals the company name, for a brief page the `h2`s match the `## ` headings of `signals/briefs/<slug>/LIVING_BRIEF.md` (Thesis, …, Funding history, Open questions) and the infographic loaded (`naturalWidth > 0`, no 404 in `list_network_requests`); for a no-brief page the `Sources`/`Funding history` sections match `sources.generated.json` / `funding.generated.json` for that slug. Proved 2026-11-03: Horizon Quantum Computing (6 brief `h2`s, infographic loaded, `Back to directory` → `/hermenaut/`), Cellivate Technologies (`Sources` + `Funding history`, 4 rows, no `<img>`).

## Gotchas

- Slug is `toSlug(name)` from `build-data.mjs` (lowercase, NFKD, non-alnum → `-`); it is not the brief directory name if those ever diverge — check `companies.generated.json`.
- A brief whose `infographic.png` is missing still renders the `<img>` (404). That is the documented Layer 3/4 decoupling, not a site bug — but report it.
- An unknown slug is a build-time 404 (`notFound()`), served as `404.html` by Pages; `http.server` returns its own 404 instead, so don't assert on the 404 page body locally.
- Brief markdown is rendered with `react-markdown` + `remark-gfm` (no `rehype-raw`).
- There is no per-company `<title>`: every company page inherits the layout's `hermenaut`.
