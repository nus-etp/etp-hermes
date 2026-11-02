"""Unit tests for scripts/ats_jobs.py and the ats_jobs kind across the pipeline."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error

import pytest


@pytest.fixture()
def ats(scripts_module_loader):
    return scripts_module_loader("ats_jobs")


@pytest.fixture()
def cc(scripts_module_loader):
    return scripts_module_loader("collect-candidates")


def _fetcher(table: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        if url not in table:
            raise error.URLError(f"no fixture for {url}")
        return table[url]

    return fetch


def _iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_provider_resolution_accepts_legacy_and_generic_kinds(ats) -> None:
    assert ats.source_provider({"type": "lever_jobs"}) == "lever"
    assert ats.source_provider({"type": "recruitee_jobs"}) == "recruitee"
    assert ats.source_provider({"type": "ats_jobs", "provider": "Ashby"}) == "ashby"
    assert ats.source_provider({"type": "ats_jobs", "provider": "workday"}) is None
    assert ats.source_provider({"type": "ats_jobs"}) is None
    assert ats.source_provider({"type": "rss"}) is None
    assert ats.is_ats_source({"type": "recruitee_jobs"})
    assert not ats.is_ats_source({"type": "html_scrape"})


def test_schema_test_lists_every_provider(ats, repo_root: Path) -> None:
    schema = (repo_root / "tests" / "static" / "test_companies_schema.py").read_text()
    for provider in ats.PROVIDERS:
        assert f'"{provider}"' in schema


def test_legacy_dedup_keys_are_unchanged(ats) -> None:
    assert (
        ats.dedup_key("lever", "https://api.lever.co/v0/postings/patsnap?mode=json", "abc")
        == "lever://patsnap/abc"
    )
    assert (
        ats.dedup_key("recruitee", "https://patsnap.recruitee.com/api/offers/", "2798211")
        == "recruitee://patsnap/2798211"
    )


def test_lever_parser(ats) -> None:
    body = json.dumps(
        [
            {
                "id": "abc",
                "text": "ML Engineer",
                "categories": {"team": "AI"},
                "hostedUrl": "https://jobs.lever.co/patsnap/abc",
                "createdAt": 1_790_000_000_000,
            },
            {"id": "nodate", "text": "Undated", "categories": None},
        ]
    ).encode()
    postings, next_url = ats.parse_page("lever", body, "https://api.lever.co/v0/postings/patsnap")
    assert next_url is None
    assert postings[0]["id"] == "abc"
    assert ats.headline(postings[0]) == "ML Engineer — AI"
    assert postings[0]["published"] == datetime.fromtimestamp(1_790_000_000, tz=timezone.utc)
    assert postings[1]["published"] is None
    assert ats.headline(postings[1]) == "Undated"


def test_recruitee_parser(ats) -> None:
    body = json.dumps(
        {
            "offers": [
                {
                    "id": 2798211,
                    "title": "Life Science specialist",
                    "department": "SG - Life Sciences",
                    "careers_url": "https://careers.patsnap.com/o/life-science-specialist",
                    "published_at": "2026-10-27 06:37:59 UTC",
                }
            ]
        }
    ).encode()
    [posting], _ = ats.parse_page("recruitee", body, "https://patsnap.recruitee.com/api/offers/")
    assert posting["id"] == "2798211"
    assert posting["published"] == datetime(2026, 10, 27, 6, 37, 59, tzinfo=timezone.utc)
    assert posting["link"] == "https://careers.patsnap.com/o/life-science-specialist"


def test_smartrecruiters_parser_and_pagination(ats) -> None:
    url = "https://api.smartrecruiters.com/v1/companies/Acme/postings?limit=1"
    page1 = json.dumps(
        {
            "offset": 0,
            "limit": 1,
            "totalFound": 2,
            "content": [
                {
                    "id": "744000158320111",
                    "name": "Engineering Manager",
                    "department": {"label": "Engineering"},
                    "releasedDate": "2026-10-28T15:14:43.941Z",
                }
            ],
        }
    ).encode()
    page2 = json.dumps(
        {
            "offset": 1,
            "limit": 1,
            "totalFound": 2,
            "content": [{"id": "2", "name": "Designer", "department": {}, "releasedDate": None}],
        }
    ).encode()
    next_url = "https://api.smartrecruiters.com/v1/companies/Acme/postings?limit=1&offset=1"
    postings = ats.fetch_postings("smartrecruiters", url, _fetcher({url: page1, next_url: page2}))
    assert [p["id"] for p in postings] == ["744000158320111", "2"]
    assert postings[0]["link"] == "https://jobs.smartrecruiters.com/Acme/744000158320111"
    assert ats.headline(postings[0]) == "Engineering Manager — Engineering"
    assert postings[0]["published"] == datetime(2026, 10, 28, 15, 14, 43, 941000, tzinfo=timezone.utc)
    assert ats.dedup_key("smartrecruiters", url, "2") == "smartrecruiters://Acme/2"


def test_breezy_parser(ats) -> None:
    body = json.dumps(
        [
            {
                "id": "98323abf2296",
                "name": "Robotics Engineer",
                "url": "https://acme.breezy.hr/p/98323abf2296-robotics-engineer",
                "published_date": "2026-10-15T14:37:22.684Z",
                "department": "R&D",
            }
        ]
    ).encode()
    url = "https://acme.breezy.hr/json"
    [posting], _ = ats.parse_page("breezy", body, url)
    assert ats.headline(posting) == "Robotics Engineer — R&D"
    assert posting["link"] == "https://acme.breezy.hr/p/98323abf2296-robotics-engineer"
    assert ats.dedup_key("breezy", url, posting["id"]) == "breezy://acme/98323abf2296"


def test_ashby_parser_skips_unlisted(ats) -> None:
    body = json.dumps(
        {
            "jobs": [
                {
                    "id": "d3bc",
                    "title": "Fullstack Engineer",
                    "team": "Engineering",
                    "department": "Product",
                    "publishedAt": "2026-10-27T20:13:45.158+00:00",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/d3bc",
                    "isListed": True,
                },
                {"id": "hidden", "title": "Secret", "isListed": False},
            ],
            "apiVersion": "1",
        }
    ).encode()
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    postings, _ = ats.parse_page("ashby", body, url)
    assert [p["id"] for p in postings] == ["d3bc"]
    assert ats.headline(postings[0]) == "Fullstack Engineer — Engineering"
    assert ats.dedup_key("ashby", url, "d3bc") == "ashby://acme/d3bc"


def test_jazzhr_parser_reads_listing_html(ats) -> None:
    body = b"""
    <ul class='list-group'>
      <li class="list-group-item">
        <h3 class='list-group-item-heading'>
          <a href="https://acme.applytojob.com/apply/teiZDWhRON/Account-Executive-Cardiology">
            Account Executive, R&amp;D                                    </a>
        </h3>
      </li>
      <li class="list-group-item">
        <h3 class='list-group-item-heading'>
          <a href="https://acme.applytojob.com/apply/5fj3FdJgAG/Director">Director</a>
        </h3>
      </li>
    </ul>
    <a href="http://acme.applytojob.com/apply/" id='resumator-back-button'>Back</a>
    """
    url = "https://acme.applytojob.com/apply"
    postings, _ = ats.parse_page("jazzhr", body, url)
    assert [(p["id"], p["title"]) for p in postings] == [
        ("teiZDWhRON", "Account Executive, R&D"),
        ("5fj3FdJgAG", "Director"),
    ]
    assert postings[0]["published"] is None
    assert ats.dedup_key("jazzhr", url, "teiZDWhRON") == "jazzhr://acme/teiZDWhRON"
    with pytest.raises(ValueError):
        ats.parse_page("jazzhr", b"<html>account no longer active</html>", url)


def test_manatal_parser_follows_next(ats) -> None:
    url = "https://www.careers-page.com/api/v1.0/c/acme/jobs/"
    next_url = "https://www.careers-page.com/api/v1.0/c/acme/jobs/?page=2"
    page1 = json.dumps(
        {
            "count": 2,
            "next": next_url,
            "results": [
                {
                    "id": 2883259,
                    "hash": "L9Y69V66",
                    "position_name": "Content Intern",
                    "organization_name": "Marketing",
                }
            ],
        }
    ).encode()
    page2 = json.dumps(
        {"count": 2, "next": None, "results": [{"id": 7, "hash": "ZZ", "position_name": "Sales"}]}
    ).encode()
    postings = ats.fetch_postings("manatal", url, _fetcher({url: page1, next_url: page2}))
    assert [p["id"] for p in postings] == ["2883259", "7"]
    assert postings[0]["link"] == "https://www.careers-page.com/acme/job/L9Y69V66"
    assert ats.headline(postings[0]) == "Content Intern — Marketing"
    assert ats.dedup_key("manatal", url, "7") == "manatal://acme/7"


def test_manatal_pagination_is_capped(ats) -> None:
    url = "https://www.careers-page.com/api/v1.0/c/acme/jobs/"

    def endless(page_url: str) -> bytes:
        return json.dumps(
            {"next": page_url + "x", "results": [{"id": page_url, "hash": "h"}]}
        ).encode()

    assert len(ats.fetch_postings("manatal", url, endless)) == ats.MAX_PAGES


def test_mycareersfuture_parser_keeps_only_the_uen(ats) -> None:
    url = "https://api.mycareersfuture.gov.sg/v2/jobs?uen=201834961K&limit=100"
    body = json.dumps(
        {
            "results": [
                {
                    "uuid": "1a20c51b",
                    "title": "Robotics Hardware Engineer",
                    "categories": [{"id": 11, "category": "Engineering"}],
                    "postedCompany": {"uen": "201834961K", "name": "BEEX PTE. LTD."},
                    "hiringCompany": None,
                    "metadata": {
                        "newPostingDate": "2026-10-18",
                        "jobDetailsUrl": "https://www.mycareersfuture.gov.sg/job/engineering/x-1a20c51b",
                    },
                },
                {
                    "uuid": "agency",
                    "title": "Someone else",
                    "postedCompany": {"uen": "999999999Z"},
                    "hiringCompany": {"uen": "888888888Z"},
                    "metadata": {},
                },
            ],
            "total": 2,
            "_links": {"self": {"href": url}},
        }
    ).encode()
    postings, next_url = ats.parse_page("mycareersfuture", body, url)
    assert next_url is None
    [posting] = postings
    assert ats.headline(posting) == "Robotics Hardware Engineer — Engineering"
    assert posting["published"] == datetime(2026, 10, 18, tzinfo=timezone.utc)
    assert posting["link"].startswith("https://www.mycareersfuture.gov.sg/job/")
    assert ats.dedup_key("mycareersfuture", url, "1a20c51b") == "mycareersfuture://201834961K/1a20c51b"


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("lever", b'{"error": "bad"}'),
        ("recruitee", b"[]"),
        ("smartrecruiters", b'{"content": null}'),
        ("breezy", b"<html>Career portal not found</html>"),
        ("ashby", b"{}"),
        ("manatal", b"[]"),
        ("mycareersfuture", b'{"results": "x"}'),
    ],
)
def test_parsers_reject_unexpected_shapes(ats, provider, body) -> None:
    with pytest.raises(ValueError):
        ats.parse_page(provider, body, "https://acme.example/x")
    assert ats.count_postings(provider, body, "https://acme.example/x") is None


def test_unknown_provider_raises(ats) -> None:
    with pytest.raises(ValueError):
        ats.parse_page("workday", b"[]", "https://acme.example/x")


def test_empty_read_state_counts_consecutive_empty_reads(ats) -> None:
    first = ats.empty_read_state(None, 0)
    assert first == {"last_postings": 0, "consecutive_empty": 1}
    second = ats.empty_read_state(first, 0)
    assert second["consecutive_empty"] == 2
    unchanged = ats.empty_read_state(second, second["last_postings"])
    assert unchanged["consecutive_empty"] == 3
    unparsed = ats.empty_read_state(unchanged, None)
    assert unparsed == {"consecutive_empty": 3, "last_postings": 0}
    assert ats.empty_read_state(unparsed, 4) == {"last_postings": 4, "consecutive_empty": 0}
    assert ats.empty_read_state({}, None) == {"consecutive_empty": 0}


def _company(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": "Acme", "aliases": [], "description": "robotics", "sources": [source]}]


def test_collect_ats_jobs_new_provider(cc) -> None:
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    body = json.dumps(
        {
            "jobs": [
                {"id": "new", "title": "Engineer", "team": "R&D", "publishedAt": _iso(1), "jobUrl": "https://jobs.ashbyhq.com/acme/new"},
                {"id": "old", "title": "Old", "publishedAt": _iso(45), "jobUrl": "https://jobs.ashbyhq.com/acme/old"},
                {"id": "seen", "title": "Seen", "publishedAt": _iso(1), "jobUrl": "https://jobs.ashbyhq.com/acme/seen"},
            ]
        }
    ).encode()
    out = cc.collect(
        _company({"type": "ats_jobs", "provider": "ashby", "label": "careers", "url": url}),
        [],
        changed={"firehose": [], "per_company": {"Acme": [url]}},
        jina=None,
        seen={"ashby://acme/seen"},
        fetcher=_fetcher({url: body}),
    )
    [cand] = out["candidates"]
    assert cand["dedup_key"] == "ashby://acme/new"
    assert cand["headline"] == "Engineer — R&D"
    assert cand["source_kind"] == "ats_jobs"
    assert cand["provider"] == "ashby"
    assert cand["source"] == "Acme · careers"


def test_collect_legacy_lever_kind_still_works(cc) -> None:
    url = "https://api.lever.co/v0/postings/acme?mode=json"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    body = json.dumps([{"id": "abc", "text": "Engineer", "createdAt": now_ms}]).encode()
    out = cc.collect(
        _company({"type": "lever_jobs", "label": "careers", "url": url}),
        [],
        changed={"firehose": [], "per_company": {"Acme": [url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({url: body}),
    )
    [cand] = out["candidates"]
    assert cand["dedup_key"] == "lever://acme/abc"
    assert cand["provider"] == "lever"


def test_collect_ats_unknown_provider_and_bad_page_fail_open(cc) -> None:
    url = "https://acme.example/jobs"
    unknown = cc.collect(
        _company({"type": "ats_jobs", "provider": "workday", "url": url}),
        [],
        changed={"firehose": [], "per_company": {"Acme": [url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({url: b"[]"}),
    )
    assert unknown["candidates"] == []
    [failure] = unknown["fetch_failed"]
    assert failure["kind"] == "ats_jobs"
    assert "workday" in failure["error"]

    manatal_url = "https://www.careers-page.com/api/v1.0/c/acme/jobs/"
    first = json.dumps(
        {"next": manatal_url + "?page=2", "results": [{"id": 1, "hash": "h", "position_name": "Role"}]}
    ).encode()
    broken = cc.collect(
        _company({"type": "ats_jobs", "provider": "manatal", "url": manatal_url}),
        [],
        changed={"firehose": [], "per_company": {"Acme": [manatal_url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({manatal_url: first}),
    )
    assert broken["candidates"] == []
    assert broken["fetch_failed"][0]["provider"] == "manatal"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def test_preflight_tracks_consecutive_empty_reads(scripts_module_loader, monkeypatch) -> None:
    preflight = scripts_module_loader("preflight-feeds")
    url = "https://api.lever.co/v0/postings/acme?mode=json"
    responses: list[Any] = [
        _FakeResponse(b"[]"),
        _FakeResponse(b"[]"),
        error.HTTPError(url, 304, "Not Modified", hdrs=None, fp=io.BytesIO(b"")),  # type: ignore[arg-type]
        _FakeResponse(b'[{"id": "x"}]'),
    ]

    def urlopen(req, timeout=None):  # noqa: ARG001
        spec = responses.pop(0)
        if isinstance(spec, Exception):
            raise spec
        return spec

    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    entry: dict[str, Any] | None = None
    streaks = []
    for _ in range(4):
        _, entry = preflight.check_url(url, entry, ats_provider="lever")
        streaks.append(entry["consecutive_empty"])
    assert streaks == [1, 2, 3, 0]
    assert entry["last_postings"] == 1

    responses.append(_FakeResponse(b"[]"))
    _, plain = preflight.check_url(url, None)
    assert "consecutive_empty" not in plain


def test_preflight_preflights_every_ats_kind(scripts_module_loader) -> None:
    preflight = scripts_module_loader("preflight-feeds")
    assert {"ats_jobs", "lever_jobs", "recruitee_jobs"} <= preflight.PREFLIGHTED_TYPES


def test_detect_dead_feeds_reports_empty_ats_boards(scripts_module_loader, tmp_path: Path, monkeypatch) -> None:
    dd = scripts_module_loader("detect-dead-feeds")
    board = "https://api.lever.co/v0/postings/patsnap?mode=json"
    busy = "https://patsnap.recruitee.com/api/offers/"
    cache = {
        board: {"consecutive_failures": 0, "last_status": 200, "consecutive_empty": 4, "last_postings": 0},
        busy: {"consecutive_failures": 0, "last_status": 200, "consecutive_empty": 2, "last_postings": 0},
    }
    companies = [
        {
            "name": "Patsnap",
            "sources": [
                {"type": "lever_jobs", "label": "lever", "url": board},
                {"type": "ats_jobs", "provider": "recruitee", "label": "careers", "url": busy},
            ],
        }
    ]
    (tmp_path / "data").mkdir()
    (tmp_path / "signals").mkdir()
    (tmp_path / "data" / "feed-cache.json").write_text(json.dumps(cache))
    (tmp_path / "data" / "companies.json").write_text(json.dumps(companies))
    monkeypatch.setattr(dd, "CACHE_FILE", tmp_path / "data" / "feed-cache.json")
    monkeypatch.setattr(dd, "FEEDS_FILE", tmp_path / "data" / "feeds.json")
    monkeypatch.setattr(dd, "COMPANIES_FILE", tmp_path / "data" / "companies.json")
    monkeypatch.setattr(dd, "OUT_JSON", tmp_path / "data" / "dead-feeds.json")
    monkeypatch.setattr(dd, "OUT_MD", tmp_path / "signals" / "feed-health.md")
    monkeypatch.setenv("ATS_EMPTY_READ_THRESHOLD", "3")

    assert dd.main() == 0

    out = json.loads((tmp_path / "data" / "dead-feeds.json").read_text())
    assert [(r["name"], r["kind"], r["consecutive_empty"]) for r in out["empty"]] == [
        ("Patsnap · lever", "ats_jobs/lever", 4)
    ]
    assert out["stats"]["empty"] == 1
    report = (tmp_path / "signals" / "feed-health.md").read_text()
    assert "## Empty ATS boards" in report
    assert "1 empty" in report

    monkeypatch.setenv("ATS_EMPTY_READ_THRESHOLD", "2")
    assert dd.main() == 0
    out = json.loads((tmp_path / "data" / "dead-feeds.json").read_text())
    assert out["stats"]["empty"] == 2


def test_render_open_jobs_series_reads_legacy_lever_rows(scripts_module_loader) -> None:
    pytest.importorskip("matplotlib")
    render = scripts_module_loader("render_metrics")
    rows = [
        {"date": "2026-10-31", "lever": {"open": 3}},
        {"date": "2026-11-01", "jobs": {"open": 41, "provider": "recruitee"}},
        {"date": "2026-11-02"},
    ]
    _, ys = render.extract_series(rows, ["jobs", "open"])
    assert ys == [3, 41, None]
