"""Unit tests for scripts/harness/prune.py (subprocess, simulate and grade mocked)."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from harness import ledger, prune

NOW = dt.datetime(2026, 11, 1, 7, 0, tzinfo=dt.timezone.utc)


def _completed(cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _flag(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


class FakeRunner:
    def __init__(self, accept_prs=(), conflict_shas=(), merge_commit_shas=(), failing_runtime=None) -> None:
        self.accept_prs = set(accept_prs)
        self.conflict_shas = set(conflict_shas)
        self.merge_commit_shas = set(merge_commit_shas)
        self.failing_runtime = failing_runtime
        self.calls: list[tuple[list[str], Path | None]] = []
        self.created_body = ""

    def __call__(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.calls.append((list(cmd), cwd))
        if cmd[0] == "git":
            return self.git(cmd)
        if cmd[0] == "gh":
            return self.gh(cmd)
        if cmd[1].endswith("simulate.py"):
            return self.simulate(cmd)
        if cmd[1].endswith("grade.py"):
            return self.grade(cmd)
        raise AssertionError(f"unexpected command {cmd}")

    def git(self, cmd: list[str]) -> subprocess.CompletedProcess:
        if cmd[1:3] == ["worktree", "add"]:
            worktree = Path(cmd[4])
            (worktree / "prompts").mkdir(parents=True)
            (worktree / "prompts" / "ingest.md").write_text("reverted prompt")
        if cmd[1] == "rev-list":
            sha = cmd[-1]
            parents = f"{sha} p1 p2" if sha in self.merge_commit_shas else f"{sha} p1"
            return _completed(cmd, stdout=parents + "\n")
        if cmd[1] == "revert" and "--abort" not in cmd and cmd[-1] in self.conflict_shas:
            return _completed(cmd, returncode=1, stderr="CONFLICT")
        return _completed(cmd)

    def gh(self, cmd: list[str]) -> subprocess.CompletedProcess:
        if cmd[1:3] == ["pr", "list"]:
            return _completed(cmd, stdout="[]")
        if cmd[1:3] == ["pr", "create"]:
            self.created_body = Path(_flag(cmd, "--body-file")).read_text()
            return _completed(cmd, stdout="https://github.com/nus-etp/etp-hermes/pull/77\n")
        return _completed(cmd)

    def simulate(self, cmd: list[str]) -> subprocess.CompletedProcess:
        runtime = _flag(cmd, "--runtime")
        if runtime == self.failing_runtime:
            return _completed(cmd, returncode=2, stderr="no key for runtime")
        out = Path(_flag(cmd, "--out"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"arm": out.stem, "runtime": runtime, "layer": _flag(cmd, "--layer"), "scenario_results": {}, "window_results": []}))
        return _completed(cmd)

    def grade(self, cmd: list[str]) -> subprocess.CompletedProcess:
        verdict_path = Path(_flag(cmd, "--out-verdict"))
        pr_number = int(verdict_path.parent.name.removeprefix("pr-"))
        accept = pr_number in self.accept_prs
        stats = {"scenarios_passed": 5, "scenarios_total": 5, "failed_ids": [], "champion_right": 8, "proposal_right": 8, "p_value": 1.0, "volume_ratio": 1.0, "cost_delta": 0.0}
        verdict = {"accept": accept, "reasons": [] if accept else ["scenario ingest-x failed"], "per_runtime": {"nvidia": stats, "zai": stats}}
        verdict_path.write_text(json.dumps(verdict))
        Path(_flag(cmd, "--out-table")).write_text("| table |\n")
        return _completed(cmd)

    def commands(self, *prefix: str) -> list[list[str]]:
        return [cmd for cmd, _ in self.calls if cmd[: len(prefix)] == list(prefix)]

    def scripts(self, name: str) -> list[list[str]]:
        return [cmd for cmd, _ in self.calls if len(cmd) > 1 and cmd[1].endswith(name)]


def _row(pr: int, merged_at: str = "2026-09-01T00:00:00Z", **extra) -> dict:
    row = {
        "merged_at": merged_at,
        "pr": pr,
        "cluster": "ingest/false_keep/arch",
        "rung": 1,
        "files": ["data/companies.json"],
        "scenario_ids": [f"ingest-2026-08-01-{pr:04d}"],
        "gain": {"scenarios_fixed": 1, "window_delta": 2, "volume_ratio": 1.0},
        "merge_sha": f"sha{pr}",
    }
    row.update(extra)
    return row


def _setup(tmp_path: Path, rows: list[dict]) -> Path:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "ingest.md").write_text("champion prompt")
    ledger_path = tmp_path / "signals" / "harness" / "ledger.jsonl"
    ledger.write_rows(rows, ledger_path)
    return ledger_path


def _run(tmp_path: Path, runner: FakeRunner, *flags: str) -> dict:
    assert prune.main(["--repo-root", str(tmp_path), *flags], runner=runner, now=NOW) == 0
    return json.loads((tmp_path / "data" / "harness" / "prune-report.json").read_text())


def test_accepted_revert_opens_pr_and_stamps_ledger(tmp_path: Path) -> None:
    ledger_path = _setup(tmp_path, [_row(10)])
    runner = FakeRunner(accept_prs={10})
    report = _run(tmp_path, runner, "--open-prs")
    assert [c["pr"] for c in report["candidates"]] == [10]
    assert report["candidates"][0]["verdict_summary"]["accept"] is True
    create = runner.commands("gh", "pr", "create")[0]
    assert _flag(create, "--label") == "harness:prune"
    assert _flag(create, "--assignee") == "luarss"
    assert _flag(create, "--head") == "harness/prune-10"
    assert "Reverts: #10 (`sha10`)" in runner.created_body
    assert "| table |" in runner.created_body
    assert ["git", "revert", "--no-commit", "sha10"] in runner.commands("git", "revert")
    assert ledger.read_rows(ledger_path)[0]["pruned_pr"] == 77


def test_proposal_sims_point_into_worktree(tmp_path: Path) -> None:
    _setup(tmp_path, [_row(10)])
    runner = FakeRunner(accept_prs={10})
    _run(tmp_path, runner)
    proposal_sims = [cmd for cmd in runner.scripts("simulate.py") if "proposal-" in _flag(cmd, "--out")]
    assert sorted(_flag(cmd, "--runtime") for cmd in proposal_sims) == ["nvidia", "zai"]
    for cmd in proposal_sims:
        assert "harness-prune-" in _flag(cmd, "--prompt-path")
        assert _flag(cmd, "--prompt-path").endswith("prompts/ingest.md")
    grade = runner.scripts("grade.py")[0]
    champions = grade[grade.index("--champion") + 1 : grade.index("--proposal")]
    assert [Path(p).name for p in champions] == ["champion-nvidia.json", "champion-zai.json"]


def test_rejected_revert_is_kept_without_pr(tmp_path: Path) -> None:
    ledger_path = _setup(tmp_path, [_row(11)])
    runner = FakeRunner(accept_prs=set())
    report = _run(tmp_path, runner, "--open-prs")
    assert report["candidates"] == []
    assert [k["pr"] for k in report["kept"]] == [11]
    assert runner.commands("gh", "pr", "create") == []
    assert "pruned_pr" not in ledger.read_rows(ledger_path)[0]


def test_champion_sims_computed_once_per_run(tmp_path: Path) -> None:
    _setup(tmp_path, [_row(10), _row(12)])
    runner = FakeRunner(accept_prs={10, 12})
    report = _run(tmp_path, runner)
    champion_sims = [cmd for cmd in runner.scripts("simulate.py") if "champion-" in _flag(cmd, "--out")]
    assert len(champion_sims) == 2
    assert [c["pr"] for c in report["candidates"]] == [10, 12]


def test_conflict_skips_row_and_continues(tmp_path: Path) -> None:
    _setup(tmp_path, [_row(10), _row(12)])
    runner = FakeRunner(accept_prs={10, 12}, conflict_shas={"sha10"})
    report = _run(tmp_path, runner)
    assert [s["pr"] for s in report["skipped"]] == [10]
    assert "conflict" in report["skipped"][0]["reason"]
    assert [c["pr"] for c in report["candidates"]] == [12]
    assert runner.commands("git", "revert", "--abort")
    assert len(runner.commands("git", "worktree", "remove")) == 2


def test_merge_commit_reverted_with_mainline(tmp_path: Path) -> None:
    _setup(tmp_path, [_row(10)])
    runner = FakeRunner(accept_prs={10}, merge_commit_shas={"sha10"})
    _run(tmp_path, runner)
    assert ["git", "revert", "--no-commit", "-m", "1", "sha10"] in runner.commands("git", "revert")


def test_simulate_failure_skips_row(tmp_path: Path) -> None:
    _setup(tmp_path, [_row(10)])
    runner = FakeRunner(accept_prs={10}, failing_runtime="zai")
    report = _run(tmp_path, runner, "--open-prs")
    assert report["candidates"] == []
    assert "simulate" in report["skipped"][0]["reason"]


def test_only_old_unpruned_rows_are_eligible(tmp_path: Path) -> None:
    rows = [_row(10), _row(11, merged_at="2026-10-20T00:00:00Z"), _row(12, pruned_pr=50), _row(13, merge_sha=None)]
    _setup(tmp_path, rows)
    runner = FakeRunner()
    report = _run(tmp_path, runner, "--dry-run")
    assert report["eligible"] == [10]
    assert runner.calls == []


def test_without_open_prs_reports_but_does_not_stamp(tmp_path: Path) -> None:
    ledger_path = _setup(tmp_path, [_row(10)])
    runner = FakeRunner(accept_prs={10})
    report = _run(tmp_path, runner)
    assert [c["pr"] for c in report["candidates"]] == [10]
    assert runner.commands("gh") == []
    assert "pruned_pr" not in ledger.read_rows(ledger_path)[0]


def test_evidence_body_lists_runtimes_and_change(tmp_path: Path) -> None:
    summary = prune.verdict_summary(
        {"accept": True, "reasons": ["ok"], "per_runtime": {"nvidia": {"scenarios_passed": 3, "scenarios_total": 3}}}
    )
    body = prune.evidence_body(_row(10), summary, tmp_path / "missing.md")
    assert "Reverts: #10" in body
    assert "| nvidia | 3/3 |" in body
    assert "Rung: 1" in body
