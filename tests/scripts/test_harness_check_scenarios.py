"""Unit tests for scripts/harness/check_scenarios.py."""

from __future__ import annotations

import json
from pathlib import Path

from harness import check_scenarios


def card(card_id: str, url: str, status: str = "active") -> dict:
    return {
        "id": card_id,
        "layer": "ingest",
        "version": 1,
        "status": status,
        "origin": {"kind": "ab_seed", "date": "2026-10-03"},
        "persona": "p",
        "goal": "g",
        "input": {"candidate": {"url": url}},
        "expected": {"verdict": "drop"},
        "cluster": "ingest/false_keep/acme",
        "graders": ["verdict_match"],
        "created_by": "test",
    }


def put(root: Path, relative: str, content: dict | str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def test_clean_corpus_passes(tmp_path: Path, capsys) -> None:
    put(tmp_path, "ingest/ingest-2026-10-03-0001.json", card("ingest-2026-10-03-0001", "https://a"))
    put(tmp_path, "ingest/ingest-2026-10-03-0002.json", card("ingest-2026-10-03-0002", "https://a", "retired"))
    assert check_scenarios.main(["--root", str(tmp_path)]) == 0
    assert "2 card(s) ok" in capsys.readouterr().out


def test_empty_corpus_passes(tmp_path: Path) -> None:
    assert check_scenarios.main(["--root", str(tmp_path / "missing")]) == 0


def test_reports_every_problem(tmp_path: Path, capsys) -> None:
    put(tmp_path, "ingest/ingest-2026-10-03-0001.json", card("ingest-2026-10-03-0001", "https://a"))
    put(tmp_path, "ingest/ingest-2026-10-03-0002.json", card("ingest-2026-10-03-0002", "https://a"))
    put(tmp_path, "ingest/renamed.json", card("ingest-2026-10-03-0001", "https://b"))
    put(tmp_path, "ingest/broken.json", "{oops")
    put(tmp_path, "synthesis/ingest-2026-10-03-0009.json", card("ingest-2026-10-03-0009", "https://c"))
    put(tmp_path, "stray.json", card("ingest-2026-10-03-0003", "https://d"))
    bad = card("ingest-2026-10-03-0004", "https://e")
    bad["graders"] = []
    put(tmp_path, "ingest/ingest-2026-10-03-0004.json", bad)

    assert check_scenarios.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "duplicate active ingest url https://a" in out
    assert "duplicate id ingest-2026-10-03-0001" in out
    assert "renamed.json: filename does not match id" in out
    assert "broken.json: unreadable JSON" in out
    assert "filed under synthesis/ but layer is 'ingest'" in out
    assert "stray.json: not under a known layer directory" in out
    assert "graders must be a non-empty list" in out
