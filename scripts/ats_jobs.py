#!/usr/bin/env python3
"""Provider parsers for the generic `ats_jobs` source kind.

One source kind covers every public, keyless job-board endpoint; the source's
`provider` field picks the parser. The pre-migration kinds `lever_jobs` and
`recruitee_jobs` are still accepted and map to their provider, so a
half-migrated companies.json keeps working.

Each provider turns one response body into normalised postings
`{id, title, team, published, link}` plus the next page URL (if any). Dedup
keys are `<provider>://<board>/<id>`, which keeps the historical
`lever://<slug>/<id>` and `recruitee://<subdomain>/<id>` keys byte-identical.

Pure stdlib; imported by collect-candidates.py, preflight-feeds.py and
collect_metrics.py.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import parse

ATS_KIND = "ats_jobs"
LEGACY_KINDS = {"lever_jobs": "lever", "recruitee_jobs": "recruitee"}
ATS_SOURCE_TYPES = {ATS_KIND, *LEGACY_KINDS}
MAX_PAGES = 5

Posting = dict[str, Any]

JAZZHR_LINK_RE = re.compile(
    r'<a\s+href="(https?://[^"/]+\.applytojob\.com/apply/([A-Za-z0-9]+)/[^"]*)"\s*>\s*(.*?)\s*</a>',
    re.DOTALL,
)


def source_provider(source: dict[str, Any]) -> str | None:
    stype = source.get("type")
    if stype in LEGACY_KINDS:
        return LEGACY_KINDS[stype]
    if stype == ATS_KIND:
        provider = str(source.get("provider") or "").strip().lower()
        return provider if provider in PROVIDERS else None
    return None


def is_ats_source(source: dict[str, Any]) -> bool:
    return source.get("type") in ATS_SOURCE_TYPES


def _host_label(url: str) -> str:
    host = parse.urlparse(url).hostname or ""
    return host.split(".", 1)[0]


def _last_segment(url: str) -> str:
    return parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _segment_after(url: str, marker: str) -> str:
    parts = [p for p in parse.urlparse(url).path.split("/") if p]
    if marker in parts and parts.index(marker) + 1 < len(parts):
        return parts[parts.index(marker) + 1]
    return ""


def _query_value(url: str, key: str) -> str:
    return (parse.parse_qs(parse.urlparse(url).query).get(key) or [""])[0]


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(" UTC"):
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json(body: bytes) -> Any:
    return json.loads(body)


def _require(payload: Any, kind: type, provider: str) -> Any:
    if not isinstance(payload, kind):
        raise ValueError(f"{provider}: unexpected payload shape ({type(payload).__name__})")
    return payload


def _posting(identifier: Any, title: Any, team: Any, published: datetime | None, link: Any) -> Posting:
    return {
        "id": str(identifier),
        "title": str(title or "").strip(),
        "team": str(team or "").strip(),
        "published": published,
        "link": str(link or ""),
    }


def _lever(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    postings = _require(_json(body), list, "lever")
    out = []
    for p in postings:
        created = p.get("createdAt")
        published = (
            datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            if isinstance(created, (int, float))
            else None
        )
        team = (p.get("categories") or {}).get("team")
        out.append(_posting(p.get("id"), p.get("text"), team, published, p.get("hostedUrl")))
    return out, None


def _recruitee(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    offers = _require(_require(_json(body), dict, "recruitee").get("offers"), list, "recruitee")
    out = [
        _posting(
            o.get("id"),
            o.get("title"),
            o.get("department"),
            parse_iso(o.get("published_at")),
            o.get("careers_url"),
        )
        for o in offers
    ]
    return out, None


def _smartrecruiters(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    payload = _require(_json(body), dict, "smartrecruiters")
    content = _require(payload.get("content"), list, "smartrecruiters")
    company = _segment_after(url, "companies")
    out = []
    for p in content:
        identifier = p.get("id")
        team = (p.get("department") or {}).get("label")
        link = f"https://jobs.smartrecruiters.com/{company}/{identifier}"
        out.append(_posting(identifier, p.get("name"), team, parse_iso(p.get("releasedDate")), link))
    offset = int(payload.get("offset") or 0)
    total = int(payload.get("totalFound") or 0)
    next_url = None
    if content and offset + len(content) < total:
        query = dict(parse.parse_qsl(parse.urlparse(url).query))
        query["offset"] = str(offset + len(content))
        next_url = parse.urlparse(url)._replace(query=parse.urlencode(query)).geturl()
    return out, next_url


def _breezy(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    postings = _require(_json(body), list, "breezy")
    out = [
        _posting(
            p.get("id"),
            p.get("name"),
            p.get("department"),
            parse_iso(p.get("published_date")),
            p.get("url"),
        )
        for p in postings
    ]
    return out, None


def _ashby(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    jobs = _require(_require(_json(body), dict, "ashby").get("jobs"), list, "ashby")
    out = [
        _posting(
            j.get("id"),
            j.get("title"),
            j.get("team") or j.get("department"),
            parse_iso(j.get("publishedAt")),
            j.get("jobUrl"),
        )
        for j in jobs
        if j.get("isListed", True)
    ]
    return out, None


def _jazzhr(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    text = body.decode("utf-8", errors="replace")
    if "applytojob.com" not in text:
        raise ValueError("jazzhr: not a JazzHR job board page")
    out: list[Posting] = []
    seen_ids: set[str] = set()
    for link, identifier, raw_title in JAZZHR_LINK_RE.findall(text):
        if identifier in seen_ids:
            continue
        seen_ids.add(identifier)
        title = html.unescape(re.sub(r"<[^>]+>", " ", raw_title))
        out.append(_posting(identifier, " ".join(title.split()), "", None, link))
    return out, None


def _manatal(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    payload = _require(_json(body), dict, "manatal")
    results = _require(payload.get("results"), list, "manatal")
    slug = _segment_after(url, "c")
    out = [
        _posting(
            r.get("id"),
            r.get("position_name"),
            r.get("organization_name"),
            None,
            f"https://www.careers-page.com/{slug}/job/{r.get('hash')}",
        )
        for r in results
    ]
    return out, payload.get("next") or None


def _mycareersfuture(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    payload = _require(_json(body), dict, "mycareersfuture")
    results = _require(payload.get("results"), list, "mycareersfuture")
    uen = _query_value(url, "uen").upper()
    out = []
    for r in results:
        employer_uens = {
            str((r.get(field) or {}).get("uen") or "").upper()
            for field in ("postedCompany", "hiringCompany")
        }
        if uen and uen not in employer_uens:
            continue
        metadata = r.get("metadata") or {}
        categories = r.get("categories") or []
        team = categories[0].get("category") if categories and isinstance(categories[0], dict) else ""
        out.append(
            _posting(
                r.get("uuid"),
                r.get("title"),
                team,
                parse_iso(metadata.get("newPostingDate")),
                metadata.get("jobDetailsUrl"),
            )
        )
    next_url = ((payload.get("_links") or {}).get("next") or {}).get("href")
    return out, next_url or None


def _jobstreet(body: bytes, url: str) -> tuple[list[Posting], str | None]:
    payload = _require(_json(body), dict, "jobstreet")
    results = _require(payload.get("data"), list, "jobstreet")
    advertiser = _query_value(url, "advertiserid")
    out = []
    for r in results:
        if advertiser and str((r.get("advertiser") or {}).get("id") or "") != advertiser:
            continue
        classifications = r.get("classifications") or []
        classification = (classifications[0].get("classification") or {}) if classifications else {}
        job_id = r.get("id")
        out.append(
            _posting(
                job_id,
                r.get("title"),
                classification.get("description"),
                parse_iso(r.get("listingDate")),
                f"https://sg.jobstreet.com/job/{job_id}",
            )
        )
    page = int(_query_value(url, "page") or 1)
    page_size = int(_query_value(url, "pageSize") or 0)
    total = payload.get("totalCount")
    if page_size and isinstance(total, int) and page * page_size < total and results:
        query = parse.parse_qs(parse.urlparse(url).query)
        query["page"] = [str(page + 1)]
        return out, parse.urlparse(url)._replace(query=parse.urlencode(query, doseq=True)).geturl()
    return out, None


PROVIDERS: dict[str, tuple[Callable[[bytes, str], tuple[list[Posting], str | None]], Callable[[str], str]]] = {
    "lever": (_lever, _last_segment),
    "recruitee": (_recruitee, _host_label),
    "smartrecruiters": (_smartrecruiters, lambda url: _segment_after(url, "companies")),
    "breezy": (_breezy, _host_label),
    "ashby": (_ashby, _last_segment),
    "jazzhr": (_jazzhr, _host_label),
    "manatal": (_manatal, lambda url: _segment_after(url, "c")),
    "mycareersfuture": (_mycareersfuture, lambda url: _query_value(url, "uen").upper()),
    "jobstreet": (_jobstreet, lambda url: _query_value(url, "advertiserid")),
}


def board_slug(provider: str, url: str) -> str:
    return PROVIDERS[provider][1](url)


def dedup_key(provider: str, url: str, posting_id: str) -> str:
    return f"{provider}://{board_slug(provider, url)}/{posting_id}"


def parse_page(provider: str, body: bytes, url: str) -> tuple[list[Posting], str | None]:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown ATS provider {provider!r}")
    return PROVIDERS[provider][0](body, url)


def fetch_postings(
    provider: str,
    url: str,
    fetcher: Callable[[str], bytes],
    first_body: bytes | None = None,
    max_pages: int = MAX_PAGES,
) -> list[Posting]:
    body = first_body if first_body is not None else fetcher(url)
    postings, next_url = parse_page(provider, body, url)
    pages = 1
    while next_url and pages < max_pages:
        more, next_url = parse_page(provider, fetcher(next_url), next_url)
        postings.extend(more)
        pages += 1
    return postings


def count_postings(provider: str, body: bytes, url: str) -> int | None:
    try:
        return len(parse_page(provider, body, url)[0])
    except ValueError:
        return None


def headline(posting: Posting) -> str:
    title, team = posting["title"], posting["team"]
    return f"{title} — {team}" if team else title


def empty_read_state(prior: dict[str, Any] | None, postings: int | None) -> dict[str, Any]:
    prior = prior or {}
    streak = int(prior.get("consecutive_empty", 0) or 0)
    if postings is None:
        state: dict[str, Any] = {"consecutive_empty": streak}
        if "last_postings" in prior:
            state["last_postings"] = prior["last_postings"]
        return state
    return {"last_postings": postings, "consecutive_empty": streak + 1 if postings == 0 else 0}
