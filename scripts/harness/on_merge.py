#!/usr/bin/env python3
"""Record a merged `harness:auto` PR in the ledger, then refresh trust.

Reads the PR via `gh pr view N --json body,mergeCommit,mergedAt`, parses the
lines `pr_body.py` emits (`Cluster: ...`, `Rung: N`, `Files: a, b`,
`New scenarios: id1, id2`; tolerant of list bullets, bold and backticks),
appends the ledger row (`ledger.py append` semantics, idempotent by PR) with
the merge SHA, and recomputes signals/harness/trust.json via `trust.py`.

    on_merge.py --pr N [--verdict data/harness/verdict.json]

The verdict is optional: data/harness/ is gitignored, so on a merge event it is
usually absent and gain degrades to the declared scenario count. Exit 1 only
when the PR cannot be read or carries no Cluster/Rung (nothing to record).
Pure stdlib; gh goes through an injectable runner.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger
import trust

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELD_NAMES = {
    "cluster": "Cluster",
    "rung": "Rung",
    "files": "Files",
    "scenarios": "New scenarios",
}

Runner = Callable[..., subprocess.CompletedProcess]


def field_pattern(label: str) -> re.Pattern[str]:
    return re.compile(rf"^[\s>*_-]*{re.escape(label)}[*_]*\s*:[*_]*\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)


def clean_value(value: str) -> str:
    return value.replace("`", "").strip().strip("*_").strip()


def parse_body(body: str) -> dict:
    fields: dict[str, str] = {}
    for key, label in FIELD_NAMES.items():
        match = field_pattern(label).search(body or "")
        if match:
            fields[key] = clean_value(match.group(1))
    rung_match = re.match(r"\d+", fields.get("rung", ""))
    return {
        "cluster": fields.get("cluster") or None,
        "rung": int(rung_match.group(0)) if rung_match else None,
        "files": split_items(fields.get("files", "")),
        "scenario_ids": split_items(fields.get("scenarios", "")),
    }


def split_items(value: str) -> list[str]:
    if value.lower() in {"", "none", "-", "n/a"}:
        return []
    return ledger.split_list([value])


def view_pr(runner: Runner, pr_number: int) -> dict:
    result = runner(["gh", "pr", "view", str(pr_number), "--json", "body,mergeCommit,mergedAt"])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip() or f"gh pr view {pr_number} failed")
    return json.loads(result.stdout or "{}")


def record_merge(runner: Runner, pr_number: int, ledger_path: Path, verdict_path: Path | None) -> dict:
    viewed = view_pr(runner, pr_number)
    parsed = parse_body(viewed.get("body") or "")
    if not parsed["cluster"] or parsed["rung"] is None:
        raise ValueError(f"PR #{pr_number} body declares no Cluster/Rung")
    row = ledger.build_row(
        pr=pr_number,
        cluster=parsed["cluster"],
        rung=parsed["rung"],
        files=parsed["files"],
        scenario_ids=parsed["scenario_ids"],
        verdict=ledger.load_verdict(verdict_path),
        merge_sha=(viewed.get("mergeCommit") or {}).get("oid"),
        merged_at=viewed.get("mergedAt") or None,
    )
    appended = ledger.append_row(row, ledger_path)
    print(f"on_merge: {'appended' if appended else 'already recorded'} {ledger.format_row(row)}")
    return row


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--verdict", default=None)
    parser.add_argument("--ledger", default=str(ledger.LEDGER_PATH))
    parser.add_argument("--trust-out", default=str(trust.TRUST_PATH))
    return parser


def main(argv: list[str] | None = None, runner: Runner = trust.default_runner) -> int:
    args = build_parser().parse_args(argv)
    verdict_path = Path(args.verdict) if args.verdict else None
    try:
        record_merge(runner, args.pr, Path(args.ledger), verdict_path)
    except (RuntimeError, ValueError) as error:
        print(f"on_merge: {error}", file=sys.stderr)
        return 1
    trust.run(runner, Path(args.trust_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
