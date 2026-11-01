"""Unit tests for scripts/startupsg_snapshot.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse
from urllib.request import Request

import pytest

NOW = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)


@pytest.fixture()
def ssg(scripts_module_loader):
    return scripts_module_loader("startupsg_snapshot")


def _profile(
    profile_id: str,
    name: str,
    uen: str,
    stage: str = "Pre-seed",
    funding: bool = False,
    employees: str = "1 - 10",
) -> dict[str, Any]:
    return {
        "id": profile_id,
        "displayName": name,
        "registeredName": name,
        "uen": uen,
        "investmentStage": {"id": "58", "name": stage, "weight": 0} if stage else None,
        "hasFundingRounds": funding,
        "rangeEmployee": {"id": "46", "name": employees} if employees else None,
        "companyDescriptor": f"{name} descriptor",
    }


COMPANIES = [
    {"name": "Acme Robotics", "aliases": ["Acme Robotics Pte Ltd"], "country": "Singapore"},
    {"name": "Nova Health", "aliases": [], "country": "Singapore", "identifiers": {"uen": "202000002B"}},
    {"name": "Assemblr", "aliases": [], "country": "Indonesia"},
    {"name": "Twin", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs", "aliases": ["Twin"], "country": "Singapore"},
    {"name": "Pinned Elsewhere", "aliases": [], "country": "Singapore", "identifiers": {"uen": "209999999Z"}},
]

PROFILES = [
    _profile("1", "ACME ROBOTICS PTE. LTD.", "202000001A"),
    _profile("2", "Nova Wellness Pte Ltd", "202000002B", stage="Seed", funding=True),
    _profile("3", "ASSEMBLR PTE. LTD.", "202000003C"),
    _profile("4", "TWIN PTE. LTD.", "202000004D"),
    _profile("5", "PINNED ELSEWHERE PTE. LTD.", "202000005E"),
]


def _pager(profiles: list[dict[str, Any]], fail_offsets: set[int] | None = None):
    calls: list[int] = []

    def fetch(url: str) -> Any:
        offset = int(parse.parse_qs(parse.urlparse(url).query)["from"][0])
        calls.append(offset)
        if fail_offsets and offset in fail_offsets:
            raise error.URLError("boom")
        size = int(parse.parse_qs(parse.urlparse(url).query)["size"][0])
        return {"total": len(profiles), "data": profiles[offset : offset + size]}

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _no_sleep(_secs: float) -> None:
    return None


def test_normalise_name_strips_legal_suffix_and_punctuation(ssg) -> None:
    assert ssg.normalise_name("ACME ROBOTICS PTE. LTD.") == "acmerobotics"
    assert ssg.normalise_name("Acme Robotics Pte Ltd") == "acmerobotics"
    assert ssg.normalise_name("ConcreteAI Private Limited") == "concreteai"
    assert ssg.normalise_name("M.i. Cloud Tech") == "micloudtech"
    assert ssg.normalise_name("THEMOONBEAM.CO PTE. LTD.") == "themoonbeamco"


def test_match_prefers_uen_then_unique_sg_name(ssg) -> None:
    matched = ssg.match_profiles(COMPANIES, PROFILES)
    assert set(matched) == {"Acme Robotics", "Nova Health"}
    assert matched["Acme Robotics"]["matched_on"] == "name"
    assert matched["Acme Robotics"]["uen"] == "202000001A"
    assert matched["Acme Robotics"]["profile_url"] == "https://www.startupsg.gov.sg/profiles/1"
    assert matched["Nova Health"]["matched_on"] == "uen"
    assert matched["Nova Health"]["stage"] == "Seed"
    assert matched["Nova Health"]["has_funding_rounds"] is True


def test_match_skips_profile_claimed_by_two_companies(ssg) -> None:
    matched = ssg.match_profiles(COMPANIES, PROFILES)
    assert "Twin" not in matched
    assert "Twin Labs" not in matched


def test_match_does_not_fall_back_to_name_when_pinned_uen_missing(ssg) -> None:
    assert "Pinned Elsewhere" not in ssg.match_profiles(COMPANIES, PROFILES)


def _record(ssg, **overrides: Any) -> dict[str, Any]:
    record = ssg.profile_record(_profile("1", "ACME ROBOTICS PTE. LTD.", "202000001A"), "name")
    record.update(overrides)
    return record


def test_diff_seed_run_emits_nothing(ssg) -> None:
    assert ssg.diff_items(None, {"Acme Robotics": _record(ssg)}, "now") == []


def test_diff_emits_one_item_per_meaningful_change(ssg) -> None:
    before = {"Acme Robotics": _record(ssg)}
    after = {
        "Acme Robotics": _record(ssg, stage="Seed", has_funding_rounds=True, employees="11 - 50"),
        "Nova Health": _record(ssg, uen="202000002B", profile_id="2", profile_url="u2"),
    }
    items = ssg.diff_items(before, after, "2026-11-01T13:00:00+00:00")
    keys = [it["dedup_key"] for it in items]
    assert keys == [
        "startupsg://202000001A/stage/seed",
        "startupsg://202000001A/funding/true",
        "startupsg://202000001A/employees/11-50",
        "startupsg://202000002B/profile/listed",
    ]
    stage = items[0]
    assert stage["company"] == "Acme Robotics"
    assert stage["headline"] == (
        "Acme Robotics now lists investment stage Seed on Startup SG (was Pre-seed)"
    )
    assert stage["link"] == "https://www.startupsg.gov.sg/profiles/1"
    assert stage["pubDate"] == "2026-11-01T13:00:00+00:00"
    assert stage["source_kind"] == "startupsg"
    assert "pre_extracted" not in stage


def test_diff_ignores_unchanged_and_funding_true_to_false(ssg) -> None:
    before = {"Acme Robotics": _record(ssg, has_funding_rounds=True)}
    after = {"Acme Robotics": _record(ssg, has_funding_rounds=False)}
    assert ssg.diff_items(before, after, "now") == []
    assert ssg.diff_items(before, dict(before), "now") == []


def test_diff_ignores_blanked_stage(ssg) -> None:
    before = {"Acme Robotics": _record(ssg)}
    after = {"Acme Robotics": _record(ssg, stage="", employees="")}
    assert ssg.diff_items(before, after, "now") == []


def test_run_seeds_snapshot_without_items(ssg, tmp_path: Path) -> None:
    snapshot = tmp_path / "startupsg" / "watchlist-profiles.json"
    items = tmp_path / "startupsg-items.json"
    rc = ssg.run(COMPANIES, snapshot, items, fetcher=_pager(PROFILES), sleep=_no_sleep, now=NOW)
    assert rc == 0
    saved = json.loads(snapshot.read_text())
    assert set(saved["companies"]) == {"Acme Robotics", "Nova Health"}
    assert "descriptor" not in saved["companies"]["Acme Robotics"]
    out = json.loads(items.read_text())
    assert out["items"] == []
    assert out["profiles_fetched"] == len(PROFILES)
    assert out["snapshot_updated"] is True


def test_run_diffs_against_previous_snapshot(ssg, tmp_path: Path) -> None:
    snapshot = tmp_path / "watchlist-profiles.json"
    items = tmp_path / "startupsg-items.json"
    ssg.run(COMPANIES, snapshot, items, fetcher=_pager(PROFILES), sleep=_no_sleep, now=NOW)
    changed = [dict(p) for p in PROFILES]
    changed[0] = _profile("1", "ACME ROBOTICS PTE. LTD.", "202000001A", stage="Seed")
    rc = ssg.run(COMPANIES, snapshot, items, fetcher=_pager(changed), sleep=_no_sleep, now=NOW)
    assert rc == 0
    out = json.loads(items.read_text())
    assert [it["dedup_key"] for it in out["items"]] == ["startupsg://202000001A/stage/seed"]
    assert json.loads(snapshot.read_text())["companies"]["Acme Robotics"]["stage"] == "Seed"


def test_run_pages_politely_until_total(ssg, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssg, "PAGE_SIZE", 2)
    sleeps: list[float] = []
    fetch = _pager(PROFILES)
    ssg.run(COMPANIES, tmp_path / "s.json", tmp_path / "i.json", fetcher=fetch, sleep=sleeps.append, now=NOW)
    assert fetch.calls == [0, 2, 4]
    assert sleeps == [ssg.PAGE_INTERVAL_SECS, ssg.PAGE_INTERVAL_SECS]


def test_run_partial_failure_fails_open_and_keeps_snapshot(ssg, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssg, "PAGE_SIZE", 2)
    snapshot = tmp_path / "watchlist-profiles.json"
    items = tmp_path / "startupsg-items.json"
    snapshot.write_text(json.dumps({"companies": {"Old Co": {"stage": "Seed"}}}))
    before = snapshot.read_text()
    rc = ssg.run(
        COMPANIES, snapshot, items, fetcher=_pager(PROFILES, {2}), sleep=_no_sleep, now=NOW
    )
    assert rc == 0
    assert snapshot.read_text() == before
    out = json.loads(items.read_text())
    assert out["items"] == []
    assert out["pages_failed"] == 1
    assert out["snapshot_updated"] is False
    assert out["error_rate_alarm"] is False


def test_run_all_pages_fail_alarms(ssg, tmp_path: Path) -> None:
    snapshot = tmp_path / "watchlist-profiles.json"
    items = tmp_path / "startupsg-items.json"

    def fetch(_url: str) -> Any:
        raise error.HTTPError(_url, 503, "down", {}, None)  # type: ignore[arg-type]

    rc = ssg.run(COMPANIES, snapshot, items, fetcher=fetch, sleep=_no_sleep, now=NOW)
    assert rc == 1
    assert not snapshot.exists()
    out = json.loads(items.read_text())
    assert out["items"] == []
    assert out["error_rate_alarm"] is True


def test_run_malformed_payload_counts_as_failure(ssg, tmp_path: Path) -> None:
    rc = ssg.run(
        COMPANIES,
        tmp_path / "s.json",
        tmp_path / "i.json",
        fetcher=lambda _url: {"error": "nope"},
        sleep=_no_sleep,
        now=NOW,
    )
    assert rc == 1


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def test_fetch_json_walks_ua_ladder_on_gate(ssg, monkeypatch) -> None:
    seen_uas: list[str] = []

    def urlopen(req: Request, timeout: float | None = None) -> Any:  # noqa: ARG001
        ua = dict(req.header_items()).get("User-agent") or ""
        seen_uas.append(ua)
        if ua == ssg.UA_LADDER[0]:
            raise error.HTTPError(req.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]
        return _Resp(b'{"total": 0, "data": []}')

    monkeypatch.setattr(ssg.request, "urlopen", urlopen)
    assert ssg.fetch_json("https://example.test/x") == {"total": 0, "data": []}
    assert seen_uas == [ssg.UA_LADDER[0], ssg.UA_LADDER[1]]


def test_fetch_json_reraises_non_gate_error(ssg, monkeypatch) -> None:
    def urlopen(req: Request, timeout: float | None = None) -> Any:  # noqa: ARG001
        raise error.HTTPError(req.full_url, 500, "err", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(ssg.request, "urlopen", urlopen)
    with pytest.raises(error.HTTPError):
        ssg.fetch_json("https://example.test/x")
