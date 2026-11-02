"""Unit tests for scripts/sitemap_diff.py."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib import error

import pytest

NOW = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@pytest.fixture()
def sd(scripts_module_loader):
    return scripts_module_loader("sitemap_diff")


def _urlset(entries: dict[str, str]) -> bytes:
    rows = "".join(
        f"<url><loc>{loc}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>"
        for loc, lastmod in entries.items()
    )
    return f'<?xml version="1.0"?><urlset {NS}>{rows}</urlset>'.encode()


def _index(children: dict[str, str]) -> bytes:
    rows = "".join(
        f"<sitemap><loc>{loc}</loc><lastmod>{lastmod}</lastmod></sitemap>"
        for loc, lastmod in children.items()
    )
    return f'<?xml version="1.0"?><sitemapindex {NS}>{rows}</sitemapindex>'.encode()


def _http_error(url: str, code: int) -> error.HTTPError:
    return error.HTTPError(url, code, "x", {}, None)


def _fetcher(pages: dict[str, object]):
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        body = pages.get(url)
        if body is None:
            raise _http_error(url, 404)
        if isinstance(body, Exception):
            raise body
        return body  # type: ignore[return-value]

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _no_sleep(_secs: float) -> None:
    return None


COMPANIES = [
    {
        "name": "Acme Robotics",
        "aliases": [],
        "country": "Singapore",
        "identifiers": {"website": "https://www.acme.ai"},
    },
    {
        "name": "Patsnap",
        "aliases": [],
        "country": "Singapore",
        "sources": [{"type": "recruitee_jobs", "url": "https://patsnap.recruitee.com/api/offers"}],
    },
    {
        "name": "Gone Co",
        "aliases": [],
        "country": "Singapore",
        "status": "inactive",
        "identifiers": {"website": "https://gone.sg"},
    },
]


def test_watchlist_domains_skip_ats_hosts_and_inactive(sd) -> None:
    assert sd.watchlist_domains(COMPANIES) == {"acme.ai": "Acme Robotics"}


def test_robots_sitemaps_parses_case_insensitive_lines(sd) -> None:
    text = "User-agent: *\nDisallow: /x\nSITEMAP: https://acme.ai/a.xml\nsitemap:https://acme.ai/b.xml\nSitemap: /rel.xml\n"
    assert sd.robots_sitemaps(text) == ["https://acme.ai/a.xml", "https://acme.ai/b.xml"]


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.ai/",
        "https://acme.ai/en/",
        "https://acme.ai/tag/robots/",
        "https://acme.ai/category/news/",
        "https://acme.ai/author/jane/",
        "https://acme.ai/blog/page/2/",
        "https://acme.ai/wp-content/uploads/deck.pdf",
        "https://acme.ai/logo.png",
        "https://acme.ai/privacy-policy/",
        "https://acme.ai/blog/?page=3",
        "https://acme.ai/unsubscribe_survey-148/",
        "https://acme.ai/products/widget",
        "mailto:hi@acme.ai",
    ],
)
def test_noise_urls_are_dropped(sd, url: str) -> None:
    assert sd.is_noise_url(url) is True


def test_content_urls_are_kept(sd) -> None:
    assert sd.is_noise_url("https://acme.ai/blog/acme-raises-series-a/") is False
    assert sd.is_noise_url("https://acme.ai/zh-hans/news/launch/") is False


def test_eligible_urls_collapse_locale_duplicates_and_other_hosts(sd) -> None:
    raw = {
        "https://acme.ai/news/launch/": "2026-10-01",
        "https://acme.ai/zh-hans/news/launch/": "2026-10-02",
        "https://acme.ai/id/news/launch/": "2026-10-02",
        "https://acme.ai/zh-hans/news/only-chinese/": "2026-10-03",
        "https://acme.ai/vi/tin-tuc/": "",
        "https://cdn.other.com/news/launch/": "",
        "https://blog.acme.ai/post/": "",
    }
    assert set(sd.eligible_urls(raw, "acme.ai")) == {
        "https://acme.ai/news/launch/",
        "https://acme.ai/zh-hans/news/only-chinese/",
        "https://acme.ai/vi/tin-tuc/",
        "https://blog.acme.ai/post/",
    }


def test_is_skipped_sitemap_by_name(sd) -> None:
    assert sd.is_skipped_sitemap("https://acme.ai/post_tag-sitemap.xml")
    assert sd.is_skipped_sitemap("https://acme.ai/sitemap_products_1.xml?from=1&to=9")
    assert sd.is_skipped_sitemap("https://acme.ai/wp-sitemap-users-1.xml")
    assert sd.is_skipped_sitemap("https://acme.ai/image-sitemap.xml")
    assert not sd.is_skipped_sitemap("https://acme.ai/post-sitemap.xml")
    assert not sd.is_skipped_sitemap("https://acme.ai/page-sitemap.xml")


def test_slug_title(sd) -> None:
    assert sd.slug_title("https://acme.ai/blog/acme-raises-series-a/") == "Acme Raises Series A"
    assert sd.slug_title("https://acme.ai/news/funding_round.html") == "Funding Round"
    assert sd.slug_title("https://acme.ai/news/big-news/2/") == "Big News"


def test_parse_lastmod_variants(sd) -> None:
    assert sd.normalise_lastmod("2026-10-30") == "2026-10-30T00:00:00+00:00"
    assert sd.normalise_lastmod("2026-10-30T08:00:00Z") == "2026-10-30T08:00:00+00:00"
    assert sd.normalise_lastmod("2026-10-30T16:00:00+08:00") == "2026-10-30T08:00:00+00:00"
    assert sd.normalise_lastmod("garbage") == ""
    assert sd.normalise_lastmod("") == ""


def test_crawl_follows_robots_index_and_skips_noisy_children(sd) -> None:
    fetch = _fetcher(
        {
            "https://acme.ai/robots.txt": b"Sitemap: https://acme.ai/sitemap_index.xml\n",
            "https://acme.ai/sitemap_index.xml": _index(
                {
                    "https://acme.ai/page-sitemap.xml": "2026-09-01",
                    "https://acme.ai/post-sitemap.xml.gz": "2026-10-30",
                    "https://acme.ai/category-sitemap.xml": "2026-10-30",
                }
            ),
            "https://acme.ai/post-sitemap.xml.gz": gzip.compress(
                _urlset({"https://acme.ai/blog/series-a/": "2026-10-30", "https://acme.ai/tag/x/": ""})
            ),
            "https://acme.ai/page-sitemap.xml": _urlset({"https://acme.ai/about/": "2026-09-01"}),
        }
    )
    result = sd.crawl_domain("acme.ai", fetch, _no_sleep)
    assert result["status"] == "ok"
    assert result["urls"] == {
        "https://acme.ai/about/": "2026-09-01T00:00:00+00:00",
        "https://acme.ai/blog/series-a/": "2026-10-30T00:00:00+00:00",
    }
    assert "https://acme.ai/category-sitemap.xml" not in fetch.calls
    assert fetch.calls.index("https://acme.ai/post-sitemap.xml.gz") < fetch.calls.index(
        "https://acme.ai/page-sitemap.xml"
    )


def test_crawl_falls_back_to_default_paths_and_www(sd) -> None:
    fetch = _fetcher(
        {
            "https://acme.ai/robots.txt": error.URLError("no A record"),
            "https://www.acme.ai/robots.txt": b"User-agent: *\n",
            "https://www.acme.ai/sitemap.xml": b"<html>soft 404</html>",
            "https://www.acme.ai/sitemap_index.xml": _urlset({"https://www.acme.ai/news/x/": ""}),
        }
    )
    result = sd.crawl_domain("acme.ai", fetch, _no_sleep)
    assert result["status"] == "ok"
    assert result["urls"] == {"https://www.acme.ai/news/x/": ""}


def test_crawl_reports_none_and_unreachable(sd) -> None:
    none = sd.crawl_domain("acme.ai", _fetcher({"https://acme.ai/robots.txt": b""}), _no_sleep)
    assert none["status"] == "none"
    down = sd.crawl_domain(
        "acme.ai",
        _fetcher(
            {
                "https://acme.ai/robots.txt": error.URLError("down"),
                "https://www.acme.ai/robots.txt": error.URLError("down"),
            }
        ),
        _no_sleep,
    )
    assert down["status"] == "unreachable"


def test_crawl_marks_partial_when_a_child_fails(sd) -> None:
    fetch = _fetcher(
        {
            "https://acme.ai/robots.txt": b"Sitemap: https://acme.ai/sitemap.xml\n",
            "https://acme.ai/sitemap.xml": _index(
                {"https://acme.ai/a.xml": "2026-10-01", "https://acme.ai/b.xml": "2026-10-02"}
            ),
            "https://acme.ai/a.xml": _urlset({"https://acme.ai/news/a/": "2026-10-01"}),
            "https://acme.ai/b.xml": error.URLError("timeout"),
        }
    )
    result = sd.crawl_domain("acme.ai", fetch, _no_sleep)
    assert result["status"] == "partial"
    assert result["urls"] == {"https://acme.ai/news/a/": "2026-10-01T00:00:00+00:00"}


def test_domain_items_window_cap_and_order(sd) -> None:
    changes = [
        ("https://acme.ai/news/old/", "2026-09-01T00:00:00+00:00", "new"),
        ("https://acme.ai/news/undated/", "", "new"),
        ("https://acme.ai/news/undated-update/", "", "updated"),
    ] + [(f"https://acme.ai/news/n{i}/", f"2026-10-2{i}T00:00:00+00:00", "updated") for i in range(8)]
    items = sd.domain_items("Acme Robotics", "acme.ai", changes, NOW, window_days=14, cap=3)
    assert [it["link"] for it in items] == [
        "https://acme.ai/news/undated/",
        "https://acme.ai/news/n7/",
        "https://acme.ai/news/n6/",
    ]
    first = items[0]
    assert first["source_kind"] == "sitemap"
    assert first["dedup_key"] == first["link"]
    assert first["pubDate"] == NOW.isoformat(timespec="seconds")
    assert first["headline"] == "Undated"
    assert "pre_extracted" not in first


def _run(sd, tmp_path: Path, pages: dict[str, object]) -> tuple[int, dict]:
    code = sd.run(
        COMPANIES,
        tmp_path / "state.json",
        tmp_path / "items.json",
        fetcher=_fetcher(pages),
        sleep=_no_sleep,
        now=NOW,
    )
    return code, json.loads((tmp_path / "items.json").read_text())


def _site(entries: dict[str, str]) -> dict[str, object]:
    return {
        "https://acme.ai/robots.txt": b"Sitemap: https://acme.ai/sitemap.xml\n",
        "https://acme.ai/sitemap.xml": _urlset(entries),
    }


def test_run_seeds_then_emits_new_and_moved_pages(sd, tmp_path: Path) -> None:
    base = {
        "https://acme.ai/about/": "2026-10-01",
        "https://acme.ai/blog/launch/": "2026-10-10",
        "https://acme.ai/blog/hiring/": "2026-10-10",
    }
    code, seeded = _run(sd, tmp_path, _site(base))
    assert code == 0
    assert seeded["items"] == []
    assert seeded["stats"]["seeded"] == 1

    later = dict(base)
    later["https://acme.ai/blog/launch/"] = "2026-10-31"
    later["https://acme.ai/blog/hiring/"] = "2026-10-05"
    later["https://acme.ai/blog/acme-raises-series-a/"] = "2026-11-01"
    code, diffed = _run(sd, tmp_path, _site(later))
    assert code == 0
    assert [(it["link"], it["sitemap_change"]) for it in diffed["items"]] == [
        ("https://acme.ai/blog/acme-raises-series-a/", "new"),
        ("https://acme.ai/blog/launch/", "updated"),
    ]
    state = json.loads((tmp_path / "state.json").read_text())["domains"]["acme.ai"]
    assert state["urls"]["https://acme.ai/blog/launch/"] == "2026-10-31T00:00:00+00:00"


def test_run_suppresses_bulk_regeneration(sd, tmp_path: Path) -> None:
    base = {f"https://acme.ai/blog/p{i}/": "2026-10-01" for i in range(20)}
    _run(sd, tmp_path, _site(base))
    regenerated = {url: "2026-11-02" for url in base}
    _code, diffed = _run(sd, tmp_path, _site(regenerated))
    assert diffed["items"] == []
    assert diffed["stats"]["bulk_suppressed"] == ["acme.ai"]


def test_run_carries_state_when_sitemap_missing(sd, tmp_path: Path) -> None:
    _run(sd, tmp_path, _site({"https://acme.ai/blog/a/": "2026-10-01"}))
    _code, diffed = _run(sd, tmp_path, {})
    assert diffed["items"] == []
    state = json.loads((tmp_path / "state.json").read_text())["domains"]["acme.ai"]
    assert state["status"] == "none"
    assert state["urls"] == {"https://acme.ai/blog/a/": "2026-10-01T00:00:00+00:00"}
    _code, back = _run(sd, tmp_path, _site({"https://acme.ai/blog/a/": "2026-10-01"}))
    assert back["items"] == []


def test_run_partial_crawl_merges_previous_urls(sd, tmp_path: Path) -> None:
    _run(
        sd,
        tmp_path,
        _site({"https://acme.ai/news/a/": "2026-10-01", "https://acme.ai/news/b/": "2026-10-01"}),
    )
    pages = {
        "https://acme.ai/robots.txt": b"Sitemap: https://acme.ai/sitemap.xml\n",
        "https://acme.ai/sitemap.xml": _index(
            {"https://acme.ai/a.xml": "2026-10-01", "https://acme.ai/b.xml": "2026-10-01"}
        ),
        "https://acme.ai/a.xml": _urlset({"https://acme.ai/news/a/": "2026-10-01"}),
        "https://acme.ai/b.xml": error.URLError("timeout"),
    }
    _run(sd, tmp_path, pages)
    state = json.loads((tmp_path / "state.json").read_text())["domains"]["acme.ai"]
    assert set(state["urls"]) == {"https://acme.ai/news/a/", "https://acme.ai/news/b/"}


def test_run_alarm_when_every_domain_unreachable(sd, tmp_path: Path) -> None:
    companies = [
        {"name": f"Co{i}", "aliases": [], "country": "SG", "identifiers": {"website": f"https://co{i}.sg"}}
        for i in range(4)
    ]
    code = sd.run(
        companies,
        tmp_path / "state.json",
        tmp_path / "items.json",
        fetcher=_fetcher({f"https://{p}co{i}.sg/robots.txt": error.URLError("x") for i in range(4) for p in ("", "www.")}),
        sleep=_no_sleep,
        now=NOW,
    )
    assert code == 1
    assert json.loads((tmp_path / "items.json").read_text())["error_rate_alarm"] is True


def test_page_lastmod_moved(sd) -> None:
    before = {"acme.ai": {"urls": {"https://acme.ai/x/": "2026-10-01T00:00:00+00:00"}}}
    after = {
        "acme.ai": {
            "urls": {
                "https://acme.ai/x/": "2026-10-05T00:00:00+00:00",
                "https://acme.ai/y/": "2026-10-05T00:00:00+00:00",
            }
        }
    }
    assert sd.page_lastmod_moved(before, after, "https://acme.ai/x/") is True
    assert sd.page_lastmod_moved(before, after, "https://acme.ai/y/") is True
    assert sd.page_lastmod_moved(after, after, "https://acme.ai/x/") is False
    assert sd.page_lastmod_moved(before, after, "https://acme.ai/unknown/") is None
    assert sd.page_lastmod_moved({}, after, "https://acme.ai/x/") is None


GATE_COMPANIES = [
    {
        "name": "Acme Robotics",
        "aliases": [],
        "country": "Singapore",
        "identifiers": {"website": "https://www.acme.ai"},
        "sources": [
            {"type": "html_scrape", "url": "https://www.acme.ai/blog"},
            {"type": "html_scrape", "url": "https://acme.ai/news/"},
            {"type": "html_scrape", "url": "https://acme.ai/press/"},
            {"type": "html_scrape", "url": "https://acme.ai/careers/"},
        ],
    }
]


def _gate_run(sd, tmp_path: Path, pages: dict[str, object], changed: dict[str, list[str]]):
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps({"firehose": [], "per_company": changed}))
    sd.run(
        GATE_COMPANIES,
        tmp_path / "state.json",
        tmp_path / "items.json",
        fetcher=_fetcher(pages),
        sleep=_no_sleep,
        now=NOW,
        changed_path=changed_path,
    )
    return json.loads(changed_path.read_text()), json.loads((tmp_path / "items.json").read_text())


def test_gate_drops_html_scrape_urls_whose_lastmod_did_not_move(sd, tmp_path: Path) -> None:
    base = {
        "https://acme.ai/blog/": "2026-10-01",
        "https://acme.ai/news/": "2026-10-01",
        "https://acme.ai/press/": "2026-10-01",
    }
    changed = {"Acme Robotics": [s["url"] for s in GATE_COMPANIES[0]["sources"]]}
    seeded_changed, _ = _gate_run(sd, tmp_path, _site(base), changed)
    assert seeded_changed["per_company"] == changed
    assert "sitemap_gated" not in seeded_changed

    later = dict(base)
    later["https://acme.ai/press/"] = "2026-11-01"
    gated, items = _gate_run(sd, tmp_path, _site(later), changed)
    assert gated["per_company"] == {
        "Acme Robotics": ["https://acme.ai/press/", "https://acme.ai/careers/"]
    }
    assert gated["sitemap_gated"] == {
        "Acme Robotics": ["https://www.acme.ai/blog", "https://acme.ai/news/"]
    }
    assert items["stats"]["sitemap_gated"] == 2


def test_gate_fails_open_without_trusted_sitemap(sd, tmp_path: Path) -> None:
    changed = {"Acme Robotics": ["https://www.acme.ai/blog"]}
    _gate_run(sd, tmp_path, _site({"https://acme.ai/blog/": "2026-10-01"}), changed)
    gated, items = _gate_run(sd, tmp_path, {}, changed)
    assert gated["per_company"] == changed
    assert items["stats"]["sitemap_gated"] == 0


def test_gate_ignores_pages_without_lastmod(sd, tmp_path: Path) -> None:
    changed = {"Acme Robotics": ["https://www.acme.ai/blog"]}
    pages = _site({"https://acme.ai/blog/": ""})
    _gate_run(sd, tmp_path, pages, changed)
    gated, _ = _gate_run(sd, tmp_path, pages, changed)
    assert gated["per_company"] == changed


def test_gate_noop_without_changed_sources_file(sd, tmp_path: Path) -> None:
    _code, items = _run(sd, tmp_path, _site({"https://acme.ai/blog/": "2026-10-01"}))
    assert items["stats"]["sitemap_gated"] == 0
