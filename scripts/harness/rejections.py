#!/usr/bin/env python3
"""Append rejected harness runs to signals/harness/rejections.md.

GitHub Issues are disabled on this repository, so a harness-optimise run whose
proposal was rejected records its evidence in a committed file instead:

    rejections.py append --run-id ID --run-url URL [--date YYYY-MM-DD] \
        [--cluster data/harness/cluster.json] [--verdict data/harness/verdict.json] \
        [--proposal data/harness/proposal.json] [--review data/harness/review.json] \
        [--resolutions data/harness/resolutions.json] [--table data/harness/replay-table.md] \
        [--out signals/harness/rejections.md]

Records are appended chronologically (oldest first, newest at the bottom), one
`## <date> — <cluster> (run <id>)` section each. Idempotent per run: a run
whose URL already appears in the file is skipped. Every input fails open to a
placeholder. Pure stdlib. See docs/self-optimising-harness.md §7.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_body

REPO_ROOT = Path(__file__).resolve().parents[2]
REJECTIONS_PATH = REPO_ROOT / "signals" / "harness" / "rejections.md"
HEADER = (
    "# Harness rejections\n\n"
    "One section per harness-optimise run whose proposal was rejected, oldest first. "
    "Written by `scripts/harness/rejections.py`.\n"
)


def today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def bullets(items: list[str], empty: str) -> list[str]:
    return [f"- {item}" for item in items] if items else [f"- {empty}"]


def render_record(
    *,
    run_id: str,
    run_url: str,
    date: str,
    cluster: dict,
    proposal: dict,
    verdict: dict,
    review: dict,
    resolutions: dict,
    table: str,
) -> str:
    cluster_key = cluster.get("cluster") or "unknown"
    accept = verdict.get("accept")
    decision = "not available" if accept is None else ("accept" if accept else "reject")
    unresolved = [str(finding.get("claim", "")) for finding in pr_body.unresolved_blocking(review, resolutions)]
    lines = [
        f"## {date} — {cluster_key} (run {run_id})",
        "",
        f"- Run: {run_url}",
        f"- Rung: {proposal.get('rung', 'not available')}",
        f"- Files: {', '.join(f'`{path}`' for path in proposal.get('files') or []) or 'none'}",
        f"- Verdict: {decision}",
        "",
        "Verdict reasons:",
        "",
        *bullets([str(reason) for reason in verdict.get("reasons") or []], "none recorded"),
        "",
        "Unresolved blocking findings:",
        "",
        *bullets(unresolved, "none"),
        "",
    ]
    if table:
        lines += ["Replay table:", "", table, ""]
    return "\n".join(lines)


def already_recorded(existing: str, run_url: str) -> bool:
    return f"- Run: {run_url}\n" in existing


def append_record(out: Path, record: str, run_url: str) -> bool:
    existing = out.read_text(encoding="utf-8") if out.exists() else ""
    if already_recorded(existing, run_url):
        return False
    base = existing if existing else HEADER
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(base.rstrip("\n") + "\n\n" + record.rstrip("\n") + "\n", encoding="utf-8")
    return True


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a rejected harness proposal in signals/harness/rejections.md")
    sub = parser.add_subparsers(dest="command", required=True)
    append = sub.add_parser("append")
    append.add_argument("--run-id", required=True)
    append.add_argument("--run-url", required=True)
    append.add_argument("--date", default=None)
    append.add_argument("--cluster", type=Path, default=pr_body.HARNESS_DIR / "cluster.json")
    append.add_argument("--verdict", type=Path, default=pr_body.HARNESS_DIR / "verdict.json")
    append.add_argument("--proposal", type=Path, default=pr_body.HARNESS_DIR / "proposal.json")
    append.add_argument("--review", type=Path, default=pr_body.HARNESS_DIR / "review.json")
    append.add_argument("--resolutions", type=Path, default=pr_body.HARNESS_DIR / "resolutions.json")
    append.add_argument("--table", type=Path, default=pr_body.HARNESS_DIR / "replay-table.md")
    append.add_argument("--out", type=Path, default=REJECTIONS_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    record = render_record(
        run_id=args.run_id,
        run_url=args.run_url,
        date=args.date or today_utc(),
        cluster=pr_body.load_json(args.cluster),
        proposal=pr_body.load_json(args.proposal),
        verdict=pr_body.load_json(args.verdict),
        review=pr_body.load_json(args.review),
        resolutions=pr_body.load_json(args.resolutions),
        table=pr_body.load_text(args.table),
    )
    written = append_record(args.out, record, args.run_url)
    print(f"rejections: {'recorded' if written else 'already recorded'} run {args.run_id} in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
