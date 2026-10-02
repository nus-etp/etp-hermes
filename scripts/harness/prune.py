#!/usr/bin/env python3
"""Monthly prune — revert each old harness change and check nothing needed it.

For every row in signals/harness/ledger.jsonl merged more than
`--older-than-days` ago and not already pruned:

1. create a detached temp worktree at `--base` (default `main`) and
   `git revert --no-commit <merge_sha>` into it (conflict → skip the row);
2. run `simulate.py` (layer from the cluster key) against the reverted
   worktree on both runtimes (`nvidia`, `zai`), with `--prompt-path` inside
   the worktree and the *current* active scenario corpus + replay window;
3. run `grade.py` with champion = current `--base` sims (computed once per
   layer per run, cached under data/harness/sim/prune/) and proposal = the
   reverted sims.

If the reverted arm is accepted (every active scenario still passes, no
significant window regression), the change bought nothing: the row becomes a
candidate in data/harness/prune-report.json, and with `--open-prs` a PR from
`harness/prune-<pr>` carrying the revert is opened (label `harness:prune`,
assignee `luarss`) and the ledger row is stamped `pruned_pr`.

`--dry-run` only lists the eligible rows; no git, simulate or gh calls.
Fail open per row. Every git/gh/simulate call goes through an injectable
runner so tests never shell out. Pure stdlib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIMES = ("nvidia", "zai")
PROMPT_FOR_LAYER = {
    "ingest": "prompts/ingest.md",
    "agent_supplement": "prompts/agent_supplement.md",
    "synthesis": "prompts/synthesis.md",
}
PRUNE_LABEL = "harness:prune"
PRUNE_ASSIGNEE = "luarss"
WINDOW_DAYS = 14

Runner = Callable[..., subprocess.CompletedProcess]


class RowSkipped(Exception):
    pass


def default_runner(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def layer_of(cluster: str) -> str:
    return str(cluster).split("/", 1)[0]


def eligible_rows(rows: list[dict], older_than_days: int, now: dt.datetime) -> list[dict]:
    cutoff = now - dt.timedelta(days=older_than_days)
    selected: list[dict] = []
    for row in rows:
        if row.get("pruned_pr") or not row.get("merge_sha"):
            continue
        try:
            merged_at = ledger.parse_iso(str(row.get("merged_at")))
        except ValueError:
            continue
        if merged_at <= cutoff:
            selected.append(row)
    return selected


class Pruner:
    def __init__(
        self,
        repo_root: Path,
        runner: Runner = default_runner,
        base: str = "main",
        python: str = sys.executable,
        workspace: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.runner = runner
        self.base = base
        self.python = python
        self.workspace = workspace
        self.sim_root = repo_root / "data" / "harness" / "sim" / "prune"
        self.champion_cache: dict[str, list[Path]] = {}
        self.champion_failures: dict[str, str] = {}

    def run(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        return self.runner(cmd, cwd=cwd)

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return self.run(["git", *args], cwd=cwd or self.repo_root)

    def require(self, result: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise RowSkipped(f"{what} failed: {detail[-1] if detail else result.returncode}")
        return result

    def simulate(self, layer: str, prompt_path: Path, runtime: str, out: Path, cwd: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.python,
            str(cwd / "scripts" / "harness" / "simulate.py"),
            "--layer", layer,
            "--prompt-path", str(prompt_path),
            "--runtime", runtime,
            "--out", str(out),
            "--scenarios", str(self.repo_root / "evals" / "scenarios"),
            "--window", str(self.repo_root / "data" / "replay"),
            "--window-days", str(WINDOW_DAYS),
        ]
        self.require(self.run(cmd, cwd=cwd), f"simulate {layer}/{runtime}")
        if not out.exists():
            raise RowSkipped(f"simulate {layer}/{runtime} wrote no output")
        return out

    def prompt_path(self, layer: str, root: Path) -> Path:
        if layer not in PROMPT_FOR_LAYER:
            raise RowSkipped(f"unknown layer {layer!r}")
        return root / PROMPT_FOR_LAYER[layer]

    def champion_sims(self, layer: str) -> list[Path]:
        if layer in self.champion_failures:
            raise RowSkipped(self.champion_failures[layer])
        if layer not in self.champion_cache:
            prompt = self.prompt_path(layer, self.repo_root)
            try:
                outputs = [
                    self.simulate(layer, prompt, runtime, self.sim_root / layer / f"champion-{runtime}.json", self.repo_root)
                    for runtime in RUNTIMES
                ]
            except RowSkipped as failure:
                self.champion_failures[layer] = f"champion {failure}"
                raise RowSkipped(self.champion_failures[layer]) from failure
            self.champion_cache[layer] = outputs
        return self.champion_cache[layer]

    def grade(self, champion: list[Path], proposal: list[Path], out_dir: Path) -> tuple[dict, Path]:
        verdict_path = out_dir / "verdict.json"
        table_path = out_dir / "replay-table.md"
        cmd = [
            self.python,
            str(self.repo_root / "scripts" / "harness" / "grade.py"),
            "--champion", *map(str, champion),
            "--proposal", *map(str, proposal),
            "--out-table", str(table_path),
            "--out-verdict", str(verdict_path),
        ]
        self.require(self.run(cmd, cwd=self.repo_root), "grade")
        verdict = ledger.load_verdict(verdict_path)
        if verdict is None:
            raise RowSkipped("grade wrote no readable verdict")
        return verdict, table_path

    def revert_args(self, merge_sha: str, worktree: Path) -> list[str]:
        parents = self.git("rev-list", "--parents", "-n", "1", merge_sha, cwd=worktree)
        self.require(parents, f"rev-list {merge_sha}")
        is_merge_commit = len(parents.stdout.split()) > 2
        return ["revert", "--no-commit", *(["-m", "1"] if is_merge_commit else []), merge_sha]

    def create_worktree(self) -> Path:
        parent = Path(tempfile.mkdtemp(prefix="harness-prune-", dir=self.workspace))
        worktree = parent / "worktree"
        self.require(self.git("worktree", "add", "--detach", str(worktree), self.base), "worktree add")
        return worktree

    def remove_worktree(self, worktree: Path) -> None:
        self.git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(worktree.parent, ignore_errors=True)

    def apply_revert(self, row: dict, worktree: Path) -> None:
        result = self.git(*self.revert_args(row["merge_sha"], worktree), cwd=worktree)
        if result.returncode != 0:
            self.git("revert", "--abort", cwd=worktree)
            raise RowSkipped(f"revert of {row['merge_sha']} conflicts with {self.base}")

    def evaluate(self, row: dict, worktree: Path) -> tuple[dict, Path]:
        layer = layer_of(row.get("cluster", ""))
        champion = self.champion_sims(layer)
        prompt = self.prompt_path(layer, worktree)
        out_dir = self.sim_root / f"pr-{row['pr']}"
        proposal = [
            self.simulate(layer, prompt, runtime, out_dir / f"proposal-{runtime}.json", worktree)
            for runtime in RUNTIMES
        ]
        return self.grade(champion, proposal, out_dir)

    def existing_pr(self, branch: str) -> int | None:
        result = self.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"])
        if result.returncode != 0:
            return None
        try:
            listed = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None
        return int(listed[0]["number"]) if listed else None

    def open_pr(self, row: dict, worktree: Path, body: str) -> int:
        branch = f"harness/prune-{row['pr']}"
        self.require(self.git("switch", "-c", branch, cwd=worktree), "switch")
        message = f"revert: prune harness change from #{row['pr']} ({row.get('cluster')})"
        self.require(self.git("commit", "-m", message, cwd=worktree), "commit")
        self.require(self.git("push", "-f", "origin", branch, cwd=worktree), "push")
        existing = self.existing_pr(branch)
        if existing is not None:
            return existing
        body_path = worktree.parent / "pr-body.md"
        body_path.write_text(body, encoding="utf-8")
        cmd = [
            "gh", "pr", "create",
            "--base", self.base,
            "--head", branch,
            "--title", message,
            "--body-file", str(body_path),
            "--label", PRUNE_LABEL,
            "--assignee", PRUNE_ASSIGNEE,
        ]
        created = self.require(self.run(cmd, cwd=worktree), "gh pr create")
        match = re.search(r"/pull/(\d+)", created.stdout or "")
        if not match:
            raise RowSkipped("gh pr create returned no PR URL")
        return int(match.group(1))


def verdict_summary(verdict: dict) -> dict:
    per_runtime = {}
    for runtime, stats in (verdict.get("per_runtime") or {}).items():
        if not isinstance(stats, dict):
            continue
        per_runtime[runtime] = {
            key: stats.get(key)
            for key in ("scenarios_passed", "scenarios_total", "champion_right", "proposal_right", "p_value", "volume_ratio")
        }
    return {"accept": bool(verdict.get("accept")), "reasons": list(verdict.get("reasons") or []), "per_runtime": per_runtime}


def evidence_body(row: dict, summary: dict, table_path: Path) -> str:
    gain = row.get("gain") or {}
    lines = [
        f"Monthly harness prune: replaying the active scenario corpus with #{row['pr']} reverted "
        "shows the change is no longer needed — every active scenario still passes on both runtimes "
        "and the fresh window shows no significant regression.",
        "",
        f"Reverts: #{row['pr']} (`{row['merge_sha']}`), merged {row.get('merged_at')}",
        f"Cluster: {row.get('cluster')}",
        f"Rung: {row.get('rung')}",
        f"Files: {', '.join(row.get('files') or [])}",
        f"Scenarios added by the change: {', '.join(row.get('scenario_ids') or []) or 'none'}",
        f"Gain recorded at merge: scenarios_fixed={gain.get('scenarios_fixed')}, "
        f"window_delta={gain.get('window_delta')}, volume_ratio={gain.get('volume_ratio')}",
        "",
        "| runtime | scenarios passed | champion right | reverted right | p | volume ratio |",
        "|---|---|---|---|---|---|",
    ]
    for runtime, stats in summary["per_runtime"].items():
        lines.append(
            f"| {runtime} | {stats.get('scenarios_passed')}/{stats.get('scenarios_total')} | "
            f"{stats.get('champion_right')} | {stats.get('proposal_right')} | {stats.get('p_value')} | "
            f"{stats.get('volume_ratio')} |"
        )
    if summary["reasons"]:
        lines += ["", "Grader reasons:", *[f"- {reason}" for reason in summary["reasons"]]]
    if table_path.exists():
        lines += ["", "<details><summary>Replay table</summary>", "", table_path.read_text(encoding="utf-8"), "</details>"]
    return "\n".join(lines) + "\n"


def prune_row(pruner: Pruner, row: dict, open_prs: bool, ledger_path: Path) -> tuple[str, dict]:
    worktree = pruner.create_worktree()
    try:
        pruner.apply_revert(row, worktree)
        verdict, table_path = pruner.evaluate(row, worktree)
        summary = verdict_summary(verdict)
        entry = {"pr": row["pr"], "cluster": row.get("cluster"), "merge_sha": row["merge_sha"], "verdict_summary": summary}
        if not summary["accept"]:
            return "kept", entry
        if open_prs:
            pruned_pr = pruner.open_pr(row, worktree, evidence_body(row, summary, table_path))
            ledger.mark_pruned(row["pr"], pruned_pr, ledger_path)
            entry["pruned_pr"] = pruned_pr
        return "candidate", entry
    finally:
        pruner.remove_worktree(worktree)


def run_prune(
    pruner: Pruner,
    ledger_path: Path,
    older_than_days: int,
    open_prs: bool,
    dry_run: bool,
    now: dt.datetime,
) -> dict:
    rows = eligible_rows(ledger.read_rows(ledger_path), older_than_days, now)
    report: dict = {
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "older_than_days": older_than_days,
        "dry_run": dry_run,
        "eligible": [row["pr"] for row in rows],
        "candidates": [],
        "kept": [],
        "skipped": [],
    }
    if dry_run:
        return report
    for row in rows:
        try:
            outcome, entry = prune_row(pruner, row, open_prs, ledger_path)
        except RowSkipped as skip:
            report["skipped"].append({"pr": row["pr"], "reason": str(skip)})
            continue
        except Exception as error:
            report["skipped"].append({"pr": row["pr"], "reason": f"{type(error).__name__}: {error}"})
            continue
        report["candidates" if outcome == "candidate" else "kept"].append(entry)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--open-prs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--base", default="main")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--report", default=None)
    return parser


def main(argv: list[str] | None = None, runner: Runner = default_runner, now: dt.datetime | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    ledger_path = Path(args.ledger) if args.ledger else repo_root / "signals" / "harness" / "ledger.jsonl"
    report_path = Path(args.report) if args.report else repo_root / "data" / "harness" / "prune-report.json"
    pruner = Pruner(repo_root, runner=runner, base=args.base)
    report = run_prune(
        pruner,
        ledger_path,
        args.older_than_days,
        args.open_prs,
        args.dry_run,
        now or dt.datetime.now(dt.timezone.utc),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"prune: {len(report['eligible'])} eligible, {len(report['candidates'])} prune candidates, "
        f"{len(report['kept'])} kept, {len(report['skipped'])} skipped"
        + (" (dry run)" if args.dry_run else "")
    )
    for skipped in report["skipped"]:
        print(f"prune: skipped #{skipped['pr']}: {skipped['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
