#!/usr/bin/env python3
"""Merge drafted portfolio entries (from scripts/discover_portfolio.py) into
data/companies.json, preserving the file's case-insensitive alphabetical
ordering from position 3 onwards — the first three entries (Carousell,
Patsnap, Horizon Quantum Computing) are deliberately pinned at the top.

Usage:
  python3 scripts/merge_portfolio_entries.py data/portfolio-new-entries.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_JSON = REPO_ROOT / "data" / "companies.json"
PINNED_HEAD = 3  # Carousell, Patsnap, Horizon Quantum Computing stay first


def merge(companies: list[dict], new_entries: list[dict]) -> tuple[list[dict], int]:
    existing_names = {c["name"].lower() for c in companies}
    additions = []
    for e in new_entries:
        if e["name"].lower() in existing_names:
            print(f"SKIP duplicate: {e['name']}")
            continue
        additions.append(e)
        existing_names.add(e["name"].lower())
    if not additions:
        return companies, 0
    head = companies[:PINNED_HEAD]
    tail = companies[PINNED_HEAD:]
    merged_tail = sorted(tail + additions, key=lambda c: c["name"].lower())
    return head + merged_tail, len(additions)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0]} path/to/new_entries.json", file=sys.stderr)
        return 2
    new_entries = json.loads(Path(argv[1]).read_text())
    if not isinstance(new_entries, list):
        print("new entries file must be a JSON array", file=sys.stderr)
        return 2
    companies = json.loads(COMPANIES_JSON.read_text())
    merged, added = merge(companies, new_entries)
    if not added:
        print("Nothing to add.")
        return 0
    COMPANIES_JSON.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {added} entries. New total: {len(merged)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
