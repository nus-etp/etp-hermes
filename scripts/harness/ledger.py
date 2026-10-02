#!/usr/bin/env python3
"""Ledger of merged harness proposals — signals/harness/ledger.jsonl.

One JSON row per merged `harness:auto` PR, keyed (idempotently) by PR number:
`{merged_at, pr, cluster, rung, files, scenario_ids, gain, merge_sha}` where
gain is `{scenarios_fixed, window_delta, volume_ratio}` read from the grade.py
verdict.json the proposal was accepted on. A missing or unreadable verdict
degrades gain to what the PR itself declares (fail open). `prune.py` later
stamps `pruned_pr` on rows it opened a revert PR for.

    ledger.py append --pr N --cluster K --rung R --files a b --scenarios id1 \
        --verdict data/harness/verdict.json [--merge-sha SHA] [--merged-at ISO]
    ledger.py list [--json]

Pure stdlib. See docs/self-optimising-harness.md §7.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "signals" / "harness" / "ledger.jsonl"


def utc_now_iso() -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def read_rows(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            print(f"ledger: skipping malformed line: {line[:80]}", file=sys.stderr)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_rows(rows: list[dict], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def find_row(rows: list[dict], pr: int) -> dict | None:
    for row in rows:
        if row.get("pr") == pr:
            return row
    return None


def split_list(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for part in value.split(","):
            part = part.strip()
            if part and part not in items:
                items.append(part)
    return items


def load_verdict(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        verdict = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return verdict if isinstance(verdict, dict) else None


def gain_from_verdict(verdict: dict | None, scenario_ids: list[str]) -> dict:
    per_runtime = (verdict or {}).get("per_runtime")
    if not isinstance(per_runtime, dict) or not per_runtime:
        return {"scenarios_fixed": len(scenario_ids), "window_delta": None, "volume_ratio": None}
    failed: set[str] = set()
    window_delta = 0
    ratios: list[float] = []
    for stats in per_runtime.values():
        if not isinstance(stats, dict):
            continue
        failed.update(stats.get("failed_ids") or [])
        window_delta += int(stats.get("proposal_right") or 0) - int(stats.get("champion_right") or 0)
        ratio = stats.get("volume_ratio")
        if isinstance(ratio, (int, float)):
            ratios.append(float(ratio))
    return {
        "scenarios_fixed": sum(1 for scenario_id in scenario_ids if scenario_id not in failed),
        "window_delta": window_delta,
        "volume_ratio": round(min(ratios), 4) if ratios else None,
    }


def build_row(
    pr: int,
    cluster: str,
    rung: int,
    files: list[str],
    scenario_ids: list[str],
    verdict: dict | None,
    merge_sha: str | None,
    merged_at: str | None,
) -> dict:
    return {
        "merged_at": merged_at or utc_now_iso(),
        "pr": pr,
        "cluster": cluster,
        "rung": rung,
        "files": files,
        "scenario_ids": scenario_ids,
        "gain": gain_from_verdict(verdict, scenario_ids),
        "merge_sha": merge_sha,
    }


def append_row(row: dict, path: Path = LEDGER_PATH) -> bool:
    rows = read_rows(path)
    if find_row(rows, row["pr"]) is not None:
        return False
    rows.append(row)
    write_rows(rows, path)
    return True


def mark_pruned(pr: int, pruned_pr: int, path: Path = LEDGER_PATH) -> bool:
    rows = read_rows(path)
    row = find_row(rows, pr)
    if row is None:
        return False
    row["pruned_pr"] = pruned_pr
    write_rows(rows, path)
    return True


def format_row(row: dict) -> str:
    gain = row.get("gain") or {}
    pruned = f" pruned_by=#{row['pruned_pr']}" if row.get("pruned_pr") else ""
    return (
        f"#{row.get('pr')}\t{row.get('merged_at')}\trung {row.get('rung')}\t{row.get('cluster')}\t"
        f"fixed={gain.get('scenarios_fixed')} window_delta={gain.get('window_delta')} "
        f"volume_ratio={gain.get('volume_ratio')}{pruned}"
    )


def command_append(args: argparse.Namespace) -> int:
    scenario_ids = split_list(args.scenarios)
    verdict_path = Path(args.verdict) if args.verdict else None
    verdict = load_verdict(verdict_path)
    if verdict_path is not None and verdict is None:
        print(f"ledger: verdict {verdict_path} missing or unreadable; gain degraded", file=sys.stderr)
    row = build_row(
        pr=args.pr,
        cluster=args.cluster,
        rung=args.rung,
        files=split_list(args.files),
        scenario_ids=scenario_ids,
        verdict=verdict,
        merge_sha=args.merge_sha,
        merged_at=args.merged_at,
    )
    if append_row(row, Path(args.ledger)):
        print(f"ledger: appended {format_row(row)}")
    else:
        print(f"ledger: PR #{args.pr} already recorded; unchanged")
    return 0


def command_list(args: argparse.Namespace) -> int:
    rows = read_rows(Path(args.ledger))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True) if args.json else format_row(row))
    if not rows and not args.json:
        print("ledger: empty")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    append = sub.add_parser("append")
    append.add_argument("--ledger", default=argparse.SUPPRESS)
    append.add_argument("--pr", type=int, required=True)
    append.add_argument("--cluster", required=True)
    append.add_argument("--rung", type=int, required=True)
    append.add_argument("--files", nargs="*", default=[])
    append.add_argument("--scenarios", nargs="*", default=[])
    append.add_argument("--verdict", default=None)
    append.add_argument("--merge-sha", default=None)
    append.add_argument("--merged-at", default=None)
    append.set_defaults(handler=command_append)
    listing = sub.add_parser("list")
    listing.add_argument("--ledger", default=argparse.SUPPRESS)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=command_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
