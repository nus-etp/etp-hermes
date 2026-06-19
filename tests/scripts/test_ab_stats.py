"""Unit tests for scripts/ab_stats.py — the A/B significance test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("ab_stats")


def _row(kept_by: str, label):
    return {"kept_by": kept_by, "label": label, "url": f"u/{kept_by}/{label}", "date": "2026-06-10"}


def test_right_arm_mapping(mod) -> None:
    # kept by v1 + judge says keep -> v1 was right
    assert mod.right_arm(_row("v1", "keep")) == "v1"
    # kept by v1 + judge says drop -> v1 let noise through, v2 was right
    assert mod.right_arm(_row("v1", "drop")) == "v2"
    assert mod.right_arm(_row("v2", "keep")) == "v2"
    assert mod.right_arm(_row("v2", "drop")) == "v1"
    # unlabeled / malformed -> None
    assert mod.right_arm(_row("v1", None)) is None
    assert mod.right_arm(_row("v3", "keep")) is None


def test_binom_two_sided(mod) -> None:
    assert mod.binom_two_sided(0, 0) is None
    # even split is maximally non-significant
    assert mod.binom_two_sided(5, 5) == pytest.approx(1.0)
    # 10 vs 0 is strongly significant
    assert mod.binom_two_sided(10, 0) < 0.01
    # symmetry
    assert mod.binom_two_sided(8, 2) == mod.binom_two_sided(2, 8)


def test_compute_collecting_below_target(mod) -> None:
    rows = [_row("v2", "keep") for _ in range(5)]
    res = mod.compute(rows, target=40)
    assert res["status"] == "collecting"
    assert res["winner"] is None
    assert res["n_discordant"] == 5 and res["v2_right"] == 5


def test_compute_significant_at_target(mod) -> None:
    # 30 say v2 right, 10 say v1 right, n=40 >= target -> significant for v2
    rows = [_row("v2", "keep") for _ in range(30)] + [_row("v2", "drop") for _ in range(10)]
    res = mod.compute(rows, target=40)
    assert res["status"] == "significant"
    assert res["winner"] == "v2"
    assert res["p_value"] < 0.05


def test_compute_not_significant_at_target(mod) -> None:
    rows = [_row("v2", "keep") for _ in range(20)] + [_row("v1", "keep") for _ in range(20)]
    res = mod.compute(rows, target=40)
    assert res["status"] == "not_significant"
    assert res["winner"] is None


def test_compute_counts_unlabeled(mod) -> None:
    rows = [_row("v1", "keep"), _row("v2", None), _row("v1", None)]
    res = mod.compute(rows, target=40)
    assert res["unlabeled"] == 2 and res["n_discordant"] == 1


def test_upsert_section_replaces_not_duplicates(mod, tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# A/B report\n\n## History\n\n| ... |\n")
    mod.upsert_section(report, mod.render_section(mod.compute([_row("v2", "keep")], target=40)))
    first = report.read_text()
    assert first.count(mod.SECTION_HEADER) == 1
    assert "## History" in first  # prior content kept
    mod.upsert_section(report, mod.render_section(mod.compute([_row("v2", "keep")] * 3, target=40)))
    assert report.read_text().count(mod.SECTION_HEADER) == 1  # replaced, not appended


def test_main_writes_artifacts(mod, tmp_path: Path, monkeypatch) -> None:
    ab = tmp_path / "signals" / "ab"
    ab.mkdir(parents=True)
    (ab / "report.md").write_text("# A/B report\n")
    rows = [_row("v2", "keep") for _ in range(3)]
    (ab / "disagreements.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    monkeypatch.setattr(mod.sys, "argv", ["ab_stats", "--repo-root", str(tmp_path), "--target", "40"])
    assert mod.main() == 0
    sig = json.loads((ab / "significance.json").read_text())
    assert sig["status"] == "collecting" and sig["v2_right"] == 3
    assert mod.SECTION_HEADER in (ab / "report.md").read_text()
