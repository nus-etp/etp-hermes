#!/usr/bin/env python3
"""Render the pull-request body for a harness proposal.

Reads the artifacts one `harness-optimise.yml` run leaves under
`data/harness/` and prints Markdown to stdout (docs/self-optimising-harness.md
§5): the cluster and its named root cause, the rung and files touched, a diff
summary, the new scenario ids, the replay table, the verdict, and the
reviewer's findings with their resolutions (from the optional
`resolutions.json`, shaped `{claim: {"resolution": "fixed|rejected",
"scenario_id": ...}}`).

`--unresolved` instead prints one bullet per blocking finding that carries no
`evidence_scenario_ids` and has no fixed/rejected resolution backed by a
scenario id — empty output means the review loop is done. Every input fails
open to a placeholder so a partial run still renders. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HARNESS_DIR = Path("data") / "harness"
RESOLVED_KINDS = ("fixed", "rejected")
MISSING = "_not available_"


def load_json(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def diff_stat(base: str) -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{base}...HEAD"], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def findings_of(review: dict) -> list[dict]:
    findings = review.get("findings")
    if not isinstance(findings, list):
        return []
    return [finding for finding in findings if isinstance(finding, dict)]


def resolution_for(finding: dict, resolutions: dict) -> dict | None:
    resolution = resolutions.get(str(finding.get("claim", "")))
    if not isinstance(resolution, dict):
        return None
    if resolution.get("resolution") not in RESOLVED_KINDS or not resolution.get("scenario_id"):
        return None
    return resolution


def unresolved_blocking(review: dict, resolutions: dict) -> list[dict]:
    return [
        finding
        for finding in findings_of(review)
        if finding.get("severity") == "blocking"
        and not finding.get("evidence_scenario_ids")
        and resolution_for(finding, resolutions) is None
    ]


def render_cluster(cluster: dict) -> list[str]:
    root_cause = cluster.get("root_cause") or MISSING
    return [
        "## Cluster",
        "",
        f"- Key: `{cluster.get('cluster') or 'unknown'}`",
        f"- Size: {cluster.get('size', 0)} card(s), weighted {cluster.get('weighted_size', 0)}",
        f"- Companies: {', '.join(cluster.get('companies') or []) or '_none_'}",
        f"- Root cause: {root_cause}",
        "",
    ]


def render_proposal(proposal: dict) -> list[str]:
    files = proposal.get("files") or []
    new_ids = proposal.get("new_scenario_ids") or []
    lines = [
        "## Proposal",
        "",
        f"- Rung: {proposal.get('rung', MISSING)}",
        f"- Files: {', '.join(f'`{path}`' for path in files) or '_none_'}",
        f"- New scenarios: {', '.join(f'`{scenario_id}`' for scenario_id in new_ids) or '_none_'}",
    ]
    if proposal.get("rationale"):
        lines.append(f"- Rationale: {proposal['rationale']}")
    return lines + [""]


def render_diff(stat: str) -> list[str]:
    body = ["```", stat, "```"] if stat else [MISSING]
    return ["## Diff summary", "", *body, ""]


def render_verdict(verdict: dict, table: str) -> list[str]:
    accept = verdict.get("accept")
    decision = MISSING if accept is None else ("**accept**" if accept else "**reject**")
    lines = ["## Replay", "", f"Verdict: {decision}", ""]
    for reason in verdict.get("reasons") or []:
        lines.append(f"- {reason}")
    if verdict.get("reasons"):
        lines.append("")
    lines += [table or MISSING, ""]
    return lines


def describe_resolution(finding: dict, resolutions: dict) -> str:
    resolution = resolution_for(finding, resolutions)
    if resolution is not None:
        return f"{resolution['resolution']} (`{resolution['scenario_id']}`)"
    if finding.get("evidence_scenario_ids"):
        return "covered by cited scenarios"
    if finding.get("severity") == "blocking":
        return "**unresolved**"
    return "noted"


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_review(review: dict, resolutions: dict) -> list[str]:
    findings = findings_of(review)
    lines = ["## Review findings", ""]
    if not findings:
        return lines + ["_No findings._" if review else MISSING, ""]
    lines += ["| Severity | Claim | Evidence | Resolution |", "|---|---|---|---|"]
    for finding in findings:
        evidence = ", ".join(f"`{item}`" for item in finding.get("evidence_scenario_ids") or []) or "—"
        lines.append(
            f"| {finding.get('severity', '?')} | {escape_cell(str(finding.get('claim', '')))} "
            f"| {evidence} | {describe_resolution(finding, resolutions)} |"
        )
    return lines + [""]


def render_body(
    cluster: dict, proposal: dict, verdict: dict, table: str, review: dict, resolutions: dict, stat: str
) -> str:
    lines = [
        f"Automated proposal from the self-optimising harness for cluster `{cluster.get('cluster') or 'unknown'}`.",
        "See `docs/self-optimising-harness.md`. Merge only if the replay table and review hold up.",
        "",
        *render_cluster(cluster),
        *render_proposal(proposal),
        *render_diff(stat),
        *render_verdict(verdict, table),
        *render_review(review, resolutions),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_unresolved(review: dict, resolutions: dict) -> str:
    return "".join(f"- {finding.get('claim', '')}\n" for finding in unresolved_blocking(review, resolutions))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cluster", type=Path, default=HARNESS_DIR / "cluster.json")
    parser.add_argument("--verdict", type=Path, default=HARNESS_DIR / "verdict.json")
    parser.add_argument("--table", type=Path, default=HARNESS_DIR / "replay-table.md")
    parser.add_argument("--review", type=Path, default=HARNESS_DIR / "review.json")
    parser.add_argument("--proposal", type=Path, default=HARNESS_DIR / "proposal.json")
    parser.add_argument("--resolutions", type=Path, default=HARNESS_DIR / "resolutions.json")
    parser.add_argument("--diff-base", default="origin/main")
    parser.add_argument("--unresolved", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    review = load_json(args.review)
    resolutions = load_json(args.resolutions)
    if args.unresolved:
        sys.stdout.write(render_unresolved(review, resolutions))
        return 0
    body = render_body(
        load_json(args.cluster),
        load_json(args.proposal),
        load_json(args.verdict),
        load_text(args.table),
        review,
        resolutions,
        diff_stat(args.diff_base),
    )
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
