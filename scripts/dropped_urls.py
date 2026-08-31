#!/usr/bin/env python3
"""Shared reader for `signals/dropped-urls.txt` — the *expiring* dedup list.

`signals/seen-urls.txt` is the permanent record of keys we **published** (or
deterministically excluded). Keys the Layer 1/2 relevance pass *dropped* go
here instead, stamped with the UTC day of the drop, so a false-negative drop
stops blocking the item once its TTL lapses and the item can be re-judged.

Line format (tab-separated, one entry per line)::

    YYYY-MM-DD<TAB><dedup_key>

Lines starting with ``#`` are comments and are preserved by :func:`prune_file`.
Anything else that doesn't parse (no tab, unparseable date) is treated as
**non-blocking** — fail open toward recall: a malformed line must never
silently suppress an item forever.

TTL defaults to 30 days, overridable with ``DROPPED_URL_TTL_DAYS``.

Used by:
- ``scripts/collect-candidates.py`` — dedup + a prune pass at the start of the
  run, so the on-disk file the Layer 1 prompt greps holds only live entries.
- ``scripts/filter_seen_updates.py`` — the post-Layer-1 deterministic backstop.

Pure stdlib. Importable as a sibling module (``import dropped_urls``); callers
insert ``scripts/`` on ``sys.path`` so the import also resolves under pytest's
file-path module loader.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

DEFAULT_TTL_DAYS = 30
TTL_ENV = "DROPPED_URL_TTL_DAYS"


def ttl_days(default: int = DEFAULT_TTL_DAYS) -> int:
    """TTL in days from the environment, falling back to ``default``.

    A non-integer or negative value falls back too — a broken override must not
    silently turn the list into a permanent blocklist.
    """
    raw = os.environ.get(TTL_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def parse_line(line: str) -> tuple[dt.date, str] | None:
    """Parse one ``YYYY-MM-DD<TAB><key>`` line. ``None`` when malformed."""
    if not line or line.lstrip().startswith("#"):
        return None
    date_part, tab, key_part = line.partition("\t")
    if not tab:
        return None
    key = key_part.strip()
    if not key:
        return None
    try:
        day = dt.date.fromisoformat(date_part.strip())
    except ValueError:
        return None
    return day, key


def is_expired(day: dt.date, today: dt.date, ttl: int) -> bool:
    """True once ``ttl`` days have elapsed since the drop.

    A same-day drop blocks; a drop exactly ``ttl`` days old no longer does.
    Future-dated entries (clock skew) block, which is the conservative side.
    """
    return (today - day).days >= ttl


def load_active(
    path: Path, ttl: int | None = None, today: dt.date | None = None
) -> set[str]:
    """Dedup keys dropped within the TTL. Missing file → empty set (fail open)."""
    if not path.exists():
        return set()
    ttl = ttl_days() if ttl is None else ttl
    today = today or dt.datetime.now(dt.timezone.utc).date()
    active: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(raw)
        if parsed is None:
            continue  # malformed → non-blocking
        day, key = parsed
        if not is_expired(day, today, ttl):
            active.add(key)
    return active


def prune_file(
    path: Path, ttl: int | None = None, today: dt.date | None = None
) -> int:
    """Rewrite ``path`` without expired/malformed entries. Returns lines removed.

    Comment lines are kept. Keeping the on-disk file free of expired entries is
    what lets the prompts do a plain membership check (no date arithmetic in
    shell) and still honour the TTL. No-ops when nothing changes, so an
    unchanged file never shows up as a spurious diff.
    """
    if not path.exists():
        return 0
    ttl = ttl_days() if ttl is None else ttl
    today = today or dt.datetime.now(dt.timezone.utc).date()
    text = path.read_text(encoding="utf-8")
    kept: list[str] = []
    removed = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            kept.append(raw)
            continue
        parsed = parse_line(raw)
        if parsed is None or is_expired(parsed[0], today, ttl):
            removed += 1
            continue
        kept.append(f"{parsed[0].isoformat()}\t{parsed[1]}")
    if not removed:
        return 0
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def format_line(key: str, day: dt.date | None = None) -> str:
    """Render one append-ready line (no trailing newline)."""
    day = day or dt.datetime.now(dt.timezone.utc).date()
    return f"{day.isoformat()}\t{key}"
