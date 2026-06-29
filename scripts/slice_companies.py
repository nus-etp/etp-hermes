#!/usr/bin/env python3
"""Slice data/companies.json down to the companies a layer actually needs.

data/companies.json is ~300KB; feeding it wholesale into an agent session
costs ~80K input tokens per turn. Each layer only needs a handful of
entries, all derivable deterministically before the session starts:

  --layer agent      Layer 2 cohort: names in signals/agent-queue.txt plus
                     the `## ` company headings in today's
                     signals/updates/<date>.md (the deepen cohort).
                     Writes data/agent-companies.json.

  --layer synthesis  Layer 3 cohort: company headings in today's
                     signals/updates/<date>.md (H2) and
                     signals/agent/<date>.md (H3).
                     Writes data/touched-companies.json.

Output is a JSON array of the full company objects, same schema as
companies.json. Names that don't match any company are reported on stderr
and skipped (the prompts already treat unrecognized names as skip-and-log).
Always exits 0 with a valid (possibly empty) array so the layer prompts can
rely on the file existing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
QUEUE_FILE = REPO_ROOT / "signals" / "agent-queue.txt"
UPDATES_DIR = REPO_ROOT / "signals" / "updates"
AGENT_DIR = REPO_ROOT / "signals" / "agent"
OUT_FILES = {
    "agent": REPO_ROOT / "data" / "agent-companies.json",
    "synthesis": REPO_ROOT / "data" / "touched-companies.json",
}

# `## Run at <time>` subheadings are section dividers, not company names.
RUN_HEADING_RE = re.compile(r"^run at ", re.IGNORECASE)


def heading_names(path: Path, level: int) -> list[str]:
    """Company names from `#`*level headings; skips 'Run at' dividers."""
    if not path.exists():
        return []
    prefix = "#" * level + " "
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(prefix):
            continue
        name = line[len(prefix) :].strip()
        if name and not RUN_HEADING_RE.match(name):
            names.append(name)
    return names


def queue_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=("agent", "synthesis"), required=True)
    parser.add_argument("--date", help="UTC date override (YYYY-MM-DD), default today")
    args = parser.parse_args()

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updates_file = UPDATES_DIR / f"{date}.md"
    agent_file = AGENT_DIR / f"{date}.md"

    if args.layer == "agent":
        names = queue_names(QUEUE_FILE) + heading_names(updates_file, 2)
    else:
        names = heading_names(updates_file, 2) + heading_names(agent_file, 3)

    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in companies}

    sliced: list[dict] = []
    picked: set[str] = set()
    unknown: list[str] = []
    for name in names:
        if name in picked:
            continue
        c = by_name.get(name)
        if c is None:
            unknown.append(name)
            continue
        picked.add(name)
        sliced.append(c)

    out_file = OUT_FILES[args.layer]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(sliced, f, indent=2, sort_keys=True)

    if unknown:
        print(f"slice_companies: unrecognized names skipped: {unknown}", file=sys.stderr)
    print(
        f"slice_companies: layer={args.layer} date={date} "
        f"companies={len(sliced)} unknown={len(unknown)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
