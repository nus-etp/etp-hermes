"""Unit tests for scripts/harness/pr_body.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import pr_body

CLUSTER = {
    "cluster": "ingest/false_keep/weak_term/arch",
    "size": 4,
    "weighted_size": 6,
    "companies": ["Arch"],
    "root_cause": "Arch is a dictionary word with no exclude_terms.",
}
PROPOSAL = {
    "rung": 1,
    "files": ["data/companies.json"],
    "rationale": "Config-only fix.",
    "new_scenario_ids": ["ingest-2026-10-04-0001"],
}
VERDICT = {"accept": True, "reasons": ["all scenarios pass on nvidia and zai"], "per_runtime": {}}
TABLE = "| runtime | passed |\n|---|---|\n| nvidia | 5/5 |"
REVIEW = {
    "findings": [
        {"severity": "blocking", "claim": "Overfits to the card titles.", "evidence_scenario_ids": None},
        {"severity": "blocking", "claim": "Breaks a passing case.", "evidence_scenario_ids": ["ingest-2026-10-01-0002"]},
        {"severity": "minor", "claim": "Wording | nit.", "evidence_scenario_ids": None},
        {"severity": "blocking", "claim": "Leaks to other companies.", "evidence_scenario_ids": None},
    ]
}
RESOLUTIONS = {
    "Overfits to the card titles.": {"resolution": "fixed", "scenario_id": "ingest-2026-10-04-0002"},
    "Leaks to other companies.": {"resolution": "rejected", "scenario_id": None},
}


@pytest.fixture()
def harness_dir(tmp_path: Path) -> Path:
    files = {
        "cluster.json": CLUSTER,
        "proposal.json": PROPOSAL,
        "verdict.json": VERDICT,
        "review.json": REVIEW,
        "resolutions.json": RESOLUTIONS,
    }
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload))
    (tmp_path / "replay-table.md").write_text(TABLE)
    return tmp_path


def args_for(directory: Path) -> list[str]:
    return [
        "--cluster", str(directory / "cluster.json"),
        "--verdict", str(directory / "verdict.json"),
        "--table", str(directory / "replay-table.md"),
        "--review", str(directory / "review.json"),
        "--proposal", str(directory / "proposal.json"),
        "--resolutions", str(directory / "resolutions.json"),
    ]


def test_full_body(harness_dir: Path, monkeypatch, capsys):
    monkeypatch.setattr(pr_body, "diff_stat", lambda base: " data/companies.json | 2 +-")
    assert pr_body.main(args_for(harness_dir)) == 0
    body = capsys.readouterr().out
    assert "`ingest/false_keep/weak_term/arch`" in body
    assert "Arch is a dictionary word" in body
    assert "- Rung: 1" in body
    assert "`data/companies.json`" in body
    assert "`ingest-2026-10-04-0001`" in body
    assert "data/companies.json | 2 +-" in body
    assert "Verdict: **accept**" in body
    assert "| nvidia | 5/5 |" in body
    assert "fixed (`ingest-2026-10-04-0002`)" in body
    assert "covered by cited scenarios" in body
    assert "Wording \\| nit." in body
    assert "**unresolved**" in body


def test_unresolved_lists_only_open_blocking(harness_dir: Path, capsys):
    assert pr_body.main([*args_for(harness_dir), "--unresolved"]) == 0
    assert capsys.readouterr().out == "- Leaks to other companies.\n"


def test_unresolved_empty_when_review_missing(tmp_path: Path, capsys):
    assert pr_body.main(["--review", str(tmp_path / "none.json"), "--resolutions", str(tmp_path / "x.json"), "--unresolved"]) == 0
    assert capsys.readouterr().out == ""


def test_missing_inputs_render_placeholders(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(pr_body, "diff_stat", lambda base: "")
    assert pr_body.main(args_for(tmp_path)) == 0
    body = capsys.readouterr().out
    assert "Root cause: _not available_" in body
    assert "## Diff summary\n\n_not available_" in body
    assert "Verdict: _not available_" in body


def test_reject_verdict_and_empty_findings():
    body = pr_body.render_body(CLUSTER, PROPOSAL, {"accept": False, "reasons": ["zai: 3/5"]}, "", {"findings": []}, {}, "")
    assert "Verdict: **reject**" in body
    assert "- zai: 3/5" in body
    assert "_No findings._" in body


def test_malformed_json_fails_open(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert pr_body.load_json(path) == {}
    path.write_text("[1, 2]")
    assert pr_body.load_json(path) == {}
