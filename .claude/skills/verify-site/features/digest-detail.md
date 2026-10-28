# Digest detail

Renders one day's `signals/updates/<date>.md` as markdown with older/newer navigation. Source: `src/app/digests/[date]/page.tsx`.

## Sub-features

- Markdown body (company `##` headings, item bullets with source links).
- `← <older date>` / `<newer date> →` links at the bottom (absent at the ends of the range).
- `All digests` back link.

## How to get to it (user POV)

`Daily digests` in the header → click a date card.

## Driving it with chrome-devtools

```bash
.claude/skills/verify-site/scripts/browser.sh "$RUN_DIR" /digests/
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'link "2026-' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools click "$uid" 2>/dev/null
chrome-devtools evaluate_script '() => ({url: location.href, headings: [...document.querySelectorAll("main h2, main h3")].map(h => h.innerText), nav: [...document.querySelectorAll("main nav a")].map(a => a.innerText)})' 2>/dev/null > "$RUN_DIR/evidence/digest-01.json"
uid=$(chrome-devtools take_snapshot 2>/dev/null | grep -m1 'link "← ' | grep -o 'uid=[^ ]*' | cut -d= -f2)
chrome-devtools click "$uid" 2>/dev/null
chrome-devtools evaluate_script '() => location.href' 2>/dev/null >> "$RUN_DIR/evidence/digest-01.json"
chrome-devtools take_screenshot --fullPage true --filePath "$RUN_DIR/evidence/digest-01.png" 2>/dev/null
```

Proof: headings equal `grep -E '^#{2,3} ' signals/updates/<date>.md`; the newest digest shows only `← <prev>`; clicking it lands on `/etp-hermes/digests/<prev>/` where `<prev>` is the next-older file in `signals/updates/`.

## Gotchas

- Dates without an updates file are skipped (no empty days), so "previous" is the previous *file*, not yesterday.
- Company names in a digest are headings, not links to `/c/<slug>/`, unless the markdown itself links them.
