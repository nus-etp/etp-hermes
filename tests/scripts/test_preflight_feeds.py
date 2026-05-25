"""Unit tests for scripts/preflight-feeds.py."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any
from urllib import error
from urllib.request import Request

import pytest


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200) -> None:
        self._body = body
        self._headers = headers
        self.status = status
        self.headers = headers  # type: ignore[assignment]

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _make_urlopen(responses: dict[str, list[Any]]):
    calls: list[tuple[str, dict[str, str]]] = []

    def urlopen(req: Request, timeout: float | None = None) -> Any:  # noqa: ARG001
        url = req.full_url
        hdrs = {k: v for k, v in req.header_items()}
        calls.append((url, hdrs))
        if url not in responses or not responses[url]:
            raise AssertionError(f"unexpected urlopen({url!r})")
        spec = responses[url].pop(0)
        if isinstance(spec, Exception):
            raise spec
        return _FakeResponse(spec["body"], spec["headers"], spec.get("status", 200))

    return urlopen, calls


@pytest.fixture()
def preflight(tmp_repo: Path, monkeypatch, scripts_module_loader):
    mod = scripts_module_loader("preflight-feeds")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_repo)
    monkeypatch.setattr(mod, "FEEDS_FILE", tmp_repo / "data" / "feeds.json")
    monkeypatch.setattr(mod, "COMPANIES_FILE", tmp_repo / "data" / "companies.json")
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_repo / "data" / "feed-cache.json")
    monkeypatch.setattr(mod, "CHANGED_FILE", tmp_repo / "data" / "changed-sources.json")
    return mod


def _write_inputs(
    tmp_repo: Path,
    feeds: list[dict],
    companies: list[dict],
    cache: dict | None = None,
) -> None:
    (tmp_repo / "data" / "feeds.json").write_text(json.dumps(feeds))
    (tmp_repo / "data" / "companies.json").write_text(json.dumps(companies))
    if cache is not None:
        (tmp_repo / "data" / "feed-cache.json").write_text(json.dumps(cache))


def test_first_run_all_feeds_marked_changed(preflight, tmp_repo, monkeypatch) -> None:
    feeds = [{"name": "Vulcan Post", "type": "firehose", "url": "https://vulcanpost.com/feed/"}]
    body = b"<rss><item>hello</item></rss>"
    headers = {"ETag": 'W/"abc"', "Last-Modified": "Mon, 01 Jan 2026 00:00:00 GMT"}
    urlopen, calls = _make_urlopen(
        {"https://vulcanpost.com/feed/": [{"body": body, "headers": headers}]}
    )
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, feeds, [])

    assert preflight.main() == 0

    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    cache = json.loads((tmp_repo / "data" / "feed-cache.json").read_text())

    assert changed["firehose"] == ["https://vulcanpost.com/feed/"]
    assert changed["per_company"] == {}
    entry = cache["https://vulcanpost.com/feed/"]
    assert entry["etag"] == 'W/"abc"'
    assert entry["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert entry["last_status"] == 200
    assert "If-None-Match" not in dict(calls[0][1])


def test_304_marks_unchanged_and_omits_from_changed_list(preflight, tmp_repo, monkeypatch) -> None:
    feeds = [{"name": "Vulcan Post", "type": "firehose", "url": "https://vulcanpost.com/feed/"}]
    prior_cache = {
        "https://vulcanpost.com/feed/": {
            "etag": 'W/"abc"',
            "last_modified": "Mon, 01 Jan 2026 00:00:00 GMT",
            "body_sha256": "deadbeef",
            "last_status": 200,
            "last_run": "2026-01-01T00:00:00+00:00",
            "last_changed": "2026-01-01T00:00:00+00:00",
        }
    }

    not_modified = error.HTTPError(
        "https://vulcanpost.com/feed/", 304, "Not Modified", hdrs=None, fp=io.BytesIO(b"")  # type: ignore[arg-type]
    )
    urlopen, calls = _make_urlopen({"https://vulcanpost.com/feed/": [not_modified]})
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, feeds, [], cache=prior_cache)

    assert preflight.main() == 0

    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    cache = json.loads((tmp_repo / "data" / "feed-cache.json").read_text())

    assert changed["firehose"] == []
    assert cache["https://vulcanpost.com/feed/"]["last_status"] == 304
    assert cache["https://vulcanpost.com/feed/"]["etag"] == 'W/"abc"'
    sent_headers = dict(calls[0][1])
    assert sent_headers.get("If-none-match") == 'W/"abc"' or sent_headers.get("If-None-Match") == 'W/"abc"'


def test_body_change_with_no_etag_detected_via_sha256(preflight, tmp_repo, monkeypatch) -> None:
    feeds = [{"name": "Acme", "type": "firehose", "url": "https://acme.example/feed"}]
    old_body = b"first"
    new_body = b"second"
    prior_cache = {
        "https://acme.example/feed": {
            "etag": None,
            "last_modified": None,
            "body_sha256": hashlib.sha256(old_body).hexdigest(),
            "last_status": 200,
            "last_run": "2026-01-01T00:00:00+00:00",
            "last_changed": "2026-01-01T00:00:00+00:00",
        }
    }
    urlopen, _ = _make_urlopen(
        {"https://acme.example/feed": [{"body": new_body, "headers": {}}]}
    )
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, feeds, [], cache=prior_cache)

    assert preflight.main() == 0
    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    cache = json.loads((tmp_repo / "data" / "feed-cache.json").read_text())
    assert changed["firehose"] == ["https://acme.example/feed"]
    assert cache["https://acme.example/feed"]["body_sha256"] == hashlib.sha256(new_body).hexdigest()


def test_body_unchanged_same_hash_marked_unchanged(preflight, tmp_repo, monkeypatch) -> None:
    feeds = [{"name": "Acme", "type": "firehose", "url": "https://acme.example/feed"}]
    body = b"same content"
    prior_cache = {
        "https://acme.example/feed": {
            "etag": None,
            "last_modified": None,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "last_status": 200,
            "last_run": "2026-01-01T00:00:00+00:00",
            "last_changed": "2026-01-01T00:00:00+00:00",
        }
    }
    urlopen, _ = _make_urlopen(
        {"https://acme.example/feed": [{"body": body, "headers": {}}]}
    )
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, feeds, [], cache=prior_cache)

    assert preflight.main() == 0
    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    assert changed["firehose"] == []


def test_html_scrape_sources_always_changed_no_http(preflight, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [
                {"type": "html_scrape", "label": "team", "url": "https://acme.example/team"},
            ],
        }
    ]

    def fail(*a, **kw):  # noqa: ANN001, ARG001
        raise AssertionError("html_scrape sources must not be fetched in preflight")

    monkeypatch.setattr(preflight.request, "urlopen", fail)
    _write_inputs(tmp_repo, [], companies)

    assert preflight.main() == 0
    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    assert changed["per_company"] == {"Acme": ["https://acme.example/team"]}


def test_url_error_marks_changed_so_agent_retries(preflight, tmp_repo, monkeypatch) -> None:
    feeds = [{"name": "Flaky", "type": "firehose", "url": "https://flaky.example/feed"}]
    urlopen, _ = _make_urlopen(
        {"https://flaky.example/feed": [error.URLError("conn refused")]}
    )
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, feeds, [])

    assert preflight.main() == 0
    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    cache = json.loads((tmp_repo / "data" / "feed-cache.json").read_text())
    assert changed["firehose"] == ["https://flaky.example/feed"]
    assert cache["https://flaky.example/feed"]["last_status"] == -1
    assert "last_error" in cache["https://flaky.example/feed"]


def test_per_company_sources_grouped_under_company_name(preflight, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Carousell",
            "sources": [
                {"type": "rss", "label": "press", "url": "https://press.carousell.com/feed/"},
                {"type": "github_org", "label": "gh", "url": "https://github.com/carousell.atom"},
            ],
        }
    ]
    body = b"<rss/>"
    urlopen, _ = _make_urlopen(
        {
            "https://press.carousell.com/feed/": [{"body": body, "headers": {"ETag": '"e1"'}}],
            "https://github.com/carousell.atom": [{"body": body, "headers": {"ETag": '"e2"'}}],
        }
    )
    monkeypatch.setattr(preflight.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, [], companies)

    assert preflight.main() == 0
    changed = json.loads((tmp_repo / "data" / "changed-sources.json").read_text())
    assert set(changed["per_company"]["Carousell"]) == {
        "https://press.carousell.com/feed/",
        "https://github.com/carousell.atom",
    }
