"""Unit tests for scripts/gebiz_awards.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse

import pytest

NOW = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)


@pytest.fixture()
def gebiz(scripts_module_loader):
    return scripts_module_loader("gebiz_awards")


COMPANIES = [
    {"name": "Hivebotics", "aliases": [], "country": "Singapore"},
    {"name": "Softgridinc", "aliases": ["SOFTGRIDINC PTE LTD"], "country": "China"},
    {"name": "Aleph", "aliases": ["Aleph Digital Technologies Pte Ltd"], "country": "Singapore"},
    {"name": "Twin", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs", "aliases": ["Twin"], "country": "Singapore"},
    {"name": "Acme Overseas", "aliases": [], "country": "Indonesia"},
]


def _record(tender_no: str, supplier: str, award_date: str = "21/3/2025", amount: str = "1340000", **extra: Any):
    record = {
        "tender_no": tender_no,
        "tender_description": "Provision of Remote Sensing Services",
        "agency": "National Parks Board",
        "award_date": award_date,
        "tender_detail_status": "Awarded to Suppliers",
        "supplier_name": supplier,
        "awarded_amt": amount,
    }
    record.update(extra)
    return record


def _fetcher(records: list[dict[str, Any]], fail: bool = False):
    calls: list[dict[str, str]] = []

    def fetch(url: str) -> Any:
        query = {k: v[0] for k, v in parse.parse_qs(parse.urlparse(url).query).items()}
        calls.append(query)
        if fail:
            raise error.URLError("boom")
        offset = int(query["offset"])
        limit = int(query["limit"])
        return {"success": True, "result": {"records": records[offset : offset + limit], "total": len(records)}}

    fetch.calls = calls
    return fetch


def _no_sleep(_secs: float) -> None:
    return None


def _run(gebiz, tmp_path: Path, fetcher, window_days: int = 730):
    snapshot = tmp_path / "awards.json"
    items = tmp_path / "items.json"
    code = gebiz.run(
        COMPANIES, snapshot, items, window_days=window_days, pace_seconds=0, fetcher=fetcher, sleep=_no_sleep, now=NOW
    )
    payload = json.loads(items.read_text(encoding="utf-8")) if items.exists() else None
    return code, payload, snapshot


def test_exact_normalised_name_match_builds_item(gebiz, tmp_path) -> None:
    code, payload, _ = _run(gebiz, tmp_path, _fetcher([_record("NPB000ETT24000083", "SOFTGRIDINC PTE LTD")]))
    assert code == 0
    [item] = payload["items"]
    assert item["company"] == "Softgridinc"
    assert item["headline"] == (
        "Softgridinc awarded National Parks Board tender: Provision of Remote Sensing Services (S$1.34M)"
    )
    assert item["dedup_key"] == "gebiz://NPB000ETT24000083/softgridinc"
    assert item["source_kind"] == "gebiz"
    assert item["pubDate"] == "2025-03-21"
    assert "NPB000ETT24000083" in item["link"]


def test_weak_bare_name_and_containment_never_match(gebiz, tmp_path) -> None:
    records = [
        _record("A1", "ALEPH PTE. LTD."),
        _record("A2", "HIVEBOTICS SOLUTIONS PTE LTD"),
        _record("A3", "Aleph Digital Technologies Pte. Ltd."),
    ]
    _, payload, _ = _run(gebiz, tmp_path, _fetcher(records))
    assert [i["dedup_key"] for i in payload["items"]] == ["gebiz://A3/alephdigitaltechnologies"]


def test_ambiguous_key_and_non_sg_company_are_skipped(gebiz, tmp_path) -> None:
    records = [_record("B1", "TWIN PTE LTD"), _record("B2", "ACME OVERSEAS PTE LTD")]
    _, payload, _ = _run(gebiz, tmp_path, _fetcher(records))
    assert payload["items"] == []


def test_snapshot_prevents_re_emitting_and_seeds_first_run(gebiz, tmp_path) -> None:
    fetcher = _fetcher([_record("C1", "Hivebotics Pte Ltd")])
    _, first, snapshot = _run(gebiz, tmp_path, fetcher)
    assert first["seeded"] is True and len(first["items"]) == 1
    assert json.loads(snapshot.read_text())["awards"] == {"gebiz://C1/hivebotics": "2026-11-02"}

    _, second, _ = _run(gebiz, tmp_path, fetcher)
    assert second["seeded"] is False and second["items"] == []

    newer = _fetcher([_record("C1", "Hivebotics Pte Ltd"), _record("C2", "HIVEBOTICS PTE. LTD.")])
    _, third, _ = _run(gebiz, tmp_path, newer)
    assert [i["dedup_key"] for i in third["items"]] == ["gebiz://C2/hivebotics"]


def test_window_limits_emission_but_still_snapshots(gebiz, tmp_path) -> None:
    fetcher = _fetcher([_record("D1", "Hivebotics Pte Ltd", award_date="1/1/2020")])
    _, payload, snapshot = _run(gebiz, tmp_path, fetcher, window_days=365)
    assert payload["items"] == []
    assert "gebiz://D1/hivebotics" in json.loads(snapshot.read_text())["awards"]


def test_bad_dates_and_missing_tender_numbers_are_skipped(gebiz, tmp_path) -> None:
    records = [
        _record("E1", "Hivebotics Pte Ltd", award_date="not a date"),
        _record("", "Hivebotics Pte Ltd"),
    ]
    _, payload, _ = _run(gebiz, tmp_path, _fetcher(records))
    assert payload["items"] == []


def test_amount_formatting(gebiz) -> None:
    assert gebiz.format_amount(1340000) == "S$1.34M"
    assert gebiz.format_amount(342000) == "S$342K"
    assert gebiz.format_amount(1) == "S$1"
    assert gebiz.format_amount(None) == "amount not disclosed"


def test_pages_through_the_dataset(gebiz, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gebiz, "PAGE_SIZE", 2)
    records = [_record(f"F{n}", "Other Co Pte Ltd") for n in range(5)] + [_record("F9", "Hivebotics Pte Ltd")]
    fetcher = _fetcher(records)
    _, payload, _ = _run(gebiz, tmp_path, fetcher)
    assert payload["records_scanned"] == 6
    assert len(fetcher.calls) == 3
    assert len(payload["items"]) == 1


def test_fetch_failure_exits_1_and_leaves_state_untouched(gebiz, tmp_path) -> None:
    code, payload, snapshot = _run(gebiz, tmp_path, _fetcher([], fail=True))
    assert code == 1
    assert payload is None
    assert not snapshot.exists()


def test_rate_limit_is_retried(gebiz, tmp_path) -> None:
    attempts = {"n": 0}
    records = [_record("G1", "Hivebotics Pte Ltd")]

    def fetch(url: str) -> Any:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise gebiz.RateLimited("429")
        return {"success": True, "result": {"records": records, "total": 1}}

    code, payload, _ = _run(gebiz, tmp_path, fetch)
    assert code == 0
    assert len(payload["items"]) == 1
