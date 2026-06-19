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


def _metric(date: str, v1: int, v2: int) -> dict:
    return {"date": date, "v1_items": v1, "v2_items": v2}


def test_guardrail_ok_within_band(mod) -> None:
    metrics = [_metric(f"2026-06-{d:02d}", 10, 9) for d in range(1, 6)]
    g = mod.compute_guardrail(metrics, lo=0.5, hi=2.0, window=14)
    assert g["status"] == "ok"
    assert g["ratio"] == 0.9 and g["window_days"] == 5


def test_guardrail_warns_on_signal_collapse(mod) -> None:
    # v2 keeping a fraction of v1 -> below the band
    metrics = [_metric(f"2026-06-{d:02d}", 10, 2) for d in range(1, 6)]
    g = mod.compute_guardrail(metrics, lo=0.5, hi=2.0, window=14)
    assert g["status"] == "warn_low" and g["ratio"] == 0.2


def test_guardrail_warns_on_noise_flood(mod) -> None:
    metrics = [_metric(f"2026-06-{d:02d}", 5, 20) for d in range(1, 6)]
    g = mod.compute_guardrail(metrics, lo=0.5, hi=2.0, window=14)
    assert g["status"] == "warn_high" and g["ratio"] == 4.0


def test_guardrail_window_limits_to_recent(mod) -> None:
    # old collapse, recent recovery — window=2 should only see the recent days
    metrics = [_metric("2026-06-01", 10, 1), _metric("2026-06-02", 10, 10), _metric("2026-06-03", 10, 10)]
    g = mod.compute_guardrail(metrics, lo=0.5, hi=2.0, window=2)
    assert g["status"] == "ok" and g["window_days"] == 2 and g["ratio"] == 1.0


def test_guardrail_insufficient_when_no_v1(mod) -> None:
    g = mod.compute_guardrail([_metric("2026-06-01", 0, 0)], lo=0.5, hi=2.0, window=14)
    assert g["status"] == "insufficient" and g["ratio"] is None


def test_guardrail_empty_metrics(mod) -> None:
    g = mod.compute_guardrail([], lo=0.5, hi=2.0, window=14)
    assert g["status"] == "insufficient" and g["window_days"] == 0


def test_render_section_includes_guardrail_warning(mod) -> None:
    res = mod.compute([_row("v2", "keep")], target=40)
    res["guardrail"] = mod.compute_guardrail([_metric("2026-06-01", 10, 2)], 0.5, 2.0, 14)
    section = mod.render_section(res)
    assert "Volume guardrail: warn_low" in section


def test_main_writes_artifacts(mod, tmp_path: Path, monkeypatch) -> None:
    ab = tmp_path / "signals" / "ab"
    ab.mkdir(parents=True)
    (ab / "report.md").write_text("# A/B report\n")
    rows = [_row("v2", "keep") for _ in range(3)]
    (ab / "disagreements.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (ab / "metrics.jsonl").write_text(
        "".join(json.dumps(_metric(f"2026-06-{d:02d}", 10, 9)) + "\n" for d in range(1, 4))
    )

    monkeypatch.setattr(mod.sys, "argv", ["ab_stats", "--repo-root", str(tmp_path), "--target", "40"])
    assert mod.main() == 0
    sig = json.loads((ab / "significance.json").read_text())
    assert sig["status"] == "collecting" and sig["v2_right"] == 3
    assert sig["guardrail"]["status"] == "ok"
    report = (ab / "report.md").read_text()
    assert mod.SECTION_HEADER in report
    assert "Volume guardrail" in report
