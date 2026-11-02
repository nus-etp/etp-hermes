"""Unit tests for scripts/ipos_delta.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse

import pytest

NOW = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
TM_ID = "d_6145acb2130bf781165258e76a584383"
PATENT_ID = "d_cb395225d1dcf91cc795f83e572c5e0e"


@pytest.fixture()
def ipos(scripts_module_loader):
    return scripts_module_loader("ipos_delta")


COMPANIES = [
    {"name": "Horizon Quantum", "aliases": [], "country": "Singapore", "identifiers": {"uen": "201802755E"}},
    {"name": "pQCee", "aliases": ["pQCee Pte Ltd"], "country": "Singapore", "identifiers": {"uen": "202228798G"}},
    {"name": "SoundEye", "aliases": [], "country": "Singapore"},
    {"name": "WateRoam", "aliases": ["Wateroam Pte Ltd"], "country": "Indonesia"},
    {"name": "InnoArk", "aliases": ["PT Innoark"], "country": "Indonesia"},
    {"name": "Twin", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs", "aliases": ["Twin"], "country": "Singapore"},
]


def _applicant(name: str, uen: str, country: str = "SG") -> dict[str, Any]:
    return {
        "uenCompanyCode": uen,
        "name": name,
        "countryOfIncorporationOrResidence": {"code": country, "description": country},
    }


def _trademark(number: str, applicant: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    record = {
        "applicationNumber": number,
        "filingDate": "2026-10-30",
        "applicationType": "Trade Mark",
        "markStatus": "Pending (Under Examination)",
        "markStatusDate": "2026-10-30",
        "publicationDate": None,
        "lastModifiedDate": "2026-10-30",
        "markData_json": json.dumps([{"wordsInMark": "SAFEQUARD", "descrOfDevice": ""}]),
        "goodsAndServicesSpecifications_json": json.dumps(
            [
                {"classNum": "Class 09", "goodsServices": [{"itemDesc": "Cryptographic software"}]},
                {"classNum": "Class 42", "goodsServices": [{"itemDesc": "Software as a service"}]},
            ]
        ),
        "currentApplicantProprietorDetails_json": json.dumps([applicant]),
    }
    record.update(overrides)
    return record


def _patent(number: str, applicant: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    record = {
        "applicationNumber": number,
        "applicationStatus": "Pending (Published)",
        "applicationType": "Normal",
        "filingDate": "2025-02-14",
        "lodgementDate": "2025-02-14",
        "titleOfInvention": "AI-POWERED  SMART CEILING LIGHT",
        "dateOfPublication": "2026-10-31",
        "lastModifiedDate": "2026-10-31",
        "applicants_json": json.dumps([applicant]),
    }
    record.update(overrides)
    return record


def _store(by_dataset_day: dict[tuple[str, str], list[dict[str, Any]]], fail: set[tuple[str, str]] | None = None):
    calls: list[dict[str, str]] = []

    def fetch(url: str) -> Any:
        query = {k: v[0] for k, v in parse.parse_qs(parse.urlparse(url).query).items()}
        calls.append(query)
        dataset = query["resource_id"]
        day = json.loads(query["filters"])["lastModifiedDate"]
        if fail and (dataset, day) in fail:
            raise error.URLError("boom")
        records = by_dataset_day.get((dataset, day), [])
        offset = int(query["offset"])
        limit = int(query["limit"])
        return {"success": True, "result": {"records": records[offset : offset + limit], "total": len(records)}}

    fetch.calls = calls
    return fetch


def _no_sleep(_secs: float) -> None:
    return None


def _run(ipos, tmp_path: Path, fetcher, days: int = 2) -> tuple[int, dict[str, Any]]:
    out = tmp_path / "ipos-items.json"
    code = ipos.run(COMPANIES, out, days=days, pace_seconds=0, fetcher=fetcher, sleep=_no_sleep, now=NOW)
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_window_days_uses_singapore_calendar(ipos) -> None:
    sgt_today = NOW.astimezone(ipos.SGT).date()
    assert ipos.window_days(sgt_today, 3) == ["2026-11-02", "2026-11-01", "2026-10-31"]


def test_watchlist_matches_uen_before_name(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    assert watchlist.match({"name": "Anything", "uen": "201802755E", "country": "US"}) == ("Horizon Quantum", "uen")
    assert watchlist.match({"name": "SOUNDEYE PTE. LTD.", "uen": "201525722H", "country": "SG"}) == ("SoundEye", "name")


def test_watchlist_name_match_requires_singapore_applicant(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    assert watchlist.match({"name": "SoundEye Inc.", "uen": "E26000001A", "country": "US"}) is None


def test_watchlist_pinned_company_accepts_ipos_internal_code_only(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    assert watchlist.match({"name": "PQCEE PTE LTD", "uen": "E24017741V", "country": "SG"}) == ("pQCee", "name")
    assert watchlist.match({"name": "PQCEE PTE LTD", "uen": "", "country": "SG"}) == ("pQCee", "name")
    assert watchlist.match({"name": "PQCEE PTE LTD", "uen": "201999999Z", "country": "SG"}) is None


def test_watchlist_non_sg_company_matches_only_through_pte_alias(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    assert watchlist.match({"name": "WATEROAM PTE. LTD.", "uen": "201425118K", "country": "SG"}) == ("WateRoam", "name")
    assert watchlist.match({"name": "INNOARK PTE. LTD.", "uen": "200512484K", "country": "SG"}) is None


def test_watchlist_skips_ambiguous_names(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    assert watchlist.match({"name": "TWIN PTE. LTD.", "uen": "202000004D", "country": "SG"}) is None


def test_trademark_filing_item_shape(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    record = _trademark("40202626216T", _applicant("pQCee Pte Ltd", "E25014399Y"))
    [item] = ipos.build_items("trademark", [record], watchlist, "2026-10-29")
    assert item["company"] == "pQCee"
    assert item["headline"] == "pQCee files trademark 'SAFEQUARD' (class 9, 42)"
    assert item["dedup_key"] == "ipos://trademark/40202626216T/pending-under-examination"
    assert item["pubDate"] == "2026-10-30"
    assert item["source_kind"] == "ipos"
    assert item["matched_on"] == "name"
    assert "Cryptographic software" in item["description"]
    query = parse.parse_qs(parse.urlparse(item["link"]).query)
    assert query["resource_id"] == [TM_ID]
    assert json.loads(query["filters"][0]) == {"applicationNumber": "40202626216T"}


def test_trademark_status_change_item(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    record = _trademark(
        "40202500001A",
        _applicant("SOUNDEYE PTE. LTD.", "201525722H"),
        filingDate="2025-01-02",
        markStatus="Registered",
        markStatusDate="2026-10-30",
    )
    [item] = ipos.build_items("trademark", [record], watchlist, "2026-10-29")
    assert item["headline"] == "SoundEye trademark 'SAFEQUARD' (class 9, 42): Registered"
    assert item["dedup_key"] == "ipos://trademark/40202500001A/registered"


def test_patent_items_by_status(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    published = _patent("10202500419V", _applicant("SOUNDEYE PTE. LTD.", "201525722H"))
    filed = _patent(
        "10202600909Y",
        _applicant("Horizon", "201802755E"),
        lodgementDate="2026-10-30",
        filingDate="2026-10-30",
        applicationStatus="Pending",
        dateOfPublication=None,
        titleOfInvention="Quantum compiler",
    )
    published_item, filed_item = ipos.build_items("patent", [published, filed], watchlist, "2026-10-29")
    assert published_item["headline"] == "SoundEye patent (Pending (Published)): AI-POWERED SMART CEILING LIGHT"
    assert published_item["pubDate"] == "2026-10-31"
    assert published_item["dedup_key"] == "ipos://patent/10202500419V/pending-published"
    assert filed_item["headline"] == "Horizon Quantum patent application: Quantum compiler"
    assert filed_item["matched_on"] == "uen"
    assert filed_item["pubDate"] == "2026-10-31"


def test_unmatched_and_numberless_records_are_ignored(ipos) -> None:
    watchlist = ipos.Watchlist(COMPANIES)
    records = [
        _trademark("40202600000X", _applicant("Unrelated Pte Ltd", "209999999Z")),
        _trademark("", _applicant("SOUNDEYE PTE. LTD.", "201525722H")),
    ]
    assert ipos.build_items("trademark", records, watchlist, "2026-10-29") == []


def test_run_reads_both_registers_for_each_day_and_dedupes(ipos, tmp_path) -> None:
    trademark = _trademark("40202626216T", _applicant("pQCee Pte Ltd", "E25014399Y"))
    patent = _patent("10202500419V", _applicant("SOUNDEYE PTE. LTD.", "201525722H"))
    fetcher = _store(
        {
            (TM_ID, "2026-11-02"): [trademark],
            (TM_ID, "2026-11-01"): [trademark],
            (PATENT_ID, "2026-11-01"): [patent],
        }
    )
    code, payload = _run(ipos, tmp_path, fetcher)
    assert code == 0
    assert len(fetcher.calls) == 4
    assert {(c["resource_id"], json.loads(c["filters"])["lastModifiedDate"]) for c in fetcher.calls} == {
        (TM_ID, "2026-11-02"),
        (TM_ID, "2026-11-01"),
        (PATENT_ID, "2026-11-02"),
        (PATENT_ID, "2026-11-01"),
    }
    assert payload["dates"] == ["2026-11-02", "2026-11-01"]
    assert [i["dedup_key"] for i in payload["items"]] == [
        "ipos://patent/10202500419V/pending-published",
        "ipos://trademark/40202626216T/pending-under-examination",
    ]
    assert payload["fetches_ok"] == 4
    assert payload["companies_matched"] == 2
    assert payload["error_rate_alarm"] is False


def test_run_pages_through_large_days(ipos, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ipos, "PAGE_SIZE", 2)
    records = [_trademark(f"4020260000{n}A", _applicant("Other Pte Ltd", "209999999Z")) for n in range(5)]
    records.append(_trademark("40202600009Z", _applicant("SOUNDEYE PTE. LTD.", "201525722H")))
    fetcher = _store({(TM_ID, "2026-11-02"): records})
    code, payload = _run(ipos, tmp_path, fetcher, days=1)
    assert code == 0
    assert [c["offset"] for c in fetcher.calls if c["resource_id"] == TM_ID] == ["0", "2", "4"]
    assert payload["records_scanned"] == 6
    assert [i["company"] for i in payload["items"]] == ["SoundEye"]


def test_run_halves_page_size_on_payload_too_large(ipos, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ipos, "PAGE_SIZE", 4)
    monkeypatch.setattr(ipos, "MIN_PAGE_SIZE", 1)
    records = [_trademark(f"4020260000{n}A", _applicant("Other Pte Ltd", "209999999Z")) for n in range(3)]
    records.append(_trademark("40202600009Z", _applicant("SOUNDEYE PTE. LTD.", "201525722H")))
    inner = _store({(TM_ID, "2026-11-02"): records})
    limits: list[int] = []

    def fetcher(url: str) -> Any:
        query = {k: v[0] for k, v in parse.parse_qs(parse.urlparse(url).query).items()}
        limits.append(int(query["limit"]))
        if query["resource_id"] == TM_ID and int(query["limit"]) > 2:
            raise error.HTTPError(url, 413, "Payload Too Large", None, None)
        return inner(url)

    code, payload = _run(ipos, tmp_path, fetcher, days=1)
    assert code == 0
    assert limits[:3] == [4, 2, 2]
    assert payload["fetches_failed"] == 0
    assert payload["records_scanned"] == 4
    assert [i["company"] for i in payload["items"]] == ["SoundEye"]


def test_run_fails_open_on_partial_failure(ipos, tmp_path) -> None:
    patent = _patent("10202500419V", _applicant("SOUNDEYE PTE. LTD.", "201525722H"))
    fetcher = _store({(PATENT_ID, "2026-11-02"): [patent]}, fail={(TM_ID, "2026-11-02")})
    code, payload = _run(ipos, tmp_path, fetcher, days=1)
    assert code == 0
    assert payload["fetches_failed"] == 1
    assert len(payload["items"]) == 1


def test_run_exits_1_when_every_fetch_fails(ipos, tmp_path) -> None:
    def fetcher(_url: str) -> Any:
        raise error.URLError("down")

    code, payload = _run(ipos, tmp_path, fetcher)
    assert code == 1
    assert payload["items"] == []
    assert payload["fetches_ok"] == 0
    assert payload["error_rate_alarm"] is True


def test_run_retries_rate_limited_calls(ipos, tmp_path) -> None:
    attempts: list[str] = []
    inner = _store({})

    def fetcher(url: str) -> Any:
        attempts.append(url)
        if len(attempts) == 1:
            raise ipos.RateLimited("HTTP 429")
        return inner(url)

    code, payload = _run(ipos, tmp_path, fetcher, days=1)
    assert code == 0
    assert len(attempts) == 3
    assert payload["fetches_failed"] == 0


def test_unsuccessful_response_counts_as_failure(ipos, tmp_path) -> None:
    def fetcher(_url: str) -> Any:
        return {"success": False, "error": {"message": "bad"}}

    code, payload = _run(ipos, tmp_path, fetcher, days=1)
    assert code == 1
    assert payload["fetches_failed"] == 2
