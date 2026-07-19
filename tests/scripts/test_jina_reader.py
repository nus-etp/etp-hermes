"""Unit tests for scripts/jina-reader.py."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error
from urllib.request import Request

import pytest


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
        return _FakeResponse(spec["body"], spec.get("status", 200))

    return urlopen, calls


@pytest.fixture()
def jina(tmp_repo: Path, monkeypatch, scripts_module_loader):
    mod = scripts_module_loader("jina-reader")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_repo)
    monkeypatch.setattr(mod, "COMPANIES_FILE", tmp_repo / "data" / "companies.json")
    monkeypatch.setattr(mod, "CHANGED_FILE", tmp_repo / "data" / "changed-sources.json")
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_repo / "data" / "jina-cache")
    monkeypatch.setattr(mod, "CACHE_INDEX_FILE", tmp_repo / "data" / "jina-cache" / "index.json")
    monkeypatch.setattr(mod, "ITEMS_FILE", tmp_repo / "data" / "jina-items.json")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_DAILY_BUDGET", raising=False)
    return mod


def _write_inputs(
    tmp_repo: Path,
    companies: list[dict],
    changed_per_company: dict[str, list[str]] | None,
) -> None:
    (tmp_repo / "data" / "companies.json").write_text(json.dumps(companies))
    if changed_per_company is not None:
        (tmp_repo / "data" / "changed-sources.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-05-28T00:00:00+00:00",
                    "firehose": [],
                    "per_company": changed_per_company,
                }
            )
        )


READER = "https://r.jina.ai/"


def test_extracts_items_from_listing_page(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "news", "url": "https://acme.example/news"}],
        }
    ]
    markdown = b"""# Acme Newsroom

## Acme raises 20M Series B
January 15, 2026
[Read more](/news/series-b)

## Acme launches new product
[Read more](https://acme.example/news/launch)

## [Inline-link Headline](https://acme.example/news/inline)
2026-01-10
"""
    urlopen, calls = _make_urlopen(
        {READER + "https://acme.example/news": [{"body": markdown}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/news"]})

    assert jina.main() == 0

    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    items = out["per_company"]["Acme"]["https://acme.example/news"]
    headlines = [i["headline"] for i in items]
    links = [i["link"] for i in items]
    assert "Acme raises 20M Series B" in headlines
    assert "Acme launches new product" in headlines
    assert "Inline-link Headline" in headlines
    assert "https://acme.example/news/series-b" in links  # relative resolved
    assert all(i["pre_extracted"] is True for i in items)
    assert any(i.get("pubDate") == "January 15, 2026" for i in items)
    assert any(i.get("pubDate") == "2026-01-10" for i in items)
    assert all(i["label"] == "news" for i in items)
    assert out["extraction_failed"] == []
    assert out["budget_used"] == 1
    assert len(calls) == 1


def test_no_headings_marks_extraction_failed(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "news", "url": "https://acme.example/news"}],
        }
    ]
    markdown = b"just a wall of prose with no headings and no links to speak of."
    urlopen, _ = _make_urlopen(
        {READER + "https://acme.example/news": [{"body": markdown}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/news"]})

    assert jina.main() == 0
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    assert out["per_company"] == {}
    assert out["extraction_failed"] == ["https://acme.example/news"]


def test_http_error_marks_extraction_failed_and_continues(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [
                {"type": "html_scrape", "label": "a", "url": "https://acme.example/a"},
                {"type": "html_scrape", "label": "b", "url": "https://acme.example/b"},
            ],
        }
    ]
    err = error.HTTPError(
        READER + "https://acme.example/a",
        500,
        "Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )
    good_md = b"## Acme news\n[link](https://acme.example/b/post)\n"
    urlopen, _ = _make_urlopen(
        {
            READER + "https://acme.example/a": [err],
            READER + "https://acme.example/b": [{"body": good_md}],
        }
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(
        tmp_repo,
        companies,
        {"Acme": ["https://acme.example/a", "https://acme.example/b"]},
    )

    assert jina.main() == 0
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    assert out["extraction_failed"] == ["https://acme.example/a"]
    assert "https://acme.example/b" in out["per_company"]["Acme"]


def test_budget_cap_defers_remaining(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [
                {"type": "html_scrape", "label": "a", "url": "https://acme.example/a"},
                {"type": "html_scrape", "label": "b", "url": "https://acme.example/b"},
                {"type": "html_scrape", "label": "c", "url": "https://acme.example/c"},
            ],
        }
    ]
    md = b"## h\n[link](https://acme.example/article)\n"
    urlopen, calls = _make_urlopen(
        {
            READER + "https://acme.example/a": [{"body": md}],
            READER + "https://acme.example/b": [{"body": md}],
        }
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    monkeypatch.setenv("JINA_DAILY_BUDGET", "2")
    _write_inputs(
        tmp_repo,
        companies,
        {
            "Acme": [
                "https://acme.example/a",
                "https://acme.example/b",
                "https://acme.example/c",
            ]
        },
    )

    assert jina.main() == 0
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    assert out["budget_used"] == 2
    assert out["budget_limit"] == 2
    assert out["deferred"] == ["https://acme.example/c"]
    assert len(calls) == 2


def test_skips_html_scrape_not_in_changed_list(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [
                {"type": "html_scrape", "label": "stale", "url": "https://acme.example/stale"},
                {"type": "html_scrape", "label": "fresh", "url": "https://acme.example/fresh"},
            ],
        }
    ]
    md = b"## h\n[link](https://acme.example/post)\n"
    urlopen, calls = _make_urlopen(
        {READER + "https://acme.example/fresh": [{"body": md}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/fresh"]})

    assert jina.main() == 0
    assert [c[0] for c in calls] == [READER + "https://acme.example/fresh"]


def test_authorization_header_set_when_api_key_present(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "n", "url": "https://acme.example/n"}],
        }
    ]
    urlopen, calls = _make_urlopen(
        {READER + "https://acme.example/n": [{"body": b"## h\n[link](https://acme.example/post)\n"}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    monkeypatch.setenv("JINA_API_KEY", "jina_secret_xyz")
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/n"]})

    assert jina.main() == 0
    hdrs = dict(calls[0][1])
    assert hdrs.get("Authorization") == "Bearer jina_secret_xyz"


def test_fresh_cache_reuses_without_network(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "n", "url": "https://acme.example/n"}],
        }
    ]
    # Pre-populate cache as if fetched 1 hour ago.
    cache_dir = tmp_repo / "data" / "jina-cache"
    cache_dir.mkdir(parents=True)
    cached_md = "## Cached headline\n[link](https://acme.example/article)\n"
    cache_file = jina._cache_path_for("https://acme.example/n")
    cache_file.write_text(cached_md)
    now = datetime.now(timezone.utc)
    cache_index = {
        "https://acme.example/n": {
            "fetched_at": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            "status": 200,
            "cache_file": cache_file.name,
        }
    }
    (cache_dir / "index.json").write_text(json.dumps(cache_index))

    def boom(*a, **kw):  # noqa: ANN001, ARG001
        raise AssertionError("fresh cache must not trigger network")

    monkeypatch.setattr(jina.request, "urlopen", boom)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/n"]})

    assert jina.main() == 0
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    assert out["budget_used"] == 0
    assert out["cache_hits"] == 1
    items = out["per_company"]["Acme"]["https://acme.example/n"]
    assert items[0]["headline"] == "Cached headline"


def test_stale_cache_triggers_refetch(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "n", "url": "https://acme.example/n"}],
        }
    ]
    cache_dir = tmp_repo / "data" / "jina-cache"
    cache_dir.mkdir(parents=True)
    cache_file = jina._cache_path_for("https://acme.example/n")
    cache_file.write_text("## stale\n[link](https://acme.example/old)\n")
    long_ago = datetime.now(timezone.utc) - timedelta(days=2)
    (cache_dir / "index.json").write_text(
        json.dumps(
            {
                "https://acme.example/n": {
                    "fetched_at": long_ago.isoformat(timespec="seconds"),
                    "status": 200,
                    "cache_file": cache_file.name,
                }
            }
        )
    )

    fresh_md = b"## Fresh headline\n[link](https://acme.example/new)\n"
    urlopen, calls = _make_urlopen(
        {READER + "https://acme.example/n": [{"body": fresh_md}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/n"]})

    assert jina.main() == 0
    assert len(calls) == 1
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    items = out["per_company"]["Acme"]["https://acme.example/n"]
    assert items[0]["headline"] == "Fresh headline"


def test_filters_self_link_and_image_link(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [{"type": "html_scrape", "label": "n", "url": "https://acme.example/news"}],
        }
    ]
    markdown = b"""## Page itself
[Acme News](https://acme.example/news)

## An image
[Photo](https://cdn.example/cover.png)

## Real article
[Read](https://acme.example/news/launch)
"""
    urlopen, _ = _make_urlopen(
        {READER + "https://acme.example/news": [{"body": markdown}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, {"Acme": ["https://acme.example/news"]})

    assert jina.main() == 0
    out = json.loads((tmp_repo / "data" / "jina-items.json").read_text())
    items = out["per_company"]["Acme"]["https://acme.example/news"]
    headlines = [i["headline"] for i in items]
    assert headlines == ["Real article"]


@pytest.fixture()
def fallback(scripts_module_loader):
    return scripts_module_loader("jina_fallback")


def test_ignores_avif_and_asset_cdn_links(fallback) -> None:
    base = "https://invigilo.ai/blog"
    # .avif thumbnail on an asset CDN — both the extension and the host disqualify it.
    assert not fallback._is_useful_news_link(
        "https://cdn.prod.website-files.com/abc/cover.avif", base
    )
    # Asset-CDN host disqualifies even a non-image path.
    assert not fallback._is_useful_news_link(
        "https://assets.website-files.com/abc/page", base
    )
    assert not fallback._is_useful_news_link(
        "https://assets-global.website-files.com/abc/page", base
    )
    # New media/icon extensions on an otherwise-fine host.
    for ext in (".avif", ".webm", ".mp4", ".ico"):
        assert not fallback._is_useful_news_link(
            f"https://other.example/media/file{ext}", base
        )
    # A real same-host article link still passes.
    assert fallback._is_useful_news_link("https://invigilo.ai/blog/real-post", base)


def test_webflow_card_prefers_same_host_article_over_thumbnail(fallback) -> None:
    base = "https://invigilo.ai/blog"
    markdown = """## AI safety on construction sites

[thumbnail](https://cdn.prod.website-files.com/abc/cover.avif)
[Read the article](https://invigilo.ai/blog/ai-safety-construction)
"""
    items = fallback.extract_items(markdown, base)
    assert len(items) == 1
    assert items[0]["link"] == "https://invigilo.ai/blog/ai-safety-construction"
    assert items[0]["headline"] == "AI safety on construction sites"


def test_case3_falls_back_to_first_useful_link_when_no_same_host(fallback) -> None:
    base = "https://invigilo.ai/blog"
    # No same-host link in the window → keep the first off-host (but useful) one.
    markdown = """## External coverage

[Coverage on partner site](https://partner.example/story)
"""
    items = fallback.extract_items(markdown, base)
    assert len(items) == 1
    assert items[0]["link"] == "https://partner.example/story"


def test_missing_changed_sources_cold_starts_all_html_scrape(jina, tmp_repo, monkeypatch) -> None:
    companies = [
        {
            "name": "Acme",
            "sources": [
                {"type": "html_scrape", "label": "n", "url": "https://acme.example/news"},
                {"type": "rss", "label": "r", "url": "https://acme.example/feed"},
            ],
        }
    ]
    md = b"## h\n[link](https://acme.example/post)\n"
    urlopen, calls = _make_urlopen(
        {READER + "https://acme.example/news": [{"body": md}]}
    )
    monkeypatch.setattr(jina.request, "urlopen", urlopen)
    _write_inputs(tmp_repo, companies, None)  # no changed-sources.json

    assert jina.main() == 0
    assert [c[0] for c in calls] == [READER + "https://acme.example/news"]
