"""Unit tests for scripts/ab_compare.py — the v1/v2 A/B comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("ab_compare")


@pytest.fixture()
def ab_repo(tmp_repo: Path) -> Path:
    (tmp_repo / "signals" / "v2" / "updates").mkdir(parents=True)
    (tmp_repo / "signals" / "v2" / "agent").mkdir(parents=True)
    return tmp_repo


L1_V1 = """\
# Daily digest — 2026-06-10

## Acme Robotics
- **Acme raises $5M** — TechCrunch · 2026-06-10
  https://techcrunch.com/acme-raises?utm_source=rss

## Beta Bio
- **Beta Bio partners with NUS** — e27 · 2026-06-09
  https://e27.co/beta-nus
"""

L1_V2 = """\
# Daily digest — 2026-06-10

## Acme Robotics
- **Acme raises $5M** — TechCrunch · 2026-06-10
  https://techcrunch.com/acme-raises

## Gamma AI
- **Gamma AI ships v2 of its platform** — VulcanPost · 2026-06-10
  https://vulcanpost.com/gamma-v2
"""

L2_V2 = """\
# Agent supplement — 2026-06-10

## Gap-fill (companies with no signals in last 7 days)
### Delta Energy
- **Delta Energy wins EMA grant** — delta.com · 2026-06-08
  https://delta.com/news/ema-grant

## Run at 13:40 UTC

## Deepen (today's covered companies)
### Acme Robotics
- **Acme raises $5M** — TechCrunch · 2026-06-10
  https://techcrunch.com/acme-raises
"""


def test_normalize_url_strips_tracking_params(mod) -> None:
    assert (
        mod.normalize_url("https://X.com/a?utm_source=rss&id=3&ref=tw")
        == "https://x.com/a?id=3"
    )
    # Non-URL dedup keys pass through untouched.
    assert mod.normalize_url("lever://acme/123") == "lever://acme/123"


def test_parse_digest_layer1_and_layer2(mod, ab_repo: Path) -> None:
    p1 = ab_repo / "signals" / "updates" / "2026-06-10.md"
    p1.write_text(L1_V1)
    items = mod.parse_digest(p1)
    assert [(i["company"], i["url"]) for i in items] == [
        ("Acme Robotics", "https://techcrunch.com/acme-raises"),
        ("Beta Bio", "https://e27.co/beta-nus"),
    ]

    p2 = ab_repo / "signals" / "v2" / "agent" / "2026-06-10.md"
    p2.write_text(L2_V2)
    items = mod.parse_digest(p2)
    # H3 = company in Layer 2 files; cohort H2s and Run-at dividers are ignored.
    assert {i["company"] for i in items} == {"Delta Energy", "Acme Robotics"}


def test_collect_arm_dedups_across_layers(mod, ab_repo: Path) -> None:
    (ab_repo / "signals" / "v2" / "updates" / "2026-06-10.md").write_text(L1_V2)
    (ab_repo / "signals" / "v2" / "agent" / "2026-06-10.md").write_text(L2_V2)
    items = mod.collect_arm(ab_repo, "2026-06-10", v2=True)
    urls = [i["url"] for i in items]
    # The Acme URL appears in both layers but is counted once.
    assert urls.count("https://techcrunch.com/acme-raises") == 1
    assert len(items) == 3


def test_compare_metrics(mod, ab_repo: Path) -> None:
    (ab_repo / "signals" / "updates" / "2026-06-10.md").write_text(L1_V1)
    (ab_repo / "signals" / "v2" / "updates" / "2026-06-10.md").write_text(L1_V2)
    v1 = mod.collect_arm(ab_repo, "2026-06-10", v2=False)
    v2 = mod.collect_arm(ab_repo, "2026-06-10", v2=True)
    row = mod.compare(v1, v2)
    # utm-stripped Acme URL overlaps despite the tracking param in v1's file.
    assert row["overlap"] == 1
    assert row["v1_only"] == 1 and row["v2_only"] == 1
    assert row["jaccard"] == pytest.approx(1 / 3, abs=1e-3)


def test_missing_arm_scores_zero(mod, ab_repo: Path) -> None:
    (ab_repo / "signals" / "updates" / "2026-06-10.md").write_text(L1_V1)
    v2 = mod.collect_arm(ab_repo, "2026-06-10", v2=True)
    assert v2 == []
    row = mod.compare(mod.collect_arm(ab_repo, "2026-06-10", v2=False), v2)
    assert row["v2_items"] == 0 and row["overlap"] == 0


def test_main_idempotent_per_date_and_writes_report(mod, ab_repo: Path, monkeypatch) -> None:
    (ab_repo / "signals" / "updates" / "2026-06-10.md").write_text(L1_V1)
    (ab_repo / "signals" / "v2" / "updates" / "2026-06-10.md").write_text(L1_V2)

    argv = ["ab_compare.py", "--repo-root", str(ab_repo), "--date", "2026-06-10"]
    monkeypatch.setattr("sys.argv", argv)
    assert mod.main() == 0
    assert mod.main() == 0  # second run replaces the row, not appends

    rows = [
        json.loads(l)
        for l in (ab_repo / "signals" / "ab" / "metrics.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-06-10"

    report = (ab_repo / "signals" / "ab" / "report.md").read_text()
    assert "Kept only by v1" in report and "Beta Bio" in report
    assert "Kept only by v2" in report and "Gamma AI" in report
