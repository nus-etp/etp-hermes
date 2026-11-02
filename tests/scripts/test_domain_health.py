"""Unit tests for scripts/domain_health.py."""

from __future__ import annotations

import io
import json
import socket
from datetime import datetime, timezone
from typing import Any
from urllib import error

import pytest

NOW = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)


@pytest.fixture()
def dh(scripts_module_loader):
    return scripts_module_loader("domain_health")


COMPANIES = [
    {
        "name": "Carousell",
        "aliases": [],
        "description": "Marketplace.",
        "sources": [{"type": "rss", "url": "https://press.carousell.com/feed/"}],
        "identifiers": {"linkedin": "https://www.linkedin.com/company/carousell", "website": "https://carousell.com"},
    },
    {"name": "Hubtech.asia", "aliases": [], "description": "Vietnamese tech hub."},
    {
        "name": "Pennycillin",
        "aliases": [],
        "description": "Startup (pennycillin.co; affordable care). Drop pennycillin.org namesake news.",
    },
    {
        "name": "Mento.vn",
        "aliases": ["Mento"],
        "description": "Mentorship platform. Drop the lookalike Mento.co (US coaching).",
    },
    {"name": "Lonely", "aliases": [], "description": "No web presence."},
    {
        "name": "Patsnap",
        "aliases": [],
        "description": "IP analytics.",
        "sources": [{"type": "recruitee_jobs", "url": "https://patsnap.recruitee.com/api/offers"}],
        "identifiers": {"website": "https://www.patsnap.com"},
    },
    {"name": "Lalia", "aliases": [], "description": "x", "identifiers": {"website": "https://www.lalia.com.sg/"}},
]


def test_registrable_domain_handles_second_level_cctlds(dh) -> None:
    assert dh.registrable_domain("press.carousell.com") == "carousell.com"
    assert dh.registrable_domain("www.lalia.com.sg") == "lalia.com.sg"
    assert dh.registrable_domain("rostek.com.vn") == "rostek.com.vn"
    assert dh.registrable_domain("localhost") == ""


def test_collect_domains_from_sources_names_and_description(dh) -> None:
    by_domain, without = dh.collect_domains(COMPANIES)
    assert by_domain == {
        "carousell.com": ["Carousell"],
        "hubtech.asia": ["Hubtech.asia"],
        "pennycillin.co": ["Pennycillin"],
        "mento.vn": ["Mento.vn"],
        "patsnap.com": ["Patsnap"],
        "lalia.com.sg": ["Lalia"],
    }
    assert without == ["Lonely"]


def test_description_distractor_domains_are_ignored(dh) -> None:
    assert dh.company_domains(COMPANIES[3]) == ["mento.vn"]
    assert "pennycillin.org" not in dh.company_domains(COMPANIES[2])


def _record(dh, *, dns=True, status=200, final_url=None, expiration=None, domain="acme.com"):
    record: dict[str, Any] = {
        "domain": domain,
        "companies": ["Acme"],
        "dns": {"resolved": dns, "host": domain if dns else None, "error": None if dns else "NXDOMAIN"},
        "http": {"url": f"https://{domain}/", "status": status, "final_url": final_url or f"https://{domain}/"},
        "rdap": {"expiration": expiration, "error": None},
    }
    return dh.classify(record, NOW, 30)[0]


def test_classify_each_class(dh) -> None:
    assert _record(dh, dns=False, status=None) == "dead"
    assert _record(dh, expiration="2026-10-01T00:00:00Z") == "expired"
    assert _record(dh, status=None) == "unreachable"
    assert _record(dh, status=503) == "unreachable"
    assert _record(dh, expiration="2026-11-20T00:00:00Z") == "expiring"
    assert _record(dh, final_url="https://hugedomains.com/domain_profile.cfm?d=acme.com") == "redirected-offsite"
    assert _record(dh, final_url="https://www.acme.com/en") == "ok"
    assert _record(dh, final_url="https://acme.sg/") == "ok"
    assert _record(dh, status=403, expiration="2027-06-01T00:00:00Z") == "ok"


def test_dead_outranks_expired(dh) -> None:
    assert _record(dh, dns=False, status=None, expiration="2026-01-01T00:00:00Z") == "dead"


def test_rdap_expiry_parses_expiration_event(dh) -> None:
    payload = {
        "events": [
            {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2027-01-01T00:00:00Z"},
        ]
    }
    assert dh.rdap_expiry(payload) == "2027-01-01T00:00:00Z"
    assert dh.rdap_expiry({"events": []}) is None
    assert dh.rdap_expiry(None) is None


def test_rdap_check_is_unknown_for_unsupported_tld(dh) -> None:
    result = dh.rdap_check("hubtech.asia", lambda _d: None)
    assert result["expiration"] is None
    assert "no RDAP record" in result["error"]


def test_rdap_check_fails_open_on_network_error(dh) -> None:
    def boom(_domain: str) -> Any:
        raise error.URLError("timed out")

    assert dh.rdap_check("acme.com", boom) == {"expiration": None, "error": "timed out"}


def test_fetch_rdap_returns_none_on_404_and_retries_429(dh, monkeypatch) -> None:
    calls: list[str] = []
    responses = [429, 200]

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        code = responses.pop(0)
        if code != 200:
            raise error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
        return io.BytesIO(json.dumps({"events": [{"eventAction": "expiration", "eventDate": "2027-01-01"}]}).encode())

    monkeypatch.setattr(dh.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(dh, "RDAP_PACER", dh.Pacer(0, sleep=lambda _s: None))
    payload = dh.fetch_rdap("acme.com", sleep=lambda _s: None)
    assert dh.rdap_expiry(payload) == "2027-01-01"
    assert calls == ["https://rdap.org/domain/acme.com"] * 2

    def not_found(req, timeout):
        raise error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO(b""))

    monkeypatch.setattr(dh.request, "urlopen", not_found)
    assert dh.fetch_rdap("acme.zz", sleep=lambda _s: None) is None


def test_resolve_falls_back_to_www(dh, monkeypatch) -> None:
    def fake_getaddrinfo(host, port, proto=0):
        if host == "acme.com":
            raise socket.gaierror(8, "nodename nor servname provided")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", port))]

    monkeypatch.setattr(dh.socket, "getaddrinfo", fake_getaddrinfo)
    assert dh.resolve("acme.com") == {"resolved": True, "host": "www.acme.com", "addresses": ["1.2.3.4"]}


def test_resolve_reports_nxdomain(dh, monkeypatch) -> None:
    def fake_getaddrinfo(host, port, proto=0):
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(dh.socket, "getaddrinfo", fake_getaddrinfo)
    result = dh.resolve("hubtech.asia")
    assert result["resolved"] is False
    assert "nodename" in result["error"]


class _Resp(io.BytesIO):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(b"")
        self.status = status
        self._url = url

    def geturl(self) -> str:
        return self._url


def test_http_check_walks_ua_ladder_and_records_redirect(dh, monkeypatch) -> None:
    agents: list[str] = []

    def fake_urlopen(req, timeout):
        agents.append(req.get_header("User-agent"))
        if len(agents) == 1:
            raise error.HTTPError(req.full_url, 403, "gated", {}, io.BytesIO(b""))
        return _Resp(200, "https://newbrand.com/home")

    monkeypatch.setattr(dh.request, "urlopen", fake_urlopen)
    result = dh.http_check("acme.com")
    assert result == {"url": "https://acme.com/", "status": 200, "final_url": "https://newbrand.com/home"}
    assert agents[0].startswith("feed-agent-domain-health")
    assert len(agents) == 2


def test_http_check_falls_back_to_http(dh, monkeypatch) -> None:
    def fake_urlopen(req, timeout):
        if req.full_url.startswith("https://"):
            raise error.URLError("CERTIFICATE_VERIFY_FAILED")
        return _Resp(200, req.full_url)

    monkeypatch.setattr(dh.request, "urlopen", fake_urlopen)
    result = dh.http_check("acme.com")
    assert result["status"] == 200
    assert result["url"] == "http://acme.com/"
    assert result["https_error"] == "CERTIFICATE_VERIFY_FAILED"


def test_check_domain_skips_http_without_dns_and_fails_open(dh) -> None:
    def http_should_not_run(_host: str) -> dict[str, Any]:
        raise AssertionError("http called without DNS")

    def rdap_crash(_domain: str) -> Any:
        raise RuntimeError("bad json")

    record = dh.check_domain(
        "hubtech.asia",
        ["Hubtech.asia"],
        now=NOW,
        expiry_days=30,
        resolver=lambda _d: {"resolved": False, "host": None, "error": "NXDOMAIN"},
        http=http_should_not_run,
        rdap_fetcher=rdap_crash,
    )
    assert record["status"] == "dead"
    assert record["http"]["error"] == "skipped: no DNS"
    assert record["rdap"]["error"] == "bad json"


def _fake_checker(statuses: dict[str, str]):
    def checker(domain: str, companies: list[str], *, now: datetime, expiry_days: int) -> dict[str, Any]:
        return {
            "domain": domain,
            "companies": companies,
            "status": statuses.get(domain, "ok"),
            "notes": [],
            "dns": {"resolved": statuses.get(domain) != "dead", "error": "NXDOMAIN"},
            "http": {"url": f"https://{domain}/", "status": 200, "final_url": f"https://{domain}/"},
            "rdap": {"expiration": "2026-01-01T00:00:00Z", "days_left": -304},
        }

    return checker


def test_run_sorts_worst_first_and_counts(dh) -> None:
    report = dh.run(
        COMPANIES,
        now=NOW,
        workers=2,
        checker=_fake_checker({"hubtech.asia": "dead", "pennycillin.co": "expired", "patsnap.com": "redirected-offsite"}),
    )
    assert [r["domain"] for r in report["domains"]][:3] == ["hubtech.asia", "pennycillin.co", "patsnap.com"]
    assert report["counts"]["dead"] == 1
    assert report["counts"]["ok"] == 3
    assert report["companies_without_domain"] == 1
    markdown = dh.render_markdown(report)
    assert markdown.index("## dead (1)") < markdown.index("## expired (1)") < markdown.index("## ok (3)")
    assert "| hubtech.asia | Hubtech.asia | 200 |  | 2026-01-01 (-304d) | NXDOMAIN |" in markdown


def test_run_honours_limit(dh) -> None:
    report = dh.run(COMPANIES, now=NOW, limit=2, checker=_fake_checker({}))
    assert report["domains_checked"] == 2
    assert report["domains_total"] == 6


def test_main_refuses_to_write_when_nothing_resolves(dh, tmp_path, monkeypatch) -> None:
    companies = tmp_path / "companies.json"
    companies.write_text(json.dumps(COMPANIES[:2]), encoding="utf-8")
    monkeypatch.setattr(dh, "check_domain", _fake_checker({"carousell.com": "dead", "hubtech.asia": "dead"}))
    out_md = tmp_path / "health.md"
    code = dh.main(["--companies", str(companies), "--out-json", str(tmp_path / "h.json"), "--out-md", str(out_md)])
    assert code == 1
    assert not out_md.exists()
