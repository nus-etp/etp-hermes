"""Unit tests for scripts/harness/rejections.py."""

from __future__ import annotations

import json
from pathlib import Path

from harness import rejections


def write_inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "cluster": tmp_path / "cluster.json",
        "verdict": tmp_path / "verdict.json",
        "proposal": tmp_path / "proposal.json",
        "review": tmp_path / "review.json",
        "resolutions": tmp_path / "resolutions.json",
        "table": tmp_path / "replay-table.md",
    }
    paths["cluster"].write_text(json.dumps({"cluster": "ingest/false_keep/acme/example.com"}))
    paths["verdict"].write_text(json.dumps({"accept": False, "reasons": ["nvidia: window regression"]}))
    paths["proposal"].write_text(json.dumps({"rung": 2, "files": ["prompts/ingest.md"]}))
    paths["review"].write_text(
        json.dumps(
            {
                "findings": [
                    {"severity": "blocking", "claim": "Overfits to Acme", "evidence_scenario_ids": None},
                    {"severity": "blocking", "claim": "Has evidence", "evidence_scenario_ids": ["ingest-1"]},
                    {"severity": "minor", "claim": "Nit"},
                ]
            }
        )
    )
    paths["table"].write_text("| runtime | result |\n|---|---|\n| nvidia | fail |\n")
    return paths


def run_append(tmp_path: Path, run_id: str = "42", **overrides: Path) -> Path:
    paths = {**write_inputs(tmp_path), **overrides}
    out = tmp_path / "signals" / "rejections.md"
    argv = [
        "append",
        "--run-id",
        run_id,
        "--run-url",
        f"https://github.com/o/r/actions/runs/{run_id}",
        "--date",
        "2026-10-06",
        "--out",
        str(out),
    ]
    for name, path in paths.items():
        argv += [f"--{name}", str(path)]
    assert rejections.main(argv) == 0
    return out


def test_append_renders_record(tmp_path: Path) -> None:
    text = run_append(tmp_path).read_text()
    assert text.startswith("# Harness rejections")
    assert "## 2026-10-06 — ingest/false_keep/acme/example.com (run 42)" in text
    assert "- Rung: 2" in text
    assert "- nvidia: window regression" in text
    assert "- Overfits to Acme" in text
    assert "Has evidence" not in text
    assert "Nit" not in text
    assert "| nvidia | fail |" in text


def test_append_is_idempotent_per_run(tmp_path: Path) -> None:
    out = run_append(tmp_path)
    first = out.read_text()
    run_append(tmp_path)
    assert out.read_text() == first


def test_append_keeps_chronological_order(tmp_path: Path) -> None:
    out = run_append(tmp_path, run_id="42")
    run_append(tmp_path, run_id="43")
    text = out.read_text()
    assert text.index("(run 42)") < text.index("(run 43)")
    assert text.count("# Harness rejections") == 1


def test_append_fails_open_on_missing_inputs(tmp_path: Path) -> None:
    names = ("cluster", "verdict", "proposal", "review", "resolutions", "table")
    missing = {name: tmp_path / "absent" / name for name in names}
    text = run_append(tmp_path, **missing).read_text()
    assert "(run 42)" in text
    assert "- Verdict: not available" in text
    assert "- none recorded" in text
