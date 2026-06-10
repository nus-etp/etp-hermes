"""Unit tests for scripts/slice_companies.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPANIES = [
    {"name": "Acme Robotics", "description": "robots"},
    {"name": "Nova Health", "description": "healthtech"},
    {"name": "Patsnap", "description": "IP analytics"},
]


@pytest.fixture()
def sc(tmp_repo: Path, monkeypatch, scripts_module_loader):
    mod = scripts_module_loader("slice_companies")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_repo)
    monkeypatch.setattr(mod, "COMPANIES_FILE", tmp_repo / "data" / "companies.json")
    monkeypatch.setattr(mod, "QUEUE_FILE", tmp_repo / "signals" / "agent-queue.txt")
    monkeypatch.setattr(mod, "UPDATES_DIR", tmp_repo / "signals" / "updates")
    monkeypatch.setattr(mod, "V2_UPDATES_DIR", tmp_repo / "signals" / "v2" / "updates")
    monkeypatch.setattr(mod, "AGENT_DIR", tmp_repo / "signals" / "agent")
    monkeypatch.setattr(
        mod,
        "OUT_FILES",
        {
            "agent": tmp_repo / "data" / "agent-companies.json",
            "agent-v2": tmp_repo / "data" / "agent-companies-v2.json",
            "synthesis": tmp_repo / "data" / "touched-companies.json",
        },
    )
    (tmp_repo / "data" / "companies.json").write_text(json.dumps(COMPANIES))
    return mod


def _run(sc, monkeypatch, layer: str, date: str = "2026-06-10") -> list[dict]:
    monkeypatch.setattr("sys.argv", ["slice_companies.py", "--layer", layer, "--date", date])
    assert sc.main() == 0
    return json.loads(sc.OUT_FILES[layer].read_text())


def test_agent_slice_queue_plus_deepen(sc, monkeypatch, tmp_repo: Path):
    (tmp_repo / "signals" / "agent-queue.txt").write_text("Patsnap\nNova Health\n")
    (tmp_repo / "signals" / "updates" / "2026-06-10.md").write_text(
        "# updates\n\n## Acme Robotics\n- **x** — Feed · today\n  https://e/1\n"
    )
    out = _run(sc, monkeypatch, "agent")
    assert [c["name"] for c in out] == ["Patsnap", "Nova Health", "Acme Robotics"]


def test_agent_v2_slice_uses_v2_updates_for_deepen(sc, monkeypatch, tmp_repo: Path):
    (tmp_repo / "signals" / "agent-queue.txt").write_text("Patsnap\n")
    # v1 updates name Acme; v2 updates name Nova — the v2 slice must follow v2's file.
    (tmp_repo / "signals" / "updates" / "2026-06-10.md").write_text("## Acme Robotics\n- item\n")
    (tmp_repo / "signals" / "v2" / "updates").mkdir(parents=True)
    (tmp_repo / "signals" / "v2" / "updates" / "2026-06-10.md").write_text("## Nova Health\n- item\n")
    out = _run(sc, monkeypatch, "agent-v2")
    assert [c["name"] for c in out] == ["Patsnap", "Nova Health"]


def test_synthesis_slice_updates_h2_and_agent_h3(sc, monkeypatch, tmp_repo: Path):
    (tmp_repo / "signals" / "updates" / "2026-06-10.md").write_text(
        "# updates\n\n## Acme Robotics\n- item\n\n## Run at 14:00 UTC\n\n## Nova Health\n- item\n"
    )
    (tmp_repo / "signals" / "agent" / "2026-06-10.md").write_text(
        "# Agent supplement\n\n## Gap-fill (companies with no signals in last 7 days)\n"
        "### Patsnap\n- item\n\n## Deepen (today's covered companies)\n### Acme Robotics\n- item\n"
    )
    out = _run(sc, monkeypatch, "synthesis")
    # 'Run at' divider skipped, cohort H2s skipped, dupes collapsed
    assert [c["name"] for c in out] == ["Acme Robotics", "Nova Health", "Patsnap"]


def test_unknown_names_skipped(sc, monkeypatch, tmp_repo: Path):
    (tmp_repo / "signals" / "updates" / "2026-06-10.md").write_text("## Ghost Corp\n- item\n")
    out = _run(sc, monkeypatch, "synthesis")
    assert out == []


def test_missing_inputs_yield_empty_slice(sc, monkeypatch):
    out = _run(sc, monkeypatch, "agent")
    assert out == []
