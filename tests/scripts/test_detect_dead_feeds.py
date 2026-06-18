"""Unit tests for scripts/detect-dead-feeds.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def dd(scripts_module_loader):
    return scripts_module_loader("detect-dead-feeds")


NOW = datetime(2026, 6, 18, tzinfo=timezone.utc)

META = {
    "https://techinasia.example/feed": {"name": "Tech in Asia", "kind": "firehose"},
    "https://gh.example/org.atom": {"name": "Acme · github", "kind": "github_org"},
    "https://quiet.example/feed": {"name": "Quiet Co · press", "kind": "rss"},
}


def test_classify_dead_stale_ok(dd):
    cache = {
        # 5 consecutive failures → dead, firehose → jina-recoverable
        "https://techinasia.example/feed": {
            "consecutive_failures": 5,
            "last_status": -1,
            "last_error": "network unreachable",
        },
        # dead but github_org → not jina-recoverable
        "https://gh.example/org.atom": {"consecutive_failures": 3, "last_status": 404},
        # reachable but no new items in ~168 days → stale
        "https://quiet.example/feed": {
            "consecutive_failures": 0,
            "last_status": 200,
            "last_changed": "2026-01-01T00:00:00+00:00",
        },
        # healthy → neither bucket
        "https://ok.example/feed": {
            "consecutive_failures": 0,
            "last_status": 200,
            "last_changed": "2026-06-17T00:00:00+00:00",
        },
    }
    result = dd.classify(cache, META, fail_threshold=3, stale_days=45, now=NOW)

    dead_names = [(r["name"], r["jina_recoverable"]) for r in result["dead"]]
    # sorted by consecutive_failures desc
    assert dead_names == [("Tech in Asia", True), ("Acme · github", False)]

    assert [r["name"] for r in result["stale"]] == ["Quiet Co · press"]
    assert result["stale"][0]["days_stale"] == 168


def test_classify_threshold_is_inclusive(dd):
    cache = {"https://quiet.example/feed": {"consecutive_failures": 2, "last_status": -1}}
    assert dd.classify(cache, META, 3, 45, NOW)["dead"] == []
    cache["https://quiet.example/feed"]["consecutive_failures"] = 3
    assert len(dd.classify(cache, META, 3, 45, NOW)["dead"]) == 1


def test_main_writes_report_and_json(dd, tmp_path: Path, monkeypatch):
    cache = {
        "https://techinasia.example/feed": {
            "consecutive_failures": 4,
            "last_status": -1,
            "last_error": "timed out",
        }
    }
    feeds = [{"name": "Tech in Asia", "type": "firehose", "url": "https://techinasia.example/feed"}]
    (tmp_path / "data").mkdir()
    (tmp_path / "signals").mkdir()
    (tmp_path / "data" / "feed-cache.json").write_text(json.dumps(cache))
    (tmp_path / "data" / "feeds.json").write_text(json.dumps(feeds))
    (tmp_path / "data" / "companies.json").write_text(json.dumps([]))

    monkeypatch.setattr(dd, "CACHE_FILE", tmp_path / "data" / "feed-cache.json")
    monkeypatch.setattr(dd, "FEEDS_FILE", tmp_path / "data" / "feeds.json")
    monkeypatch.setattr(dd, "COMPANIES_FILE", tmp_path / "data" / "companies.json")
    monkeypatch.setattr(dd, "OUT_JSON", tmp_path / "data" / "dead-feeds.json")
    monkeypatch.setattr(dd, "OUT_MD", tmp_path / "signals" / "feed-health.md")

    assert dd.main() == 0

    out = json.loads((tmp_path / "data" / "dead-feeds.json").read_text())
    assert out["stats"] == {"tracked": 1, "dead": 1, "stale": 0}
    assert out["dead"][0]["name"] == "Tech in Asia"

    report = (tmp_path / "signals" / "feed-health.md").read_text()
    assert "# Feed health" in report
    assert "Tech in Asia" in report
    assert "1 dead" in report


def test_main_missing_cache_is_empty_report(dd, tmp_path: Path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "signals").mkdir()
    monkeypatch.setattr(dd, "CACHE_FILE", tmp_path / "data" / "feed-cache.json")  # absent
    monkeypatch.setattr(dd, "FEEDS_FILE", tmp_path / "data" / "feeds.json")  # absent
    monkeypatch.setattr(dd, "COMPANIES_FILE", tmp_path / "data" / "companies.json")  # absent
    monkeypatch.setattr(dd, "OUT_JSON", tmp_path / "data" / "dead-feeds.json")
    monkeypatch.setattr(dd, "OUT_MD", tmp_path / "signals" / "feed-health.md")

    assert dd.main() == 0
    out = json.loads((tmp_path / "data" / "dead-feeds.json").read_text())
    assert out["stats"] == {"tracked": 0, "dead": 0, "stale": 0}
    assert "_None._" in (tmp_path / "signals" / "feed-health.md").read_text()
