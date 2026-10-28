# Daily digests index

Grid of every `signals/updates/<date>.md`, newest first, each with an update count and up to 6 company names. Source: `src/app/digests/page.tsx`; data `digests.generated.json`.

## Sub-features

- One card per date: `h`-styled date, `<n> update(s)` badge (= distinct `##`/`###` headings, excluding `Run at …`), preview names joined by ` · ` or `No company updates`.
- Card click → `/digests/<date>/`.

## How to get to it (user POV)

Click `Daily digests` in the header from any page.

## Driving it with chrome-devtools

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'link "Daily digests"' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools click "$uid" 2>/dev/null
chrome-devtools evaluate_script '() => ({url: location.href, cards: [...document.querySelectorAll("main li a")].map(a => ({href: a.getAttribute("href"), text: a.innerText.replace(/\s+/g, " ")})).slice(0, 5), total: document.querySelectorAll("main li a").length})' 2>/dev/null > "$RUN_DIR/evidence/digests-index-01.json"
chrome-devtools take_screenshot --filePath "$RUN_DIR/evidence/digests-index-01.png" 2>/dev/null
```

Oracle: `ls signals/updates/*.md | wc -l` equals `total` and `meta.totalDigests`; the first card is the newest file date.

Proof: URL `/etp-hermes/digests/`, card count equals the oracle, first card date = newest update file, and a sampled card's update count equals the distinct `##`/`###` headings in that file.

## Gotchas

- Only `signals/updates/` is shown; Layer 2 output (`signals/agent/<date>.md`) is not a digest here.
- Dates are file names; a malformed file name still gets a card.
