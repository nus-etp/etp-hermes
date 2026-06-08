"""Unit tests for scripts/langfuse_usage.py (mocked HTTP, no network)."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _make_urlopen(by_env: dict[str | None, list[dict[str, Any]]]):
    """Build a urlopen that returns one Daily-Metrics page per environment.

    ``by_env`` maps an environment value (or None for the unfiltered grand
    total) to the list of day-rows that environment should return.
    """
    calls: list[str] = []

    def urlopen(req, timeout: float | None = None):  # noqa: ARG001
        url = req.full_url
        calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        env = q.get("environment", [None])[0]
        rows = by_env.get(env, [])
        return _FakeResponse({"data": rows, "meta": {"page": 1, "totalPages": 1}})

    return urlopen, calls


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("langfuse_usage")


# --------------------------------------------------------------------------- #
# month_range
# --------------------------------------------------------------------------- #
def test_month_range_basic(mod) -> None:
    frm, to = mod.month_range("2026-06")
    assert frm == "2026-06-01T00:00:00Z"
    assert to == "2026-07-01T00:00:00Z"


def test_month_range_december_rolls_to_next_year(mod) -> None:
    frm, to = mod.month_range("2026-12")
    assert frm == "2026-12-01T00:00:00Z"
    assert to == "2027-01-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
def _day(date: str, traces: int, obs: int, cost: float, usage: list[dict]) -> dict:
    return {
        "date": date,
        "countTraces": traces,
        "countObservations": obs,
        "totalCost": cost,
        "usage": usage,
    }


def _usage(model: str | None, inp: int, out: int, cost: float) -> dict:
    return {
        "model": model,
        "inputUsage": inp,
        "outputUsage": out,
        "totalUsage": inp + out,
        "countObservations": 1,
        "countTraces": 1,
        "totalCost": cost,
    }


def test_aggregate_sums_totals_and_models(mod) -> None:
    rows = [
        _day("2026-06-01", 3, 5, 0.10, [_usage("deepseek", 100, 50, 0.06), _usage("mimo", 20, 10, 0.04)]),
        _day("2026-06-02", 2, 2, 0.02, [_usage("deepseek", 200, 100, 0.02)]),
    ]
    agg = mod.aggregate(rows)
    t = agg["totals"]
    assert t["days"] == 2
    assert t["countTraces"] == 5
    assert t["countObservations"] == 7
    assert t["inputUsage"] == 320
    assert t["outputUsage"] == 160
    assert t["totalUsage"] == 480
    assert t["totalCost"] == pytest.approx(0.12)

    by_model = {m["model"]: m for m in agg["by_model"]}
    assert by_model["deepseek"]["totalUsage"] == 450
    assert by_model["deepseek"]["totalCost"] == pytest.approx(0.08)
    assert by_model["mimo"]["totalUsage"] == 30
    # by_model is sorted by cost descending.
    assert agg["by_model"][0]["model"] == "deepseek"


def test_aggregate_handles_empty_and_null_model(mod) -> None:
    assert mod.aggregate([])["totals"] == mod._empty_totals()
    agg = mod.aggregate([_day("2026-06-01", 1, 1, 0.0, [_usage(None, 5, 5, 0.0)])])
    assert agg["by_model"][0]["model"] == "(unknown)"


# --------------------------------------------------------------------------- #
# fetch_daily pagination
# --------------------------------------------------------------------------- #
def test_fetch_daily_paginates(mod, monkeypatch) -> None:
    pages = {
        1: {"data": [_day("2026-06-01", 1, 1, 0.0, [])], "meta": {"page": 1, "totalPages": 2}},
        2: {"data": [_day("2026-06-02", 1, 1, 0.0, [])], "meta": {"page": 2, "totalPages": 2}},
    }

    def urlopen(req, timeout: float | None = None):  # noqa: ARG001
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)["page"][0])
        return _FakeResponse(pages[page])

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    rows = mod.fetch_daily("https://lf.example", "Basic x", "f", "t")
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02"]


# --------------------------------------------------------------------------- #
# collect_month (grand total + per-environment)
# --------------------------------------------------------------------------- #
def test_collect_month_splits_by_environment(mod, monkeypatch) -> None:
    by_env = {
        None: [_day("2026-06-01", 10, 12, 0.30, [_usage("deepseek", 1000, 500, 0.30)])],
        "production": [_day("2026-06-01", 8, 9, 0.25, [_usage("deepseek", 800, 400, 0.25)])],
        "eval": [_day("2026-06-01", 2, 3, 0.05, [_usage("deepseek", 200, 100, 0.05)])],
    }
    urlopen, calls = _make_urlopen(by_env)
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)

    snap = mod.collect_month("2026-06", "https://lf.example", "Basic x", ["production", "eval"])
    assert snap["month"] == "2026-06"
    assert snap["from"] == "2026-06-01T00:00:00Z"
    assert snap["totals"]["countTraces"] == 10
    assert snap["by_environment"]["production"]["countTraces"] == 8
    assert snap["by_environment"]["eval"]["countTraces"] == 2
    # One grand-total call + one per environment.
    assert len(calls) == 3


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #
def test_humanize_and_cost(mod) -> None:
    assert mod.humanize_count(1_500_000) == "1.5M"
    assert mod.humanize_count(2_300) == "2.3K"
    assert mod.humanize_count(42) == "42"
    assert mod.fmt_cost(0) == "$0"
    assert mod.fmt_cost(0.0345) == "$0.0345"
    assert mod.fmt_cost(12.5) == "$12.50"


def test_build_markdown_history_and_detail(mod) -> None:
    snaps = [
        {
            "month": "2026-06",
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-07-01T00:00:00Z",
            "totals": {"days": 1, "countTraces": 10, "countObservations": 12, "totalUsage": 1500, "totalCost": 0.30},
            "by_environment": {"production": {"countTraces": 8, "countObservations": 9, "totalUsage": 1200, "totalCost": 0.25}},
            "by_model": [{"model": "deepseek", "inputUsage": 1000, "outputUsage": 500, "totalUsage": 1500, "totalCost": 0.30}],
        },
        {
            "month": "2026-05",
            "totals": {"days": 1, "countTraces": 4, "countObservations": 4, "totalUsage": 500, "totalCost": 0.10},
        },
    ]
    md = mod.build_markdown(snaps, "2026-06-08 06:00 UTC")
    assert "# Langfuse usage" in md
    assert "## Monthly history" in md
    assert "| 2026-06 |" in md and "| 2026-05 |" in md
    assert "## 2026-06 detail" in md  # detail describes the newest snapshot
    assert "deepseek" in md
    assert "production" in md


def test_build_markdown_empty(mod) -> None:
    md = mod.build_markdown([], "2026-06-08 06:00 UTC")
    assert "## Monthly history" in md
    assert "detail" not in md


def test_load_snapshots_sorted_newest_first(mod, tmp_path: Path) -> None:
    d = tmp_path / "langfuse-usage"
    d.mkdir()
    (d / "2026-05.json").write_text(json.dumps({"month": "2026-05", "totals": {}}))
    (d / "2026-06.json").write_text(json.dumps({"month": "2026-06", "totals": {}}))
    (d / "bad.json").write_text("{ not json")
    snaps = mod.load_snapshots(d)
    assert [s["month"] for s in snaps] == ["2026-06", "2026-05"]


# --------------------------------------------------------------------------- #
# fail-open
# --------------------------------------------------------------------------- #
def test_main_fail_open_without_credentials(mod, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("HERMES_LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(mod.sys, "argv", ["langfuse_usage", "--month", "2026-06"])
    # Any network attempt would fail loudly; fail-open must return before that.
    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network when creds absent")),
    )
    assert mod.main() == 0
