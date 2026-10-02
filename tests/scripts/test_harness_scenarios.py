"""Unit tests for scripts/harness/scenarios.py — the scenario-card contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import scenarios


def make_card(card_id: str = "ingest-2026-10-03-0001", url: str = "https://example.com/a", **overrides) -> dict:
    card = {
        "id": card_id,
        "layer": "ingest",
        "version": 1,
        "status": "active",
        "origin": {"kind": "ab_seed", "date": "2026-10-03"},
        "persona": "Layer 1",
        "goal": "Drop it",
        "input": {"candidate": {"title": "t", "url": url}, "companies": {}},
        "expected": {"verdict": "drop", "company": None},
        "cluster": "ingest/false_keep/acme/example.com",
        "graders": ["verdict_match"],
        "created_by": "test",
    }
    card.update(overrides)
    return card


def test_validate_accepts_well_formed_card() -> None:
    assert scenarios.validate(make_card()) == []


def test_validate_reports_missing_keys() -> None:
    card = make_card()
    del card["graders"]
    assert scenarios.validate(card) == ["missing key: graders"]


def test_validate_rejects_bad_fields() -> None:
    card = make_card(status="nope", graders=["mystery"], cluster="ingest/only")
    problems = scenarios.validate(card)
    assert "bad status: nope" in problems
    assert "unknown grader: mystery" in problems
    assert any(p.startswith("cluster must be") for p in problems)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = scenarios.save(make_card(), tmp_path)
    assert path == tmp_path / "ingest" / "ingest-2026-10-03-0001.json"
    assert scenarios.load_all(tmp_path) == [make_card()]


def test_save_rejects_invalid_card(tmp_path: Path) -> None:
    with pytest.raises(scenarios.ScenarioError):
        scenarios.save(make_card(layer="bogus"), tmp_path)


def test_load_all_filters_status(tmp_path: Path) -> None:
    scenarios.save(make_card(), tmp_path)
    scenarios.save(make_card("ingest-2026-10-03-0002", status="retired"), tmp_path)
    assert [c["id"] for c in scenarios.load_all(tmp_path)] == ["ingest-2026-10-03-0001"]
    assert len(scenarios.load_all(tmp_path, status=None)) == 2


def test_next_id_increments(tmp_path: Path) -> None:
    assert scenarios.next_id("ingest", "2026-10-03", tmp_path) == "ingest-2026-10-03-0001"
    scenarios.save(make_card(), tmp_path)
    assert scenarios.next_id("ingest", "2026-10-03", tmp_path) == "ingest-2026-10-03-0002"


def test_weight_and_cluster_key() -> None:
    assert scenarios.weight(make_card(origin={"kind": "human", "date": "2026-10-03"})) == scenarios.HUMAN_WEIGHT
    assert scenarios.weight(make_card()) == 1
    assert scenarios.cluster_key("ingest", "false_keep", "Bright Sight", "www.Foo.com") == (
        "ingest/false_keep/bright-sight/www.foo.com"
    )


def test_card_url_prefers_candidate_then_input_url() -> None:
    assert scenarios.card_url(make_card()) == "https://example.com/a"
    assert scenarios.card_url(make_card(input={"url": "https://x.io"})) == "https://x.io"
    assert scenarios.card_url(make_card(input={})) is None


def test_find_by_url_searches_every_status(tmp_path: Path) -> None:
    scenarios.save(make_card(status="retired"), tmp_path)
    assert scenarios.find_by_url("https://example.com/a", tmp_path)["id"] == "ingest-2026-10-03-0001"
    assert scenarios.find_by_url("https://example.com/b", tmp_path) is None
    assert scenarios.find_by_url("https://example.com/a", tmp_path, layer="synthesis") is None


def test_find_by_issue(tmp_path: Path) -> None:
    scenarios.save(make_card(origin={"kind": "human", "date": "2026-10-03", "issue": 12}), tmp_path)
    scenarios.save(make_card("ingest-2026-10-03-0002", url="https://example.com/b"), tmp_path)
    assert scenarios.find_by_issue(12, tmp_path)["id"] == "ingest-2026-10-03-0001"
    assert scenarios.find_by_issue(13, tmp_path) is None
    assert scenarios.find_by_issue(None, tmp_path) is None
