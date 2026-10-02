"""Unit tests for scripts/harness/on_merge.py (gh mocked)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import ledger, on_merge

BODY = """## Harness proposal

- **Cluster:** `ingest/false_keep/arch`
- **Rung:** 1
- Files: data/companies.json, prompts/ingest.md
- New scenarios: ingest-2026-10-03-0007, ingest-2026-10-03-0008

Root cause: weak term matched in description.
"""


class FakeGh:
    def __init__(self, body: str = BODY, view_fails: bool = False) -> None:
        self.body = body
        self.view_fails = view_fails
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[1:3] == ["pr", "view"] and "body,mergeCommit,mergedAt" in cmd:
            if self.view_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
            payload = {"body": self.body, "mergeCommit": {"oid": "deadbeef"}, "mergedAt": "2026-10-04T08:00:00Z"}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if cmd[1:3] == ["pr", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        raise AssertionError(cmd)


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--pr", "42",
        "--ledger", str(tmp_path / "ledger.jsonl"),
        "--trust-out", str(tmp_path / "trust.json"),
        *extra,
    ]


def test_parse_body_reads_pr_body_lines() -> None:
    assert on_merge.parse_body(BODY) == {
        "cluster": "ingest/false_keep/arch",
        "rung": 1,
        "files": ["data/companies.json", "prompts/ingest.md"],
        "scenario_ids": ["ingest-2026-10-03-0007", "ingest-2026-10-03-0008"],
    }


def test_parse_body_handles_none_scenarios() -> None:
    parsed = on_merge.parse_body("Cluster: a/b/c\nRung: 2\nFiles: x\nNew scenarios: none\n")
    assert parsed["scenario_ids"] == []
    assert parsed["rung"] == 2


def test_merge_appends_ledger_row_and_writes_trust(tmp_path: Path) -> None:
    gh = FakeGh()
    assert on_merge.main(_argv(tmp_path), runner=gh) == 0
    rows = ledger.read_rows(tmp_path / "ledger.jsonl")
    assert len(rows) == 1
    assert rows[0]["pr"] == 42
    assert rows[0]["merge_sha"] == "deadbeef"
    assert rows[0]["merged_at"] == "2026-10-04T08:00:00Z"
    assert rows[0]["rung"] == 1
    assert rows[0]["gain"]["scenarios_fixed"] == 2
    assert json.loads((tmp_path / "trust.json").read_text())["streak"] == 0


def test_merge_uses_verdict_when_given(tmp_path: Path) -> None:
    verdict = tmp_path / "verdict.json"
    verdict.write_text(json.dumps({"accept": True, "per_runtime": {"nvidia": {"failed_ids": [], "champion_right": 1, "proposal_right": 4, "volume_ratio": 1.1}}}))
    on_merge.main(_argv(tmp_path, "--verdict", str(verdict)), runner=FakeGh())
    assert ledger.read_rows(tmp_path / "ledger.jsonl")[0]["gain"]["window_delta"] == 3


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    on_merge.main(_argv(tmp_path), runner=FakeGh())
    on_merge.main(_argv(tmp_path), runner=FakeGh())
    assert len(ledger.read_rows(tmp_path / "ledger.jsonl")) == 1


def test_body_without_cluster_fails(tmp_path: Path) -> None:
    assert on_merge.main(_argv(tmp_path), runner=FakeGh(body="just a PR")) == 1
    assert not (tmp_path / "ledger.jsonl").exists()


def test_gh_failure_fails(tmp_path: Path) -> None:
    assert on_merge.main(_argv(tmp_path), runner=FakeGh(view_fails=True)) == 1
