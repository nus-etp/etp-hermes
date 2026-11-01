#!/usr/bin/env python3
"""Select which briefs get a fresh infographic (Layer 4) this run.

Layer 4 used to regenerate `infographic.png` for *every* brief whose
`LIVING_BRIEF.md` differed from HEAD. That over-fires: between Layer 3 and
Layer 4 the brief is rewritten by `normalize_briefs.py` (reordering Recent
signals, rotating stale items into Older) and by synthesis itself (bumping the
`_Last updated:_` line, appending an "Also reported by:" source to an *Older*
signal, retiring an answered open question). None of those change what the
infographic depicts, yet each burned a ~1.3MB image regeneration.

This deterministic pre-step narrows the queue to briefs carrying a *genuinely
new signal*: a dated signal bullet — in either the Recent or Older section —
whose identity (date + headline, corroboration stripped) is absent from the
committed brief, or a changed `### Hiring` roll-up. Pure reordering, a
Recent→Older rotation, a `_none_`/`_Last updated:_` edit, or an "Also reported
by:" source appended to an existing signal all leave the set of signal
identities unchanged and are correctly skipped. A brand-new brief that actually
carries a signal is material (fizzdragon-style first-run creation).

Each image is redrawn at most once per `--min-age-days` (default 30): a brief
whose `infographic.png` was committed less than that long ago is skipped
("cooling"). Once the image is old enough, the brief is compared against its
own text *at the commit that last wrote the image*, not against HEAD, so new
signals that landed during the cooldown still trigger exactly one redraw.
Slugs past the per-run cap stay eligible on later runs for the same reason.

Output `data/infographic-queue.json` (gitignored, regenerated per run):

    {"changed": [<slug>, ...], "missing": [<slug>, ...]}

- `changed`  — briefs carrying a genuinely new signal since their image was
               last drawn, image older than the cooldown; oldest image first.
- `missing`  — briefs that have a `LIVING_BRIEF.md` but no `infographic.png`
               (a prior Layer 4 failure), oldest-first by last-commit time,
               with anything already in `changed` removed. Backfill.

The prompt (`prompts/infographics.md`) reads this file, applies the per-run cap,
and generates the images. Fails open: an unparseable brief or a git error for
one slug is skipped with a warning, never aborting the run.

Usage:
  python3 scripts/select_infographic_queue.py [--briefs-dir DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make the sibling `normalize_briefs` module importable both when run directly
# (scripts/ is sys.path[0]) and under pytest's file-path module loader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_briefs import (  # noqa: E402
    OLDER_HEADING,
    RECENT_HEADING,
    UnparseableBrief,
    parse_section,
    split_subsection,
)

BRIEFS_DIR = REPO_ROOT / "signals" / "briefs"
OUT_FILE = REPO_ROOT / "data" / "infographic-queue.json"
BRIEF_NAME = "LIVING_BRIEF.md"
PNG_NAME = "infographic.png"
MIN_AGE_DAYS = 30


def extract_recent(text: str) -> tuple[list[list[str]], list[list[str]], list[str]]:
    """Return (recent_blocks, older_blocks, recent_hiring_tail) for a brief.

    Locates the Recent/Older signal sections exactly as normalize_briefs does
    and parses their bullet blocks. Raises UnparseableBrief when the sections
    can't be found or parsed — callers treat that as "changed" (fail open to
    regenerating, never to a silently stale image).
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
    older_lines, _ = split_subsection(lines[oi + 1 : suffix_start])
    recent = parse_section(recent_lines, RECENT_HEADING)
    older = parse_section(older_lines, OLDER_HEADING)
    return recent, older, recent_tail


# Trailing "(Also reported by: [src](url), ...)" parenthetical on a signal's
# headline line — corroboration of an *existing* event, not a new signal.
ALSO_REPORTED_RE = re.compile(r"\s*\(Also reported by:.*\)\s*$")


def signal_key(block: list[str]) -> str:
    """Identity of a signal — its date + headline, corroboration stripped.

    A signal block's first line is `- **YYYY-MM-DD** — <headline> — [src](url)`.
    Two blocks are the *same event* when this line matches after removing any
    trailing `(Also reported by: ...)` parenthetical, so appending a corroborating
    source (the vibefam case) or moving the block between Recent and Older (the
    normalize rotation case) does not read as a new signal. The identity ignores
    sub-bullets deliberately: a new dated event is what should refresh the image,
    not an edit to an existing signal's summary detail.
    """
    if not block:
        return ""
    return ALSO_REPORTED_RE.sub("", block[0].rstrip())


def tail_key(tail: list[str]) -> str:
    return "\n".join(line.rstrip() for line in tail if line.strip())


def git_brief_at(rel_path: str, rev: str = "HEAD") -> str | None:
    """Contents of a brief at `rev`, or None if untracked/absent there."""
    try:
        out = subprocess.run(
            ["git", "show", f"{rev}:{rel_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def git_last_commit_ts(rel_path: str) -> int:
    """Unix timestamp of the file's last commit; 0 when untracked (sorts first)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", rel_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 0
    ts = out.stdout.strip()
    return int(ts) if ts.isdigit() else 0


def git_last_commit_sha(rel_path: str) -> str | None:
    """Hash of the file's last commit; None when untracked or on git error."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    sha = out.stdout.strip()
    return sha or None


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def signals_material_change(head_text: str | None, work_text: str) -> bool:
    """True when the working tree adds a genuinely new signal vs the committed brief.

    "Material" means: the working tree carries a signal — in either the Recent
    or the Older section — whose identity (`signal_key`, i.e. date + headline
    with corroboration stripped) is absent from the committed brief, or the
    Recent `### Hiring` roll-up changed. This deliberately treats as *not*
    material the churn that used to over-fire Layer 4:

      - a signal rotated Recent→Older or resorted by `normalize_briefs.py`
        (same identity, different section) — its key already exists in HEAD;
      - an "Also reported by:" source appended to an existing signal (vibefam) —
        the parenthetical is stripped before keying;
      - a `_Last updated:_` bump, retired open question, or Funding-line edit —
        none of which are signal bullets at all.

    A new brief (no HEAD version) is material iff it actually carries a signal or
    a Hiring roll-up. An unparseable brief on either side fails open to True
    (regenerate rather than serve a silently stale image).
    """
    try:
        work_recent, work_older, work_tail = extract_recent(work_text)
    except UnparseableBrief:
        return True

    work_keys = {signal_key(b) for b in work_recent + work_older if signal_key(b)}

    if head_text is None:
        # Untracked brief: material iff it carries any signal or a Hiring tail.
        return bool(work_keys) or bool(tail_key(work_tail))

    try:
        head_recent, head_older, head_tail = extract_recent(head_text)
    except UnparseableBrief:
        return True

    head_keys = {signal_key(b) for b in head_recent + head_older if signal_key(b)}
    if work_keys - head_keys:
        return True
    return tail_key(work_tail) != tail_key(head_tail)


def select(
    briefs_dir: Path,
    *,
    now: float | None = None,
    min_age_days: float = MIN_AGE_DAYS,
) -> dict[str, list[str]]:
    now = time.time() if now is None else now
    changed: list[tuple[int, str]] = []
    missing: list[tuple[int, str]] = []
    cooling: list[str] = []

    for path in sorted(briefs_dir.glob(f"*/{BRIEF_NAME}")):
        slug = path.parent.name
        rel_path = repo_relative(path)
        try:
            work_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"select_infographic_queue: SKIP {slug}: {exc}", file=sys.stderr)
            continue

        png_path = path.parent / PNG_NAME
        has_png = png_path.exists()
        baseline_rev = "HEAD"
        png_ts = 0
        if has_png:
            png_rel = repo_relative(png_path)
            png_ts = git_last_commit_ts(png_rel)
            if png_ts and now - png_ts < min_age_days * 86400:
                cooling.append(slug)
                continue
            baseline_rev = git_last_commit_sha(png_rel) or "HEAD"

        baseline_text = git_brief_at(rel_path, baseline_rev)
        try:
            is_material = signals_material_change(baseline_text, work_text)
        except Exception as exc:  # fail open: never abort the run on one brief
            print(f"select_infographic_queue: {slug}: assuming changed ({exc})", file=sys.stderr)
            is_material = True

        if is_material:
            changed.append((png_ts, slug))
            continue

        if not has_png:
            missing.append((git_last_commit_ts(rel_path), slug))

    changed.sort(key=lambda pair: pair[0])
    missing.sort(key=lambda pair: pair[0])  # oldest last-commit first
    changed_slugs = [slug for _, slug in changed]
    changed_set = set(changed_slugs)
    missing_slugs = [slug for _, slug in missing if slug not in changed_set]
    return {"changed": changed_slugs, "missing": missing_slugs, "cooling": cooling}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--briefs-dir",
        default=str(BRIEFS_DIR),
        help="Directory containing <slug>/LIVING_BRIEF.md trees (default: signals/briefs).",
    )
    parser.add_argument(
        "--out",
        default=str(OUT_FILE),
        help="Where to write the queue JSON (default: data/infographic-queue.json).",
    )
    parser.add_argument(
        "--min-age-days",
        type=float,
        default=MIN_AGE_DAYS,
        help=f"Redraw an existing infographic at most this often (default: {MIN_AGE_DAYS}).",
    )
    args = parser.parse_args()

    selection = select(Path(args.briefs_dir), min_age_days=args.min_age_days)
    queue = {"changed": selection["changed"], "missing": selection["missing"]}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")

    print(
        f"select_infographic_queue: {len(queue['changed'])} changed, "
        f"{len(queue['missing'])} missing-backfill, "
        f"{len(selection['cooling'])} cooling (<{args.min_age_days:g}d) → {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
