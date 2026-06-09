#!/usr/bin/env python3
"""Strip trailing whitespace from signals/seen-urls.txt dedup keys.

The hermes agent occasionally appends a dedup key with a trailing space
(e.g. ``mailto:contact@example.com ``), which trips test_seen_urls_format
in the static eval suite. The commit job runs this before staging so the
eval doesn't break on the next run. Idempotent and fails open: a missing
file is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_PATH = Path("signals/seen-urls.txt")


def sanitize(path: Path) -> bool:
    """Strip trailing whitespace from each line. Returns True if changed."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    # splitlines() drops the line terminators; rejoin with "\n" and preserve a
    # single trailing newline so the file stays POSIX-clean.
    cleaned = "\n".join(line.rstrip() for line in text.splitlines())
    if text.endswith("\n"):
        cleaned += "\n"
    if cleaned == text:
        return False
    path.write_text(cleaned, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    changed = sanitize(path)
    print(f"{'stripped trailing whitespace in' if changed else 'no changes to'} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
