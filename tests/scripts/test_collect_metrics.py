"""Unit tests for scripts/collect_metrics.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _urlopen_from(table: dict[str, Any]):
    def urlopen(req, timeout: float | None = None):  # noqa: ARG001
        url = req.full_url
        for prefix, spec in table.items():
            if url.startswith(prefix):
                if isinstance(spec, Exception):
                    raise spec
                if callable(spec):
                    spec = spec(url)
                if isinstance(spec, _FakeResponse):
                    return spec
                return _FakeResponse(json.dumps(spec).encode("utf-8"))
        raise AssertionError(f"unexpected urlopen({url!r})")

    return urlopen


def test_slugify_matches_synthesis_rule(scripts_module_loader) -> None:
    mod = scripts_module_loader("collect_metrics")
    assert mod.slugify("Carousell") == "carousell"
    assert mod.slugify("Horizon Quantum Computing") == "horizon-quantum-computing"
    assert mod.slugify("NEU Battery Materials") == "neu-battery-materials"
    assert mod.slugify("  --Foo, Bar-- ") == "foo-bar"


def test_parse_github_org(scripts_module_loader) -> None:
    mod = scripts_module_loader("collect_metrics")
    assert mod.parse_github_org("https://github.com/carousell.atom") == "carousell"
    assert mod.parse_github_org("https://github.com/acme") == "acme"
    assert mod.parse_github_org("https://example.com/not-github") is None


def test_upsert_record_appends_new_date(scripts_module_loader, tmp_path: Path) -> None:
    mod = scripts_module_loader("collect_metrics")
    path = tmp_path / "acme.jsonl"
    mod.upsert_record(path, {"date": "2026-05-20", "hn_30d": 3})
    mod.upsert_record(path, {"date": "2026-05-21", "hn_30d": 4})
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert [r["date"] for r in rows] == ["2026-05-20", "2026-05-21"]


def test_upsert_record_replaces_same_date(scripts_module_loader, tmp_path: Path) -> None:
    mod = scripts_module_loader("collect_metrics")
    path = tmp_path / "acme.jsonl"
    mod.upsert_record(path, {"date": "2026-05-20", "hn_30d": 3})
    mod.upsert_record(path, {"date": "2026-05-20", "hn_30d": 7})
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["hn_30d"] == 7


def test_hn_mentions_30d_parses_nbHits(scripts_module_loader, monkeypatch) -> None:
    import datetime as dt

    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from(
        {"https://hn.algolia.com/api/v1/search": {"nbHits": 42, "hits": []}}
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    n = mod.hn_mentions_30d("Acme", dt.datetime(2026, 5, 20, tzinfo=dt.timezone.utc))
    assert n == 42


def test_hn_mentions_30d_returns_none_on_error(scripts_module_loader, monkeypatch) -> None:
    import datetime as dt

    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from({"https://hn.algolia.com/": error.URLError("down")})
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert (
        mod.hn_mentions_30d("Acme", dt.datetime(2026, 5, 20, tzinfo=dt.timezone.utc))
        is None
    )


def test_gdelt_articles_7d_counts_articles(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from(
        {
            "https://api.gdeltproject.org/api/v2/doc/doc": {
                "articles": [{"url": "https://a"}, {"url": "https://b"}]
            }
        }
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod.gdelt_articles_7d("Acme") == 2


def test_gdelt_rate_limit_text_returns_none_after_retries(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    # GDELT signals rate limits with HTTP 200 + plaintext body.
    rate_limited = _FakeResponse(b"Please limit requests to ...")
    urlopen = _urlopen_from(
        {"https://api.gdeltproject.org/api/v2/doc/doc": lambda _: rate_limited}
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    # Patch sleep so the test isn't slow.
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    assert mod.gdelt_articles_7d("Acme") is None


def test_lever_open_jobs_returns_len(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from(
        {"https://api.lever.co/v0/postings/acme": [{"id": 1}, {"id": 2}, {"id": 3}]}
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod.lever_open_jobs("https://api.lever.co/v0/postings/acme?mode=json") == 3


def test_lever_open_jobs_returns_none_when_not_list(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from(
        {"https://api.lever.co/v0/postings/acme": {"error": "bad"}}
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod.lever_open_jobs("https://api.lever.co/v0/postings/acme?mode=json") is None


def test_iter_selected_filters_by_name_and_slug(scripts_module_loader) -> None:
    mod = scripts_module_loader("collect_metrics")
    companies = [
        {"name": "Acme Inc"},
        {"name": "Beta Co"},
        {"name": "Gamma"},
    ]
    out = list(mod.iter_selected(companies, ["acme-inc", "GAMMA"]))
    assert {c["name"] for c in out} == {"Acme Inc", "Gamma"}


def test_polite_gate_blocks_disallowed_paths(scripts_module_loader, monkeypatch) -> None:
    import pytest

    mod = scripts_module_loader("collect_metrics")
    robots = _FakeResponse(b"User-agent: *\nDisallow: /private/\n")
    urlopen = _urlopen_from({"https://example.com/robots.txt": robots})
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    gate = mod.PoliteGate()
    with pytest.raises(mod.Disallowed):
        gate.before_request("https://example.com/private/secret")
    # An allowed path on the same host passes.
    gate.before_request("https://example.com/public/ok")


def test_polite_gate_fails_open_without_robots(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from({"https://example.com/robots.txt": error.URLError("nope")})
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    gate = mod.PoliteGate()
    # Missing/unreachable robots.txt → allow all, no exception.
    gate.before_request("https://example.com/anything")


def test_polite_gate_spaces_same_host_requests(scripts_module_loader, monkeypatch) -> None:
    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from(
        {"https://api.gdeltproject.org/robots.txt": error.URLError("none")}
    )
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    slept: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(mod.time, "monotonic", lambda: 1000.0)  # frozen clock

    gate = mod.PoliteGate()
    base = "https://api.gdeltproject.org/api/v2/doc/doc?query="
    gate.before_request(base + "a")  # first call: no wait
    gate.before_request(base + "b")  # second: spaced by the GDELT floor
    assert slept == [mod.GDELT_PAUSE_S]


def test_collect_one_records_date_and_hn(scripts_module_loader, monkeypatch) -> None:
    import datetime as dt

    mod = scripts_module_loader("collect_metrics")
    urlopen = _urlopen_from({"https://hn.algolia.com/": {"nbHits": 5}})
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    rec = mod.collect_one(
        {"name": "Acme"},
        dt.datetime(2026, 5, 20, tzinfo=dt.timezone.utc),
        gh_token=None,
    )
    assert rec["date"] == "2026-05-20"
    assert rec["hn_30d"] == 5
    # No structured sources → gdelt not attempted.
    assert "gdelt_7d" not in rec
    # No github_org / lever sources → those keys omitted.
    assert "github" not in rec
    assert "lever" not in rec
