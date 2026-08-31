#!/usr/bin/env python3
"""Stamp today's queued gap-fill companies as queried in agent-queue-state.json.

Runs after Layer 2 (agent supplement) so the next run's selection sees the
companies just looked at as "recently queried" and rotates to the next batch.

Reads `signals/agent-queue.txt` (produced by select_gapfill_queue.py before
Layer 2) and writes `last_queried = <date>` into
`signals/agent-queue-state.json`. Stale state entries (names no longer in
`data/companies.json`) are dropped at the same time.

Which names get stamped — honest stamping:
  * If `data/agent-reached.txt` exists (Layer 2 appends one company name per
    line as it finishes each one; the workflow deletes any stale copy before
    the layer runs), only the intersection of queued ∩ reached is stamped.
    Companies the agent never got to keep their turn and come back on the next
    run instead of silently burning a rotation slot — which matters because
    Layer 2 is `continue-on-error`, so a crashed run used to consume the whole
    queue's cadence.
  * If the file is absent, fall back to stamping every queued name. Fail open:
    the model may forget to write it, and rotation liveness beats precision —
    over-marking delays a company by one cycle, while under-marking would
    re-query the same prefix indefinitely.

Names must match exactly after stripping whitespace; reached names that aren't
in the queue (or aren't watchlisted) are ignored with a note.

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


def read_names(path: Path) -> list[str]:
    """Non-empty stripped lines of a one-name-per-line file, order preserved."""
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def resolve_stamp_targets(queued: list[str], reached_path: Path) -> tuple[list[str], list[str]]:
    """(names to stamp, reached names that weren't queued).

    Present reached file → queued ∩ reached (order follows the queue).
    Absent or unreadable reached file → all queued names (fail open).
    """
    if not reached_path.exists():
        return list(queued), []
    try:
        reached = read_names(reached_path)
    except OSError:
        return list(queued), []
    reached_set = set(reached)
    queued_set = set(queued)
    targets = [name for name in queued if name in reached_set]
    unqueued = sorted({name for name in reached if name not in queued_set})
    return targets, unqueued


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

    queued = read_names(queue_path)
    if not queued:
        print("agent-queue.txt is empty; nothing to record")
        return 0

    reached_path = repo / "data" / "agent-reached.txt"
    targets, unqueued_reached = resolve_stamp_targets(queued, reached_path)
    honest = reached_path.exists()

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

    # Only stamp target names that are still watchlisted; warn on unknowns.
    stamped = 0
    unknown: list[str] = []
    for name in targets:
        if name in known:
            last_queried[name] = args.date
            stamped += 1
        else:
            unknown.append(name)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_queried": last_queried}, indent=2, sort_keys=True) + "\n"
    )

    if honest:
        source = f"reached-file ({len(targets)}/{len(queued)} queued reached)"
    else:
        source = f"no reached-file; fell back to all {len(queued)} queued"
    msg = f"stamped {stamped} companies as queried on {args.date} [{source}]"
    if pruned:
        msg += f"; pruned {pruned} stale entries"
    if unknown:
        msg += f"; skipped {len(unknown)} unknown names ({', '.join(unknown[:3])}...)"
    if unqueued_reached:
        msg += (
            f"; ignored {len(unqueued_reached)} reached names not in the queue "
            f"({', '.join(unqueued_reached[:3])}...)"
        )
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
