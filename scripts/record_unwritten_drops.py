#!/usr/bin/env python3
"""Record Layer 1 candidates the ingest LLM neither kept nor dropped.

After Layer 1, every candidate in ``data/candidates.json`` whose ``dedup_key``
(or link) is not in ``signals/seen-urls.txt``, not live in
``signals/dropped-urls.txt`` and not linked from today's updates file was
silently omitted by the model. Without a record the collector re-presents it on
every run. This appends each such key to ``dropped-urls.txt`` stamped with the
run date, so it expires through the normal TTL instead of recurring forever.

Fails open: a missing or unreadable candidates file is a no-op (exit 0). When
pre-run snapshots are given and Layer 1 left no trace at all (no updates file,
neither dedup file grew), the run is treated as a failed ingest and skipped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dropped_urls

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_candidates(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    return candidates if isinstance(candidates, list) else None


def updates_links(path: Path) -> set[str]:
    return {line for line in read_lines(path) if line.startswith(("http://", "https://"))}


def layer1_left_trace(
    updates: Path,
    seen: Path,
    dropped: Path,
    seen_prerun: Path | None,
    dropped_prerun: Path | None,
) -> bool:
    if seen_prerun is None and dropped_prerun is None:
        return True
    if updates.exists():
        return True
    seen_grew = seen_prerun is not None and len(read_lines(seen)) > len(read_lines(seen_prerun))
    dropped_grew = dropped_prerun is not None and len(read_lines(dropped)) > len(
        read_lines(dropped_prerun)
    )
    return seen_grew or dropped_grew


def unrecorded_keys(candidates: list[dict], recorded: set[str]) -> list[str]:
    missing: list[str] = []
    emitted: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("dedup_key") or "").strip()
        link = str(candidate.get("link") or "").strip()
        if not key or "\t" in key or "\n" in key:
            continue
        if key in recorded or link in recorded or key in emitted:
            continue
        emitted.add(key)
        missing.append(key)
    return missing


def append_drops(dropped: Path, keys: list[str], day: dt.date) -> None:
    if not keys:
        return
    dropped.parent.mkdir(parents=True, exist_ok=True)
    existing = dropped.read_text(encoding="utf-8") if dropped.exists() else ""
    separator = "" if not existing or existing.endswith("\n") else "\n"
    body = "".join(f"{dropped_urls.format_line(key, day)}\n" for key in keys)
    with dropped.open("a", encoding="utf-8") as handle:
        handle.write(separator + body)


def record(
    candidates_path: Path,
    updates: Path,
    seen: Path,
    dropped: Path,
    day: dt.date,
    seen_prerun: Path | None = None,
    dropped_prerun: Path | None = None,
) -> int | None:
    candidates = load_candidates(candidates_path)
    if candidates is None:
        return None
    if not layer1_left_trace(updates, seen, dropped, seen_prerun, dropped_prerun):
        return None
    recorded = (
        set(read_lines(seen))
        | dropped_urls.load_active(dropped, today=day)
        | updates_links(updates)
    )
    keys = unrecorded_keys(candidates, recorded)
    append_drops(dropped, keys, day)
    return len(keys)


def optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    )
    parser.add_argument("--candidates", default=None)
    parser.add_argument("--updates", default=None)
    parser.add_argument("--seen", default=None)
    parser.add_argument("--dropped", default=None)
    parser.add_argument("--seen-prerun", default=None)
    parser.add_argument("--dropped-prerun", default=None)
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    day = dt.date.fromisoformat(args.date)
    candidates_path = optional_path(args.candidates) or repo / "data" / "candidates.json"
    updates = optional_path(args.updates) or repo / "signals" / "updates" / f"{args.date}.md"
    seen = optional_path(args.seen) or repo / "signals" / "seen-urls.txt"
    dropped = optional_path(args.dropped) or repo / "signals" / "dropped-urls.txt"

    recorded = record(
        candidates_path,
        updates,
        seen,
        dropped,
        day,
        optional_path(args.seen_prerun),
        optional_path(args.dropped_prerun),
    )
    if recorded is None:
        print("record-unwritten-drops: no candidates or no Layer 1 trace; nothing to do")
        return 0
    print(f"record-unwritten-drops: recorded={recorded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
