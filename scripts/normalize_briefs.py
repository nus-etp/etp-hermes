#!/usr/bin/env python3
"""Normalize the signal sections of every signals/briefs/*/LIVING_BRIEF.md.

The living briefs are written by the Layer 3 synthesis agent, and their
formatting invariants drift (out-of-order bullets, stale items lingering in
Recent, inconsistent `_none_` placeholders). Mechanics belong in deterministic
scripts, so this post-step enforces them:

  1. `## Recent signals` and `## Older signals` bullet blocks (a dated
     `- **YYYY-MM-DD** — ...` line plus its indented sub-bullets) are sorted
     descending by date. Undated/unparseable blocks keep their relative order
     and sink below dated ones — nothing is ever dropped.
  2. Recent blocks older than the cutoff (default: 30 days before today,
     tunable via --days / --today) rotate into Older.
  3. The `_none_` placeholder is added to a section that empties out and
     removed from one that gains items.

Only the two signal sections are touched; everything else in the file
(header, Thesis, Profile, Funding history, Open questions) is preserved
byte-for-byte. Each block's internal content is preserved byte-for-byte too —
only block *order* and inter-block blank lines are normalized. An aggregate
`### ` subsection (e.g. the synthesis template's `### Hiring` at the end of
Recent signals) is preserved verbatim in place — never sorted or rotated.

Idempotent: a second run produces identical output, and files that need no
change are not rewritten (mtimes/git stay clean). Fails open per file: a
brief whose signal sections can't be parsed structurally is skipped with a
warning on stderr and the run still exits 0.

Usage:
  python3 scripts/normalize_briefs.py [--today YYYY-MM-DD] [--days N] [--check]
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEFS_DIR = REPO_ROOT / "signals" / "briefs"
DEFAULT_DAYS = 30

RECENT_HEADING = "## Recent signals"
OLDER_HEADING = "## Older signals"
PLACEHOLDER = "_none_"

# Full date at the head of a signal bullet. Partial dates (`**2021-05**`,
# `**2014**`) belong to Funding history — which this script never touches —
# and `**date unknown**` bullets are treated as undated, so neither needs
# matching here.
BULLET_DATE_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\*")


class UnparseableBrief(Exception):
    """Raised when a brief's structure can't be parsed safely."""


def block_date(block: list[str]) -> dt.date | None:
    """Date of a bullet block, or None for undated/unparseable dates."""
    m = BULLET_DATE_RE.match(block[0])
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def parse_section(lines: list[str], heading: str) -> list[list[str]]:
    """Parse a signal section body into bullet blocks.

    A block is a top-level `- ` bullet line plus every following indented
    line (blank lines are kept inside a block only when more indented
    content follows). Blank separator lines and the `_none_` placeholder
    are consumed; any other top-level content is a structural error.
    """
    blocks: list[list[str]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.strip() == PLACEHOLDER:
            i += 1
            continue
        if line.startswith("- "):
            block = [line]
            i += 1
            while i < n:
                cur = lines[i]
                if not cur.strip():
                    # Attach blank lines only if the block continues after them.
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and lines[j][:1] in (" ", "\t"):
                        block.extend(lines[i:j])
                        i = j
                        continue
                    break
                if cur[:1] in (" ", "\t"):
                    block.append(cur)
                    i += 1
                    continue
                break
            blocks.append(block)
            continue
        raise UnparseableBrief(f"stray content in {heading!r}: {line!r}")
    return blocks


def sort_blocks(blocks: list[list[str]]) -> list[list[str]]:
    """Dated blocks descending by date (stable); undated sink below, in order."""
    dated = [(d, b) for b in blocks if (d := block_date(b)) is not None]
    undated = [b for b in blocks if block_date(b) is None]
    dated.sort(key=lambda pair: pair[0], reverse=True)  # list.sort is stable
    return [b for _, b in dated] + undated


def split_subsection(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split a section body at the first `### ` heading.

    The synthesis template ends Recent signals with an aggregate subsection
    (`### Hiring`); everything from the heading on is a verbatim tail that
    is re-emitted after the sorted bullets and never rotates.
    """
    for i, line in enumerate(lines):
        if line.startswith("### "):
            return lines[:i], lines[i:]
    return lines, []


def _trim_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def render_body(blocks: list[list[str]], tail: list[str]) -> list[str]:
    tail = _trim_blank_edges(tail)
    if not blocks and not tail:
        return [PLACEHOLDER]
    out = [line for block in blocks for line in block]
    if tail:
        if out:
            out.append("")
        out.extend(tail)
    return out


def normalize_text(text: str, today: dt.date, days: int) -> tuple[str, int]:
    """Return (normalized text, blocks rotated Recent→Older).

    Raises UnparseableBrief when the signal sections can't be located or
    parsed — callers must skip the file untouched.
    """
    lines = text.splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]

    recent_idx = [i for i in headings if lines[i] == RECENT_HEADING]
    older_idx = [i for i in headings if lines[i] == OLDER_HEADING]
    if len(recent_idx) != 1 or len(older_idx) != 1:
        raise UnparseableBrief("expected exactly one Recent and one Older signals heading")
    ri, oi = recent_idx[0], older_idx[0]
    following = [i for i in headings if i > ri]
    if not following or following[0] != oi:
        raise UnparseableBrief("Older signals must be the next section after Recent signals")
    after_older = [i for i in headings if i > oi]
    suffix_start = after_older[0] if after_older else len(lines)

    recent_lines, recent_tail = split_subsection(lines[ri + 1 : oi])
    older_lines, older_tail = split_subsection(lines[oi + 1 : suffix_start])
    recent = parse_section(recent_lines, RECENT_HEADING)
    older = parse_section(older_lines, OLDER_HEADING)

    cutoff = today - dt.timedelta(days=days)
    moved = [b for b in recent if (d := block_date(b)) is not None and d < cutoff]
    recent = sort_blocks([b for b in recent if b not in moved])
    older = sort_blocks(moved + older)

    out = lines[:ri]
    out.append(RECENT_HEADING)
    out.extend(render_body(recent, recent_tail))
    out.append("")
    out.append(OLDER_HEADING)
    out.extend(render_body(older, older_tail))
    if suffix_start < len(lines):
        out.append("")
        out.extend(lines[suffix_start:])
    return "\n".join(out) + "\n", len(moved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--briefs-dir",
        default=str(BRIEFS_DIR),
        help="Directory containing <slug>/LIVING_BRIEF.md trees (default: signals/briefs).",
    )
    parser.add_argument(
        "--today",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date used as 'today' for the rotation cutoff (default: today).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Recent blocks older than this many days rotate to Older (default: {DEFAULT_DAYS}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry run: report what would change without rewriting any file.",
    )
    args = parser.parse_args()

    today = dt.date.fromisoformat(args.today)
    briefs = sorted(Path(args.briefs_dir).glob("*/LIVING_BRIEF.md"))

    changed = unchanged = skipped = 0
    for path in briefs:
        slug = path.parent.name
        original = path.read_text(encoding="utf-8")
        try:
            normalized, moved = normalize_text(original, today, args.days)
        except UnparseableBrief as exc:
            print(f"normalize_briefs: SKIP {path}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        if normalized == original:
            unchanged += 1
            continue
        changed += 1
        details = f"rotated {moved} to Older" if moved else "resorted/reformatted signals"
        verb = "would change" if args.check else "changed"
        print(f"normalize_briefs: {slug}: {verb} ({details})")
        if not args.check:
            path.write_text(normalized, encoding="utf-8")

    print(
        f"normalize_briefs: {changed} changed, {unchanged} unchanged, "
        f"{skipped} skipped of {len(briefs)} briefs"
        + (" (dry run)" if args.check else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
