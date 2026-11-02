"""Unit tests for scripts/edgar_daily.py."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error

import pytest

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)

HEADER = (
    "Form Type   Company Name                                                  CIK\n"
    "      Date Filed  File Name\n"
    + "-" * 120
    + "\n"
)


@pytest.fixture()
def edgar(scripts_module_loader):
    return scripts_module_loader("edgar_daily")


COMPANIES = [
    {"name": "Horizon Quantum", "aliases": ["Horizon Quantum Computing"], "country": "Singapore"},
    {"name": "Pinned Co", "aliases": [], "country": "Singapore", "identifiers": {"cik": "0001234567"}},
    {"name": "Alpha", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs Group", "aliases": ["Twin Labs"], "country": "Singapore"},
    {"name": "Acme Robotics", "aliases": [], "country": "Singapore"},
]


def _line(form: str, name: str, cik: str, filed: str, accession: str) -> str:
    return f"{form:<16} {name:<62}{cik:>8}     {filed}    edgar/data/{cik}/{accession}.txt\n"


def _index(*lines: str) -> str:
    return HEADER + "".join(lines)


def _fetcher(edgar, by_stamp: dict[str, Any]):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        stamp = url.rsplit("form.", 1)[1].removesuffix(".idx")
        value = by_stamp.get(stamp)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise edgar.IndexMissing(url)
        return value

    fetch.calls = calls
    return fetch


def _no_sleep(_secs: float) -> None:
    return None


def _run(edgar, tmp_path: Path, fetcher, days: int = 3) -> tuple[int, dict[str, Any]]:
    out = tmp_path / "edgar-items.json"
    code = edgar.run(COMPANIES, out, fetcher, days=days, pace_seconds=0, sleep=_no_sleep, now=NOW)
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_parse_index_keeps_target_forms_and_amendments(edgar) -> None:
    text = _index(
        _line("D", "Horizon Quantum Inc.", "1900001", "20261030", "0001900001-26-000001"),
        _line("D/A", "Horizon Quantum Inc.", "1900001", "20261030", "0001900001-26-000002"),
        _line("S-1/A", "Foo Corp", "1900002", "20261030", "0001900002-26-000001"),
        _line("10-K", "Bar Corp", "1900003", "20261030", "0001900003-26-000001"),
        _line("1-A POS", "Baz Corp", "1900004", "20261030", "0001900004-26-000001"),
        _line("F-4", "Qux Ltd", "1900005", "20261030", "0001900005-26-000001"),
    )
    filings = edgar.parse_index(text, set(edgar.FORM_LABELS))
    assert [f["form"] for f in filings] == ["D", "D/A", "S-1/A", "F-4"]
    assert filings[0] == {
        "form": "D",
        "name": "Horizon Quantum Inc.",
        "cik": "1900001",
        "filed": "2026-10-30",
        "accession": "0001900001-26-000001",
    }


def test_normalise_filer_name_strips_us_suffixes(edgar) -> None:
    assert edgar.normalise_filer_name("Horizon Quantum Holdings, Inc.") == "horizonquantum"
    assert edgar.normalise_filer_name("ACME Robotics L.L.C.") == "acmerobotics"
    assert edgar.normalise_filer_name("Acme Robotics Pte. Ltd.") == "acmerobotics"


def test_watchlist_matches_cik_then_exact_name(edgar) -> None:
    watchlist = edgar.Watchlist(COMPANIES)
    assert watchlist.match({"name": "Whatever", "cik": "1234567", "form": "D"}) == ("Pinned Co", "cik")
    assert watchlist.match({"name": "Horizon Quantum Inc", "cik": "9", "form": "D"}) == ("Horizon Quantum", "name")
    assert watchlist.match({"name": "Horizon Quantum Computing Fund LP", "cik": "9", "form": "D"}) is None


def test_watchlist_pinned_company_never_name_matches(edgar) -> None:
    watchlist = edgar.Watchlist(COMPANIES)
    assert watchlist.match({"name": "Pinned Co Inc", "cik": "55", "form": "D"}) is None


def test_watchlist_skips_weak_and_ambiguous_names(edgar) -> None:
    watchlist = edgar.Watchlist(COMPANIES)
    assert watchlist.match({"name": "Alpha Inc", "cik": "9", "form": "D"}) is None
    assert watchlist.match({"name": "Twin Labs Inc", "cik": "9", "form": "D"}) is None


def test_run_emits_items_and_catches_up_business_days(edgar, tmp_path: Path) -> None:
    fetcher = _fetcher(
        edgar,
        {
            "20261102": _index(_line("D", "Acme Robotics, Inc.", "1900010", "20261102", "0001900010-26-000001")),
            "20261030": _index(
                _line("S-1", "Horizon Quantum Corp", "1900011", "20261030", "0001900011-26-000001"),
                _line("D/A", "Acme Robotics Inc", "1900010", "20261030", "0001900010-26-000002"),
                _line("D", "Random Fund LP", "1900012", "20261030", "0001900012-26-000001"),
            ),
        }
    )
    code, payload = _run(edgar, tmp_path, fetcher, days=4)
    assert code == 0
    assert [u.rsplit("form.", 1)[1] for u in fetcher.calls] == ["20261102.idx", "20261030.idx"]
    assert payload["days_weekend"] == 2
    assert payload["filings_scanned"] == 4
    keys = [i["dedup_key"] for i in payload["items"]]
    assert keys == [
        "edgar://0001900010-26-000001",
        "edgar://0001900011-26-000001",
        "edgar://0001900010-26-000002",
    ]
    first, _, amendment = payload["items"]
    assert first["company"] == "Acme Robotics"
    assert first["source_kind"] == "edgar"
    assert first["link"] == (
        "https://www.sec.gov/Archives/edgar/data/1900010/000190001026000001/0001900010-26-000001-index.htm"
    )
    assert "pre_extracted" not in first
    assert "amendment" in amendment["headline"]


def test_run_partial_failure_stays_green(edgar, tmp_path: Path) -> None:
    fetcher = _fetcher(
        edgar,
        {
            "20261102": error.HTTPError("u", 403, "Forbidden", None, None),
            "20261030": _index(_line("D", "Acme Robotics", "1900010", "20261030", "0001900010-26-000003")),
        }
    )
    code, payload = _run(edgar, tmp_path, fetcher, days=4)
    assert code == 0
    assert payload["days_failed"] == 1
    assert len(payload["items"]) == 1


def test_run_exits_one_when_every_day_fails(edgar, tmp_path: Path) -> None:
    forbidden = error.HTTPError("u", 403, "Forbidden", None, None)
    code, payload = _run(edgar, tmp_path, _fetcher(edgar, {"20261102": forbidden, "20261030": forbidden}), days=4)
    assert code == 1
    assert payload["error_rate_alarm"] is True


def test_missing_index_is_empty_not_failure(edgar, tmp_path: Path) -> None:
    code, payload = _run(edgar, tmp_path, _fetcher(edgar, {}), days=4)
    assert code == 0
    assert payload["days_missing"] == 2
    assert payload["items"] == []


def test_retries_transient_status(edgar, tmp_path: Path) -> None:
    attempts = {"n": 0}
    body = _index(_line("D", "Acme Robotics", "1900010", "20261030", "0001900010-26-000004"))

    def fetch(url: str) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise error.HTTPError(url, 503, "busy", None, None)
        return body

    out = tmp_path / "e.json"
    code = edgar.run(COMPANIES, out, fetch, days=1, pace_seconds=0, sleep=_no_sleep, now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert code == 0
    assert attempts["n"] == 2
    assert len(json.loads(out.read_text())["items"]) == 1


def test_main_without_user_agent_is_a_noop(edgar, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    out = tmp_path / "e.json"
    assert edgar.main(["--out", str(out)]) == 0
    assert not out.exists()
    assert "SEC_EDGAR_USER_AGENT" in capsys.readouterr().err


def test_fetcher_sends_declared_user_agent_and_gunzips(edgar, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return gzip.compress(b"hello")

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return Response()

    monkeypatch.setattr(edgar.request, "urlopen", fake_urlopen)
    assert edgar.make_fetcher("Team ops@example.org")("https://x") == "hello"
    assert seen["ua"] == "Team ops@example.org"


def test_fetcher_maps_404_to_index_missing(edgar, monkeypatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 404, "nf", None, None)

    monkeypatch.setattr(edgar.request, "urlopen", fake_urlopen)
    with pytest.raises(edgar.IndexMissing):
        edgar.make_fetcher("Team ops@example.org")("https://x")
