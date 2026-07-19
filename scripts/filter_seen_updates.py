#!/usr/bin/env python3
"""Deterministic cross-run dedup for the LLM-parse path (post-Layer-1).

Layer 1's ingest session writes ``signals/updates/<UTC-date>.md``. For pages the
deterministic collector couldn't parse (``llm_fetch_required``), per-URL dedup
against ``signals/seen-urls.txt`` relies on the model honouring a ``grep -Fxq``
instruction — and it occasionally leaks a duplicate (the same URL surfaced on two
different days). This script backstops that in code.

The workflow snapshots ``signals/seen-urls.txt`` into ``data/seen-urls-prerun.txt``
*before* the ingest session runs. Afterwards this script reads today's updates
file and removes any item whose link line (exact, stripped) is present in that
snapshot. Emptied company sections lose their heading; if the whole file becomes
item-less it is deleted.

Item format (per the prompts):

    - **<headline>** — <source> · <pubDate>
      <link>

``## Run at <time>`` re-run dividers are preserved even when they carry no items.

Fails open: a missing updates file or missing snapshot is a no-op (exit 0), so
cold-start / local runs don't break. A parse-time crash is left to surface.

Pure stdlib. Idempotent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ITEM_BULLET_RE = re.compile(r"^- \*\*")
HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
RUN_AT_RE = re.compile(r"^## Run at\b", re.IGNORECASE)


def load_seen(snapshot_path: Path) -> set[str]:
    """Load the pre-run seen-urls snapshot as a set of stripped URLs."""
    if not snapshot_path.exists():
        return set()
    return {
        line.strip()
        for line in snapshot_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _item_block_len(lines: list[str], start: int) -> int:
    """Number of lines the item bullet at ``start`` spans.

    The bullet line plus any following indented continuation lines (the link
    line, and any extra indented detail lines of a multi-line item). Stops at
    the next bullet, heading, or blank line.
    """
    n = 1
    j = start + 1
    while j < len(lines):
        nxt = lines[j]
        if not nxt.strip():
            break
        if ITEM_BULLET_RE.match(nxt) or HEADING_RE.match(nxt):
            break
        if nxt[:1] in (" ", "\t"):
            n += 1
            j += 1
            continue
        break
    return n


def _item_link(block: list[str]) -> str:
    """The link of an item block: the first indented continuation line, stripped."""
    for line in block[1:]:
        if line.strip():
            return line.strip()
    return ""


def filter_items(lines: list[str], seen: set[str]) -> tuple[list[str], int]:
    """Drop item blocks whose link is in ``seen``. Returns (kept_lines, removed)."""
    out: list[str] = []
    removed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if ITEM_BULLET_RE.match(line):
            span = _item_block_len(lines, i)
            block = lines[i : i + span]
            if _item_link(block) in seen:
                removed += 1
                i += span
                # Swallow one trailing blank line so gaps don't accumulate.
                if i < len(lines) and lines[i].strip() == "":
                    i += 1
                continue
        out.append(line)
        i += 1
    return out, removed


def prune_empty_headings(lines: list[str]) -> list[str]:
    """Remove company (H2) headings whose section has no items and no sub-headings.

    ``## Run at <time>`` re-run dividers are preserved; the H1 title is kept.
    """
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## ") and not RUN_AT_RE.match(line):
            j = i + 1
            while j < len(lines):
                nm = HEADING_RE.match(lines[j])
                if nm and len(nm.group(1)) <= 2:
                    break
                j += 1
            body = lines[i + 1 : j]
            has_items = any(ITEM_BULLET_RE.match(b) for b in body)
            has_subheadings = any(HEADING_RE.match(b) for b in body)
            if not has_items and not has_subheadings:
                i = j
                if i < len(lines) and lines[i].strip() == "":
                    i += 1
                continue
        result.append(line)
        i += 1
    return result


def process_file(path: Path, seen: set[str]) -> int:
    """Filter one updates file in place. Returns count of removed items.

    Deletes the file if it becomes item-less after filtering.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    filtered, removed = filter_items(lines, seen)
    if not removed:
        return 0
    pruned = prune_empty_headings(filtered)
    if not any(ITEM_BULLET_RE.match(line) for line in pruned):
        path.unlink()
        return removed
    out = "\n".join(pruned)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    path.write_text(out, encoding="utf-8")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repo root (default: parent of this script).",
    )
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date of the updates file to filter (YYYY-MM-DD; default: today).",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Explicit updates file path; overrides --date discovery.",
    )
    parser.add_argument(
        "--seen",
        default=None,
        help="Pre-run seen-urls snapshot (default: <repo>/data/seen-urls-prerun.txt).",
    )
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    updates = (
        Path(args.file)
        if args.file
        else repo / "signals" / "updates" / f"{args.date}.md"
    )
    snapshot = Path(args.seen) if args.seen else repo / "data" / "seen-urls-prerun.txt"

    if not updates.exists():
        print("filter-seen-updates: no updates file; nothing to do")
        return 0
    if not snapshot.exists():
        print("filter-seen-updates: no seen-urls snapshot; nothing to do")
        return 0

    seen = load_seen(snapshot)
    text = updates.read_text(encoding="utf-8")
    kept_before = sum(1 for line in text.splitlines() if ITEM_BULLET_RE.match(line))
    removed = process_file(updates, seen)
    kept = kept_before - removed
    print(f"filter-seen-updates: removed={removed} kept={kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
