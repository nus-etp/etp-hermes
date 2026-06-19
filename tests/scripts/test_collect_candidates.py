"""Unit tests for scripts/collect-candidates.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib import error

import pytest


@pytest.fixture()
def cc(scripts_module_loader):
    return scripts_module_loader("collect-candidates")


def test_window_days_env_override(cc, monkeypatch) -> None:
    # Default when unset, env override when set, default again on a bad value.
    monkeypatch.delenv("COLLECT_FIREHOSE_DAYS", raising=False)
    assert cc._window_days("COLLECT_FIREHOSE_DAYS", 7).days == 7
    monkeypatch.setenv("COLLECT_FIREHOSE_DAYS", "90")
    assert cc._window_days("COLLECT_FIREHOSE_DAYS", 7).days == 90
    monkeypatch.setenv("COLLECT_FIREHOSE_DAYS", "not-a-number")
    assert cc._window_days("COLLECT_FIREHOSE_DAYS", 7).days == 7


def _rss(items: list[tuple[str, str, str, str]]) -> bytes:
    """items: (title, link, description, pubDate)."""
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link>"
        f"<description>{d}</description><pubDate>{p}</pubDate></item>"
        for t, l, d, p in items
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def _atom(entries: list[dict[str, str]]) -> bytes:
    body = "".join(
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        f"<id>{e['id']}</id><title>{e['title']}</title>"
        f'<link rel="alternate" href="{e["link"]}"/>'
        f"<published>{e['published']}</published>"
        f"<author><name>{e.get('author', 'someone')}</name></author>"
        f"<content>{e.get('content', '')}</content>"
        "</entry>"
        for e in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


def _recent_rfc822(days_ago: int = 0) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _recent_iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _fetcher(responses: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        if url not in responses:
            raise error.URLError(f"unexpected fetch {url}")
        return responses[url]

    return fetch


COMPANIES = [
    {
        "name": "Acme Robotics",
        "aliases": ["Acme"],
        "description": "SG robotics startup",
        "sources": [],
    },
    {
        "name": "Nova Health",
        "aliases": [],
        "description": "healthtech",
        "sources": [
            {"type": "rss", "label": "press", "url": "https://nova.example/feed"},
            {
                "type": "html_scrape",
                "label": "newsroom",
                "url": "https://nova.example/news",
                "hint": "cards",
            },
        ],
    },
    {
        "name": "Patsnap",
        "aliases": [],
        "description": "IP analytics",
        "sources": [
            {
                "type": "lever_jobs",
                "label": "careers",
                "url": "https://api.lever.co/v0/postings/patsnap?mode=json",
            },
            {
                "type": "github_org",
                "label": "github",
                "url": "https://github.com/patsnap.atom",
            },
        ],
    },
]

FEEDS = [{"name": "Tech Feed", "type": "firehose", "url": "https://firehose.example/feed"}]


def test_firehose_triage_and_seen_dedup(cc):
    feed = _rss(
        [
            ("Acme Robotics raises $10M", "https://x.example/a", "funding", _recent_rfc822(1)),
            ("Acme launches arm", "https://x.example/seen", "already seen", _recent_rfc822(1)),
            ("Unrelated story", "https://x.example/b", "nothing", _recent_rfc822(1)),
            ("Old Acme story", "https://x.example/old", "stale", _recent_rfc822(30)),
        ]
    )
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [FEEDS[0]["url"]], "per_company": {}},
        jina=None,
        seen={"https://x.example/seen"},
        fetcher=_fetcher({FEEDS[0]["url"]: feed}),
    )
    links = [c["link"] for c in out["candidates"]]
    assert links == ["https://x.example/a"]
    assert out["candidates"][0]["company"] == "Acme Robotics"
    assert out["candidates"][0]["source_kind"] == "firehose"
    # companies map only carries matched companies
    assert set(out["companies"]) == {"Acme Robotics"}


def test_firehose_skips_unchanged_feeds(cc):
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),  # any fetch would raise
    )
    assert out["candidates"] == []


def test_per_company_rss_skips_triage(cc):
    feed = _rss([("Quarterly update", "https://nova.example/post", "", _recent_rfc822(2))])
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Nova Health": ["https://nova.example/feed"]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({"https://nova.example/feed": feed}),
    )
    [cand] = out["candidates"]
    # headline has no company term, but per-company sources bind regardless
    assert cand["company"] == "Nova Health"
    assert cand["source"] == "Nova Health · press"
    assert cand["source_kind"] == "rss"


def test_lever_jobs_window_and_key(cc):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    old_ms = now_ms - 40 * 24 * 3600 * 1000
    postings = json.dumps(
        [
            {
                "id": "abc",
                "text": "ML Engineer",
                "categories": {"team": "AI"},
                "hostedUrl": "https://jobs.lever.co/patsnap/abc",
                "createdAt": now_ms,
            },
            {
                "id": "stale",
                "text": "Old role",
                "categories": {},
                "hostedUrl": "https://jobs.lever.co/patsnap/stale",
                "createdAt": old_ms,
            },
        ]
    ).encode()
    url = "https://api.lever.co/v0/postings/patsnap?mode=json"
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Patsnap": [url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({url: postings}),
    )
    [cand] = out["candidates"]
    assert cand["dedup_key"] == "lever://patsnap/abc"
    assert cand["headline"] == "ML Engineer — AI"


def test_github_org_bot_and_chore_filter(cc):
    feed = _atom(
        [
            {
                "id": "tag:github.com,2008:1",
                "title": "someone published a release v1.2 of patsnap/tool",
                "link": "https://github.com/patsnap/tool/releases/v1.2",
                "published": _recent_iso(1),
            },
            {
                "id": "tag:github.com,2008:2",
                "title": "dependabot[bot] pushed to main in patsnap/tool",
                "link": "https://github.com/patsnap/tool",
                "published": _recent_iso(1),
                "author": "dependabot[bot]",
            },
            {
                "id": "tag:github.com,2008:3",
                "title": "someone pushed to main in patsnap/tool",
                "link": "https://github.com/patsnap/tool",
                "published": _recent_iso(1),
                "content": "Bump lodash from 1.0 to 1.1",
            },
        ]
    )
    url = "https://github.com/patsnap.atom"
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Patsnap": [url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({url: feed}),
    )
    [cand] = out["candidates"]
    assert cand["dedup_key"] == "tag:github.com,2008:1"
    assert cand["source_kind"] == "github_org"


def test_html_scrape_uses_jina_items(cc):
    url = "https://nova.example/news"
    jina = {
        "per_company": {
            "Nova Health": {
                url: [
                    {
                        "headline": "Nova opens KL office",
                        "link": "https://nova.example/kl",
                        "source_kind": "html_scrape",
                        "pre_extracted": True,
                    },
                    {
                        "headline": "Seen already",
                        "link": "https://nova.example/seen",
                        "source_kind": "html_scrape",
                        "pre_extracted": True,
                    },
                ]
            }
        },
        "extraction_failed": [],
        "deferred": [],
    }
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Nova Health": [url]}},
        jina=jina,
        seen={"https://nova.example/seen"},
        fetcher=_fetcher({}),
    )
    [cand] = out["candidates"]
    assert cand["link"] == "https://nova.example/kl"
    assert cand["pre_extracted"] is True
    assert out["llm_fetch_required"] == []


def test_html_scrape_without_jina_goes_to_llm_list(cc):
    url = "https://nova.example/news"
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Nova Health": [url]}},
        jina={"per_company": {}, "extraction_failed": [url], "deferred": []},
        seen=set(),
        fetcher=_fetcher({}),
    )
    assert out["candidates"] == []
    [entry] = out["llm_fetch_required"]
    assert entry == {
        "company": "Nova Health",
        "url": url,
        "label": "newsroom",
        "hint": "cards",
    }


def test_fetch_failure_recorded_fail_open(cc):
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={
            "firehose": [FEEDS[0]["url"]],
            "per_company": {"Nova Health": ["https://nova.example/feed"]},
        },
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),  # everything raises URLError
    )
    kinds = {(f["kind"], f["url"]) for f in out["fetch_failed"]}
    assert kinds == {
        ("firehose", FEEDS[0]["url"]),
        ("rss", "https://nova.example/feed"),
    }
    assert out["candidates"] == []


def test_missing_changed_file_means_cold_start(cc):
    feed = _rss([("Acme Robotics ships", "https://x.example/c", "", _recent_rfc822(0))])
    nova = _rss([("Post", "https://nova.example/p", "", _recent_rfc822(0))])
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed=None,
        jina=None,
        seen=set(),
        fetcher=_fetcher(
            {
                FEEDS[0]["url"]: feed,
                "https://nova.example/feed": nova,
                "https://api.lever.co/v0/postings/patsnap?mode=json": b"[]",
                "https://github.com/patsnap.atom": _atom([]),
            }
        ),
    )
    assert {c["company"] for c in out["candidates"]} == {"Acme Robotics", "Nova Health"}
    # html_scrape with no jina file falls back to the LLM path on cold start
    assert [e["url"] for e in out["llm_fetch_required"]] == ["https://nova.example/news"]


def test_undated_items_pass_window(cc):
    feed = _rss([("Acme Robotics note", "https://x.example/undated", "", "")])
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [FEEDS[0]["url"]], "per_company": {}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({FEEDS[0]["url"]: feed}),
    )
    assert [c["link"] for c in out["candidates"]] == ["https://x.example/undated"]


# --- r.jina.ai dead-feed fallback ---------------------------------------------


def _reader(responses: dict[str, str], status: int = 200):
    """Fake fetch_reader: url -> markdown. Missing url raises URLError."""

    def read(url: str, api_key: str | None = None):
        if url not in responses:
            raise error.URLError(f"unexpected reader {url}")
        return status, responses[url]

    return read


def _recent_iso_date(days_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def test_jina_fallback_recovers_unreachable_firehose(cc):
    md = (
        "# Tech Feed\n\n"
        f"### [Acme Robotics raises $5M](https://j.example/win)\n\n{_recent_iso_date(1)}\n\n"
        f"### [Totally unrelated thing](https://j.example/misc)\n\n{_recent_iso_date(1)}\n"
    )
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [FEEDS[0]["url"]], "per_company": {}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),  # direct fetch fails → fallback kicks in
        reader=_reader({FEEDS[0]["url"]: md}),
    )
    # Only the term-matching item survives firehose triage.
    assert [c["link"] for c in out["candidates"]] == ["https://j.example/win"]
    cand = out["candidates"][0]
    assert cand["company"] == "Acme Robotics"
    assert cand["source_kind"] == "firehose"
    assert cand["via_jina_fallback"] is True
    # Recovered, so NOT recorded as a failure.
    assert out["fetch_failed"] == []
    assert out["jina_recovered"] == [FEEDS[0]["url"]]
    assert out["stats"]["jina_candidates"] == 1


def test_jina_fallback_recovers_per_company_rss(cc):
    url = "https://nova.example/feed"
    md = f"## [Nova quarterly update](https://nova.example/q3)\n\n{_recent_iso_date(2)}\n"
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Nova Health": [url]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),
        reader=_reader({url: md}),
    )
    [cand] = out["candidates"]
    assert cand["company"] == "Nova Health"  # per-company binds without triage
    assert cand["source"] == "Nova Health · press"
    assert cand["source_kind"] == "rss"
    assert cand["via_jina_fallback"] is True
    assert out["fetch_failed"] == []


def test_jina_fallback_disabled_by_default_keeps_fetch_failed(cc):
    # No reader passed → fallback off → identical to the legacy fail-open path.
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [FEEDS[0]["url"]], "per_company": {}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),
    )
    assert [f["url"] for f in out["fetch_failed"]] == [FEEDS[0]["url"]]
    assert out["jina_recovered"] == []


def test_jina_fallback_budget_cap_falls_through_to_fetch_failed(cc):
    md = f"### [Acme Robotics wins](https://j.example/x)\n\n{_recent_iso_date(1)}\n"
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [FEEDS[0]["url"]], "per_company": {}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),
        reader=_reader({FEEDS[0]["url"]: md}),
        jina_budget=0,  # exhausted before the first call
    )
    assert out["candidates"] == []
    assert [f["url"] for f in out["fetch_failed"]] == [FEEDS[0]["url"]]


def test_jina_fallback_skips_github_org(cc):
    # github_org isn't Markdown-recoverable → stays in fetch_failed even with a reader.
    out = cc.collect(
        COMPANIES,
        FEEDS,
        changed={"firehose": [], "per_company": {"Patsnap": ["https://github.com/patsnap.atom"]}},
        jina=None,
        seen=set(),
        fetcher=_fetcher({}),
        reader=_reader({"https://github.com/patsnap.atom": "### [r](https://x)\n2026-06-17\n"}),
    )
    assert [f["kind"] for f in out["fetch_failed"]] == ["github_org"]
    assert out["jina_recovered"] == []
