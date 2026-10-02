"""Unit tests for scripts/harness/ledger.py."""

from __future__ import annotations

import json
from pathlib import Path

from harness import ledger

VERDICT = {
    "accept": True,
    "reasons": [],
    "per_runtime": {
        "nvidia": {"failed_ids": [], "champion_right": 10, "proposal_right": 13, "volume_ratio": 0.95},
        "zai": {"failed_ids": ["ingest-2026-10-03-0002"], "champion_right": 9, "proposal_right": 10, "volume_ratio": 0.9},
    },
}


def _append(tmp_path: Path, *extra: str) -> int:
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(VERDICT))
    return ledger.main(
        [
            "--ledger", str(tmp_path / "ledger.jsonl"),
            "append",
            "--pr", "42",
            "--cluster", "ingest/false_keep/arch",
            "--rung", "1",
            "--files", "data/companies.json",
            "--scenarios", "ingest-2026-10-03-0001,ingest-2026-10-03-0002",
            "--verdict", str(verdict_path),
            "--merge-sha", "abc123",
            "--merged-at", "2026-09-01T10:00:00Z",
            *extra,
        ]
    )


def test_append_writes_row_with_gain(tmp_path: Path) -> None:
    assert _append(tmp_path) == 0
    rows = ledger.read_rows(tmp_path / "ledger.jsonl")
    assert rows == [
        {
            "merged_at": "2026-09-01T10:00:00Z",
            "pr": 42,
            "cluster": "ingest/false_keep/arch",
            "rung": 1,
            "files": ["data/companies.json"],
            "scenario_ids": ["ingest-2026-10-03-0001", "ingest-2026-10-03-0002"],
            "gain": {"scenarios_fixed": 1, "window_delta": 4, "volume_ratio": 0.9},
            "merge_sha": "abc123",
        }
    ]


def test_append_is_idempotent_by_pr(tmp_path: Path) -> None:
    _append(tmp_path)
    ledger.mark_pruned(42, 99, tmp_path / "ledger.jsonl")
    _append(tmp_path)
    rows = ledger.read_rows(tmp_path / "ledger.jsonl")
    assert len(rows) == 1
    assert rows[0]["pruned_pr"] == 99


def test_missing_verdict_degrades_gain() -> None:
    gain = ledger.gain_from_verdict(None, ["a", "b"])
    assert gain == {"scenarios_fixed": 2, "window_delta": None, "volume_ratio": None}


def test_read_rows_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"pr": 1}\nnot json\n\n{"pr": 2}\n')
    assert [row["pr"] for row in ledger.read_rows(path)] == [1, 2]


def test_list_prints_rows(tmp_path: Path, capsys) -> None:
    _append(tmp_path)
    capsys.readouterr()
    assert ledger.main(["--ledger", str(tmp_path / "ledger.jsonl"), "list", "--json"]) == 0
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert printed[0]["pr"] == 42


def test_list_empty(tmp_path: Path, capsys) -> None:
    assert ledger.main(["list", "--ledger", str(tmp_path / "none.jsonl")]) == 0
    assert "empty" in capsys.readouterr().out


def test_split_list_accepts_spaces_and_commas() -> None:
    assert ledger.split_list(["a, b", "c", "a"]) == ["a", "b", "c"]
