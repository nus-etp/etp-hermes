"""Sanity for signals/seen-urls.txt.

The file holds free-form dedup keys (mostly URLs but also `lever://...`,
`mailto:`, `tel:`, occasional headlines). Append-only by design — we only
enforce format hygiene, not URL-ness.
"""

from __future__ import annotations

from pathlib import Path


def test_seen_urls_format(signals_dir: Path) -> None:
    path = signals_dir / "seen-urls.txt"
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise AssertionError(f"seen-urls.txt is not valid UTF-8: {e}") from None

    bad: list[tuple[int, str, str]] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if raw == "":
            continue
        if raw != raw.strip():
            bad.append((n, "leading/trailing whitespace", raw))
            continue
        if any(ord(ch) < 32 and ch != "\t" for ch in raw):
            bad.append((n, "control character", raw))
    assert not bad, "seen-urls.txt format issues:\n  - " + "\n  - ".join(
        f"L{n} {why}: {raw!r}" for n, why, raw in bad
    )
