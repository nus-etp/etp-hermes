#!/usr/bin/env python3
"""Freeze today's Layer 1 input for replay.

Copies ``data/candidates.json`` to ``data/replay/<date>.json`` (committed) so
the harness simulator can later replay a fresh window of real candidates
through a proposed prompt, then prunes ``data/replay/`` to the newest
``--keep`` (default 30) dated snapshots. Fail-open: a missing candidates file
is a no-op (exit 0). Idempotent per date. Pure stdlib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEEP = 30
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def snapshot(candidates: Path, replay_dir: Path, date: str) -> Path | None:
    if not candidates.exists():
        return None
    replay_dir.mkdir(parents=True, exist_ok=True)
    target = replay_dir / f"{date}.json"
    shutil.copyfile(candidates, target)
    return target


def prune(replay_dir: Path, keep: int) -> list[Path]:
    if not replay_dir.exists():
        return []
    snapshots = sorted(p for p in replay_dir.iterdir() if SNAPSHOT_RE.match(p.name))
    stale = snapshots[: max(len(snapshots) - keep, 0)]
    for path in stale:
        path.unlink()
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    replay_dir = repo / "data" / "replay"
    target = snapshot(repo / "data" / "candidates.json", replay_dir, args.date)
    if target is None:
        print("snapshot_replay: no data/candidates.json; nothing to snapshot")
        return 0
    removed = prune(replay_dir, args.keep)
    print(f"snapshot_replay: wrote {target.relative_to(repo)}; pruned {len(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
