#!/usr/bin/env python3
"""Stamp today's queued gap-fill companies as queried in agent-queue-state.json.

Runs after Layer 2 (agent supplement) so the next run's selection sees the
companies just looked at as "recently queried" and rotates to the next batch.

Reads `signals/agent-queue.txt` (produced by select_gapfill_queue.py before
Layer 2) and writes `last_queried = <date>` for each listed name into
`signals/agent-queue-state.json`. Stale state entries (names no longer in
`data/companies.json`) are dropped at the same time.

Caveat: every queued name is marked queried, including any the agent didn't
reach if it hit the 50-op budget. That's deliberate — over-marking only delays
those companies by one cycle, whereas under-marking would re-query the same
prefix indefinitely.

Pure stdlib. Idempotent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_company_names(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    return {c["name"] for c in data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date to stamp (default: today).",
    )
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    queue_path = repo / "signals" / "agent-queue.txt"
    state_path = repo / "signals" / "agent-queue-state.json"
    companies_path = repo / "data" / "companies.json"

    if not queue_path.exists():
        print("no agent-queue.txt; nothing to record")
        return 0

    queued = [ln.strip() for ln in queue_path.read_text().splitlines() if ln.strip()]
    if not queued:
        print("agent-queue.txt is empty; nothing to record")
        return 0

    known = load_company_names(companies_path)

    last_queried: dict[str, str] = {}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("last_queried"), dict):
                last_queried = dict(loaded["last_queried"])
        except json.JSONDecodeError:
            pass

    # Drop entries for companies removed from the watchlist.
    before = len(last_queried)
    last_queried = {k: v for k, v in last_queried.items() if k in known}
    pruned = before - len(last_queried)

    # Only stamp queued names that are still watchlisted; warn on unknowns.
    stamped = 0
    unknown: list[str] = []
    for name in queued:
        if name in known:
            last_queried[name] = args.date
            stamped += 1
        else:
            unknown.append(name)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_queried": last_queried}, indent=2, sort_keys=True) + "\n"
    )

    msg = f"stamped {stamped} companies as queried on {args.date}"
    if pruned:
        msg += f"; pruned {pruned} stale entries"
    if unknown:
        msg += f"; skipped {len(unknown)} unknown names ({', '.join(unknown[:3])}...)"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
