#!/usr/bin/env python3
"""Weekly investor portfolio-list snapshot and diff for the watchlist."""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entity_terms import term_is_strong  # noqa: E402
from generate_match_terms import _host, homepage_domains  # noqa: E402
from startupsg_snapshot import normalise_name, slug_value  # noqa: E402
from user_agents import GATED_STATUSES, build_ladder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
REGISTRY_FILE = REPO_ROOT / "data" / "portfolio-lists.json"
SNAPSHOT_DIR = REPO_ROOT / "data" / "portfolio-lists"
ITEMS_FILE = REPO_ROOT / "data" / "portfolio-items.json"

TIMEOUT_SECS = 30
MAX_PAGES = 60
SHRINK_RATIO = 0.5
MIN_KEY_LEN = 3

UA_LADDER = build_ladder("portfolio-lists")

Fetcher = Callable[[str], tuple[str, dict[str, str]]]


def fetch_text(url: str) -> tuple[str, dict[str, str]]:
    last_error: Exception | None = None
    for user_agent in UA_LADDER:
        req = request.Request(url, headers={"User-Agent": user_agent})
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.read().decode("utf-8", errors="replace"), headers
        except error.HTTPError as e:
            if e.code not in GATED_STATUSES:
                raise
            last_error = e
    assert last_error is not None
    raise last_error


def _clean(text: str) -> str:
    return " ".join(html.unescape(text or "").split())


def _entry(name: str, website: str = "", description: str = "") -> dict[str, str]:
    return {"name": _clean(name), "website": website.strip(), "description": _clean(description)}


WEBFLOW_PAGE_PARAM_RE = re.compile(r"\?([0-9a-f]{6,})_page=(\d+)")
ANTLER_NAME_RE = re.compile(r'fs-cmsfilter-field="name"[^>]*>([^<]*)<')
ANTLER_DESC_RE = re.compile(r'fs-cmsfilter-field="description"[^>]*>([^<]*)<')
ANTLER_LINK_RE = re.compile(r'<a target="_blank" href="([^"]*)" class="clickable_link')


def parse_portco_cards(page: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for card in re.finditer(r'<div class="portco_card">', page):
        end = page.find('<div class="portco_card">', card.end())
        chunk = page[card.start() : end if end != -1 else len(page)]
        name = ANTLER_NAME_RE.search(chunk)
        if not name or not _clean(name.group(1)):
            continue
        description = ANTLER_DESC_RE.search(chunk)
        link = ANTLER_LINK_RE.search(chunk)
        entries.append(
            _entry(
                name.group(1),
                link.group(1) if link else "",
                description.group(1) if description else "",
            )
        )
    return entries


def _webflow_next_page(page: str, current: int) -> str | None:
    for match in WEBFLOW_PAGE_PARAM_RE.finditer(page):
        if int(match.group(2)) == current + 1:
            return f"{match.group(1)}_page={current + 1}"
    return None


def parse_webflow_paginated(url: str, fetcher: Fetcher) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    page_url = url
    for page_number in range(1, MAX_PAGES + 1):
        body, _headers = fetcher(page_url)
        entries.extend(parse_portco_cards(body))
        next_query = _webflow_next_page(body, page_number)
        if not next_query:
            break
        page_url = f"{url.split('?', 1)[0]}?{next_query}"
    return entries


ITERATIVE_ITEM_RE = re.compile(
    r'<a[^>]*href="([^"]*)"[^>]*class="company-item-wrap[^"]*"[^>]*>.*?'
    r'<div class="company-name">([^<]*)</div>'
    r'(?:<div class="company-description">([^<]*)</div>)?',
    re.DOTALL,
)


def parse_webflow_company_list(url: str, fetcher: Fetcher) -> list[dict[str, str]]:
    body, _headers = fetcher(url)
    return [
        _entry(name, website, description or "")
        for website, name, description in ITERATIVE_ITEM_RE.findall(body)
        if _clean(name)
    ]


GOOGLE_ALUMNI_RE = re.compile(
    r'<a class="accelerator-alumni-card[^"]*" href="([^"]*)"[^>]*'
    r'data-action="alumni card" data-label="([^"]*)"'
)


def parse_google_alumni(url: str, fetcher: Fetcher) -> list[dict[str, str]]:
    body, _headers = fetcher(url)
    return [_entry(name, website) for website, name in GOOGLE_ALUMNI_RE.findall(body) if _clean(name)]


def parse_wp_json(url: str, fetcher: Fetcher) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    separator = "&" if "?" in url else "?"
    total_pages = 1
    page_number = 1
    while page_number <= min(total_pages, MAX_PAGES):
        query = parse.urlencode({"per_page": 100, "page": page_number, "_fields": "title,link"})
        body, headers = fetcher(f"{url}{separator}{query}")
        posts = json.loads(body)
        if not isinstance(posts, list):
            raise ValueError("wp-json response is not a list")
        for post in posts:
            title = (post.get("title") or {}).get("rendered") if isinstance(post, dict) else ""
            if title and _clean(title):
                entries.append(_entry(title))
        total_pages = int(headers.get("x-wp-totalpages") or 1)
        page_number += 1
    return entries


PARSERS: dict[str, Callable[[str, Fetcher], list[dict[str, str]]]] = {
    "webflow_paginated": parse_webflow_paginated,
    "webflow_company_list": parse_webflow_company_list,
    "google_alumni": parse_google_alumni,
    "wp_json": parse_wp_json,
}


def _name_keys(company: dict[str, Any]) -> dict[str, bool]:
    keys: dict[str, bool] = {}
    for name in [company.get("name") or "", *(company.get("aliases") or [])]:
        key = normalise_name(name)
        if len(key) < MIN_KEY_LEN:
            continue
        keys[key] = keys.get(key, False) or term_is_strong(name)
    return keys


def build_index(companies: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], bool]]:
    owners: dict[str, list[tuple[dict[str, Any], bool]]] = {}
    for company in companies:
        for key, strong in _name_keys(company).items():
            owners.setdefault(key, []).append((company, strong))
    return {key: hits[0] for key, hits in owners.items() if len(hits) == 1}


def match_entries(
    index: dict[str, tuple[dict[str, Any], bool]], entries: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    matched: dict[str, dict[str, str]] = {}
    for entry in entries:
        hit = index.get(normalise_name(entry["name"]))
        if hit is None:
            continue
        company, strong = hit
        if not strong:
            host = _host(entry["website"]) if entry["website"] else ""
            if not host or host not in homepage_domains(company):
                continue
        matched.setdefault(company["name"], entry)
    return dict(sorted(matched.items()))


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"portfolio_lists: unreadable snapshot {path}: {e}", file=sys.stderr)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
        return None
    return data


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def make_item(investor: dict[str, str], company: str, entry: dict[str, str], now: str) -> dict[str, Any]:
    details = [f"{entry['name']} is listed in the {investor['investor']} portfolio."]
    if entry["description"]:
        details.append(f"Portfolio blurb: {entry['description']}")
    if entry["website"]:
        details.append(f"Website: {entry['website']}")
    details.append(
        "It was not on this list last week, which may point to an undisclosed round or programme intake."
    )
    return {
        "company": company,
        "headline": f"{company} appears in {investor['investor']} portfolio",
        "description": " ".join(details),
        "source": investor["investor"],
        "pubDate": now,
        "link": investor.get("page_url") or investor["url"],
        "dedup_key": f"portfolio://{investor['slug']}/{slug_value(company)}",
        "source_kind": "portfolio_list",
    }


def process_investor(
    investor: dict[str, str],
    index: dict[str, tuple[dict[str, Any], bool]],
    snapshot_dir: Path,
    fetcher: Fetcher,
    now: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    slug = investor["slug"]
    parser = PARSERS.get(investor.get("parser", ""))
    if parser is None:
        return [], {"status": "unknown_parser", "parser": investor.get("parser")}
    try:
        entries = parser(investor["url"], fetcher)
    except (error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
        print(f"portfolio_lists: {slug} fetch/parse failed: {e}", file=sys.stderr)
        return [], {"status": "fetch_failed", "error": str(e)[:200]}

    total = len({normalise_name(e["name"]) for e in entries if normalise_name(e["name"])})
    snapshot_path = snapshot_dir / f"{slug}.json"
    previous = load_snapshot(snapshot_path)
    previous_total = int(previous.get("total") or 0) if previous else 0
    if total == 0 or (previous_total and total < previous_total * SHRINK_RATIO):
        print(
            f"portfolio_lists: {slug} parsed {total} names (previous {previous_total}); skipping",
            file=sys.stderr,
        )
        return [], {"status": "shrunk", "total": total, "previous_total": previous_total}

    matched = match_entries(index, entries)
    items: list[dict[str, Any]] = []
    if previous is not None:
        before = set(previous["companies"])
        items = [make_item(investor, name, entry, now) for name, entry in matched.items() if name not in before]
    write_json(
        snapshot_path,
        {
            "investor": investor["investor"],
            "url": investor["url"],
            "total": total,
            "companies": sorted(matched),
        },
    )
    status = "ok" if previous is not None else "seeded"
    return items, {"status": status, "total": total, "matched": len(matched), "items": len(items)}


def run(
    companies: list[dict[str, Any]],
    investors: list[dict[str, str]],
    snapshot_dir: Path,
    items_path: Path,
    fetcher: Fetcher = fetch_text,
    now: datetime | None = None,
) -> int:
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    index = build_index(companies)
    items: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    for investor in investors:
        investor_items, status = process_investor(investor, index, snapshot_dir, fetcher, stamp)
        items.extend(investor_items)
        report[investor["slug"]] = status
        print(f"portfolio_lists: {investor['slug']} {status}", file=sys.stderr)

    failed = sorted(slug for slug, s in report.items() if s["status"] not in {"ok", "seeded"})
    write_json(items_path, {"generated_at": stamp, "items": items, "investors": report, "failed": failed})
    if failed:
        print(
            f"::warning::portfolio_lists: skipped {', '.join(failed)} (fetch failure or markup change)",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    investors = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return run(companies, investors, SNAPSHOT_DIR, ITEMS_FILE)


if __name__ == "__main__":
    sys.exit(main())
