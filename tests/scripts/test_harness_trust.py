"""Unit tests for scripts/harness/trust.py (gh mocked)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import trust

BOT = {"login": "github-actions[bot]", "name": "github-actions[bot]"}
HUMAN = {"login": "luarss", "name": "Song Luar"}


def _commit(*authors: dict) -> dict:
    return {"oid": "x", "authors": list(authors)}


class FakeGh:
    def __init__(self, prs: list[dict], commits: dict[int, list[dict]], body: str = "", list_fails: bool = False) -> None:
        self.prs = prs
        self.commits = commits
        self.body = body
        self.list_fails = list_fails
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[1:3] == ["pr", "list"]:
            if self.list_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 502")
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(self.prs), stderr="")
        if cmd[1:3] == ["pr", "view"]:
            fields = cmd[cmd.index("--json") + 1]
            if fields == "commits":
                payload = {"commits": self.commits.get(int(cmd[3]), [])}
            else:
                payload = {"body": self.body}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if cmd[1:3] == ["pr", "merge"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)


def _prs(count: int) -> list[dict]:
    return [{"number": n, "mergedAt": f"2026-09-{n:02d}T00:00:00Z", "commits": [], "reviews": []} for n in range(1, count + 1)]


def _all_bot(count: int) -> dict[int, list[dict]]:
    return {n: [_commit(BOT)] for n in range(1, count + 1)}


def test_streak_counts_recent_unedited_prs() -> None:
    commits = _all_bot(5)
    commits[2] = [_commit(BOT), _commit(HUMAN)]
    result = trust.compute_trust(FakeGh(_prs(5), commits))
    assert result["streak"] == 3
    assert result["last_human_edit_pr"] == 2
    assert result["auto_merge_rung1"] is False


def test_threshold_unlocks_auto_merge(tmp_path: Path) -> None:
    out = tmp_path / "trust.json"
    assert trust.main(["--out", str(out)], runner=FakeGh(_prs(10), _all_bot(10))) == 0
    written = json.loads(out.read_text())
    assert written["auto_merge_rung1"] is True
    assert written["streak"] == 10
    assert written["threshold"] == 10


def test_human_commit_before_bot_refresh_is_not_an_edit() -> None:
    assert trust.human_edited([_commit(HUMAN), _commit(BOT)]) is False


def test_pr_without_bot_commit_counts_as_human() -> None:
    assert trust.human_edited([_commit(HUMAN)]) is True
    assert trust.human_edited([]) is True


def test_bot_identified_by_email() -> None:
    assert trust.is_bot_commit(_commit({"login": "", "email": "41898282+github-actions[bot]@users.noreply.github.com"}))


def test_gh_failure_fails_closed_on_trust(tmp_path: Path) -> None:
    out = tmp_path / "trust.json"
    assert trust.main(["--out", str(out)], runner=FakeGh([], {}, list_fails=True)) == 0
    written = json.loads(out.read_text())
    assert written["streak"] == 0
    assert written["auto_merge_rung1"] is False
    assert "502" in written["error"]


def test_enable_automerge_requires_trust_and_rung_one(tmp_path: Path) -> None:
    gh = FakeGh(_prs(10), _all_bot(10), body="Cluster: ingest/false_keep/arch\n- **Rung:** 1\n")
    trust.main(["--out", str(tmp_path / "t.json"), "--enable-automerge", "55"], runner=gh)
    assert ["gh", "pr", "merge", "--auto", "--squash", "55"] in gh.calls


def test_enable_automerge_refuses_higher_rung(tmp_path: Path) -> None:
    gh = FakeGh(_prs(10), _all_bot(10), body="Rung: 2\n")
    trust.main(["--out", str(tmp_path / "t.json"), "--enable-automerge", "55"], runner=gh)
    assert not [call for call in gh.calls if call[1:3] == ["pr", "merge"]]


def test_enable_automerge_refuses_without_streak(tmp_path: Path) -> None:
    gh = FakeGh(_prs(3), _all_bot(3), body="Rung: 1\n")
    trust.main(["--out", str(tmp_path / "t.json"), "--enable-automerge", "55"], runner=gh)
    assert not [call for call in gh.calls if call[1:3] == ["pr", "merge"]]


def test_declared_rung_parsing() -> None:
    assert trust.declared_rung("intro\nRung: 1\n") == 1
    assert trust.declared_rung("**Rung**: `3`") == 3
    assert trust.declared_rung("no rung here") is None
