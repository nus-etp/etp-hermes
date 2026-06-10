"""Sanity for the per-arm seen-urls dedup files.

The files hold free-form dedup keys (mostly URLs but also `lever://...`,
`mailto:`, `tel:`, occasional headlines). Append-only by design — we only
enforce format hygiene, not URL-ness. `seen-urls.txt` belongs to the
production arm; `v2/seen-urls.txt` to the A/B experiment arm.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize("rel", ["seen-urls.txt", "v2/seen-urls.txt"])
def test_seen_urls_format(signals_dir: Path, rel: str) -> None:
    path = signals_dir / rel
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise AssertionError(f"{rel} is not valid UTF-8: {e}") from None

    bad: list[tuple[int, str, str]] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        if raw == "":
            continue
        if raw != raw.strip():
            bad.append((n, "leading/trailing whitespace", raw))
            continue
        if any(ord(ch) < 32 and ch != "\t" for ch in raw):
            bad.append((n, "control character", raw))
    assert not bad, f"{rel} format issues:\n  - " + "\n  - ".join(
        f"L{n} {why}: {raw!r}" for n, why, raw in bad
    )
