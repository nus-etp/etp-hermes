"""Unit tests for scripts/portfolio_lists.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error

import pytest

NOW = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)

COMPANIES: list[dict[str, Any]] = [
    {"name": "Wareflex", "aliases": [], "description": "VN logistics"},
    {"name": "Cellivate Technologies", "aliases": ["Cellivate"], "description": "SG biotech"},
    {
        "name": "Belli AI",
        "aliases": ["Belli"],
        "description": "air cargo",
        "sources": [{"type": "rss", "url": "https://www.belli.ai/feed"}],
    },
    {"name": "Nova", "aliases": [], "description": "ophthalmology AI"},
    {"name": "Twin One", "aliases": ["Twinco"], "description": "first twin"},
    {"name": "Twin Two", "aliases": ["Twinco"], "description": "second twin"},
]

ANTLER = {
    "investor": "Antler",
    "slug": "antler",
    "url": "https://www.antler.co/portfolio",
    "parser": "webflow_paginated",
}
WAVEMAKER = {
    "investor": "Wavemaker Partners",
    "slug": "wavemaker",
    "url": "https://wavemaker.vc/wp-json/wp/v2/wavemaker-portfolio",
    "page_url": "https://wavemaker.vc/portfolio/",
    "parser": "wp_json",
}


@pytest.fixture()
def pl(scripts_module_loader):
    return scripts_module_loader("portfolio_lists")


def _card(name: str, website: str = "https://example.com", description: str = "blurb") -> str:
    return (
        '<div role="listitem" class="w-dyn-item"><div class="portco_card">'
        f'<p fs-cmsfilter-field="name" class="u-text">{name}</p>'
        f'<p fs-cmsfilter-field="description" class="u-text">{description}</p>'
        '<div class="clickable_wrap u-cover-absolute">'
        f'<a target="_blank" href="{website}" class="clickable_link w-inline-block"></a>'
        "</div></div></div>"
    )


def _antler_page(cards: list[str], next_page: int | None) -> str:
    pager = f'<a href="?0b933bfd_page={next_page}">Next</a>' if next_page else ""
    return "<html>" + "".join(cards) + pager + "</html>"


def _fetcher(pages: dict[str, Any]):
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        body = pages[url]
        if isinstance(body, Exception):
            raise body
        if isinstance(body, tuple):
            return body
        return body, {}

    fetch.calls = calls
    return fetch


def _antler_pages(names_page1: list[str], names_page2: list[str]) -> dict[str, str]:
    return {
        ANTLER["url"]: _antler_page([_card(n) for n in names_page1], 2),
        f"{ANTLER['url']}?0b933bfd_page=2": _antler_page([_card(n) for n in names_page2], None),
    }


def test_webflow_paginated_follows_pages_and_parses_cards(pl):
    pages = {
        ANTLER["url"]: _antler_page(
            [_card("Wareflex", "https://www.wareflex.io/en", "B2B &amp; logistics")], 2
        ),
        f"{ANTLER['url']}?0b933bfd_page=2": _antler_page([_card("Other Co")], None),
    }
    entries = pl.parse_webflow_paginated(ANTLER["url"], _fetcher(pages))
    assert entries == [
        {"name": "Wareflex", "website": "https://www.wareflex.io/en", "description": "B2B & logistics"},
        {"name": "Other Co", "website": "https://example.com", "description": "blurb"},
    ]


def test_webflow_company_list_parses_name_site_and_description(pl):
    page = (
        '<div role="listitem" class="company-item w-dyn-item"><a data-w-id="x" href="https://www.udifine.ai/" '
        'target="_blank" class="company-item-wrap w-inline-block"><div class="company-logo-wrap"><img alt="Udifine"/></div>'
        '<div class="company-content"><div class="company-name">Udifine</div>'
        '<div class="company-description">Physical products</div></div></a></div>'
    )
    entries = pl.parse_webflow_company_list("https://www.iterative.vc/companies", _fetcher({"https://www.iterative.vc/companies": page}))
    assert entries == [{"name": "Udifine", "website": "https://www.udifine.ai/", "description": "Physical products"}]


def test_google_alumni_parses_data_label(pl):
    url = "https://startup.google.com/programs/accelerator/southeast-asia/"
    page = (
        '<a class="accelerator-alumni-card glue-card" href="https://www.belli.ai/" aria-label="x" '
        'title="x" data-category="accelerator" data-action="alumni card" data-label="BELLI" target="_blank">'
    )
    assert pl.parse_google_alumni(url, _fetcher({url: page})) == [
        {"name": "BELLI", "website": "https://www.belli.ai/", "description": ""}
    ]


def test_wp_json_pages_until_total_pages(pl):
    base = WAVEMAKER["url"]
    page1 = f"{base}?per_page=100&page=1&_fields=title%2Clink"
    page2 = f"{base}?per_page=100&page=2&_fields=title%2Clink"
    pages = {
        page1: (json.dumps([{"title": {"rendered": "Taidii"}}]), {"x-wp-totalpages": "2"}),
        page2: (json.dumps([{"title": {"rendered": "PQCEE &#038; Co"}}]), {"x-wp-totalpages": "2"}),
    }
    entries = pl.parse_wp_json(base, _fetcher(pages))
    assert [e["name"] for e in entries] == ["Taidii", "PQCEE & Co"]


def test_matching_is_exact_unique_and_guards_weak_names(pl):
    index = pl.build_index(COMPANIES)
    entries = [
        {"name": "Cellivate", "website": "", "description": ""},
        {"name": "Wareflex Logistics", "website": "", "description": ""},
        {"name": "BELLI", "website": "https://www.belli.ai/", "description": ""},
        {"name": "Nova", "website": "https://nova-doc.com/", "description": ""},
        {"name": "Twinco", "website": "", "description": ""},
    ]
    assert list(pl.match_entries(index, entries)) == ["Belli AI", "Cellivate Technologies"]


def test_first_run_seeds_snapshot_without_items(pl, tmp_path: Path):
    fetch = _fetcher(_antler_pages(["Wareflex", "Other"], ["Third"]))
    items_path = tmp_path / "items.json"
    rc = pl.run(COMPANIES, [ANTLER], tmp_path / "snap", items_path, fetcher=fetch, now=NOW)
    assert rc == 0
    snapshot = json.loads((tmp_path / "snap" / "antler.json").read_text())
    assert snapshot == {
        "companies": ["Wareflex"],
        "investor": "Antler",
        "total": 3,
        "url": ANTLER["url"],
    }
    payload = json.loads(items_path.read_text())
    assert payload["items"] == []
    assert payload["investors"]["antler"]["status"] == "seeded"


def test_newly_present_company_emits_item(pl, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "antler.json").write_text(
        json.dumps({"investor": "Antler", "url": ANTLER["url"], "total": 3, "companies": ["Wareflex"]})
    )
    fetch = _fetcher(_antler_pages(["Wareflex", "Cellivate", "Other"], ["Third"]))
    items_path = tmp_path / "items.json"
    rc = pl.run(COMPANIES, [ANTLER], snap_dir, items_path, fetcher=fetch, now=NOW)
    assert rc == 0
    [item] = json.loads(items_path.read_text())["items"]
    assert item["company"] == "Cellivate Technologies"
    assert item["headline"] == "Cellivate Technologies appears in Antler portfolio"
    assert item["link"] == ANTLER["url"]
    assert item["dedup_key"] == "portfolio://antler/cellivate-technologies"
    assert item["source_kind"] == "portfolio_list"
    assert item["pubDate"] == "2026-11-02T13:00:00+00:00"
    assert "blurb" in item["description"]
    snapshot = json.loads((snap_dir / "antler.json").read_text())
    assert snapshot["companies"] == ["Cellivate Technologies", "Wareflex"]
    assert snapshot["total"] == 4


def test_item_link_prefers_page_url(pl, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "wavemaker.json").write_text(
        json.dumps({"investor": "Wavemaker Partners", "url": WAVEMAKER["url"], "total": 1, "companies": []})
    )
    page1 = f"{WAVEMAKER['url']}?per_page=100&page=1&_fields=title%2Clink"
    fetch = _fetcher({page1: (json.dumps([{"title": {"rendered": "Wareflex"}}]), {"x-wp-totalpages": "1"})})
    rc = pl.run(COMPANIES, [WAVEMAKER], snap_dir, tmp_path / "items.json", fetcher=fetch, now=NOW)
    assert rc == 0
    [item] = json.loads((tmp_path / "items.json").read_text())["items"]
    assert item["link"] == "https://wavemaker.vc/portfolio/"
    assert item["dedup_key"] == "portfolio://wavemaker/wareflex"


def test_shrunk_parse_is_skipped_and_exits_nonzero(pl, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    original = {"investor": "Antler", "url": ANTLER["url"], "total": 10, "companies": ["Wareflex"]}
    (snap_dir / "antler.json").write_text(json.dumps(original))
    fetch = _fetcher(_antler_pages(["Cellivate", "Other"], ["Third"]))
    items_path = tmp_path / "items.json"
    rc = pl.run(COMPANIES, [ANTLER], snap_dir, items_path, fetcher=fetch, now=NOW)
    assert rc == 1
    payload = json.loads(items_path.read_text())
    assert payload["items"] == []
    assert payload["failed"] == ["antler"]
    assert json.loads((snap_dir / "antler.json").read_text()) == original


def test_fetch_failure_skips_investor_but_others_run(pl, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    pages = {ANTLER["url"]: error.URLError("boom")}
    page1 = f"{WAVEMAKER['url']}?per_page=100&page=1&_fields=title%2Clink"
    pages[page1] = (json.dumps([{"title": {"rendered": "Wareflex"}}]), {"x-wp-totalpages": "1"})
    rc = pl.run(COMPANIES, [ANTLER, WAVEMAKER], snap_dir, tmp_path / "items.json", fetcher=_fetcher(pages), now=NOW)
    assert rc == 1
    assert not (snap_dir / "antler.json").exists()
    assert (snap_dir / "wavemaker.json").exists()
    payload = json.loads((tmp_path / "items.json").read_text())
    assert payload["investors"]["antler"]["status"] == "fetch_failed"
    assert payload["investors"]["wavemaker"]["status"] == "seeded"


def test_fetch_text_walks_ua_ladder_on_gate(pl, monkeypatch):
    seen_agents: list[str] = []

    class Response:
        headers = {"X-WP-TotalPages": "3"}

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout):
        seen_agents.append(req.get_header("User-agent"))
        if len(seen_agents) == 1:
            raise error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        return Response()

    monkeypatch.setattr(pl.request, "urlopen", fake_urlopen)
    body, headers = pl.fetch_text("https://example.com")
    assert body == "ok"
    assert headers["x-wp-totalpages"] == "3"
    assert seen_agents[0].startswith("feed-agent-portfolio-lists")
    assert len(seen_agents) == 2
