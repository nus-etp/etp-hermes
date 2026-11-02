#!/usr/bin/env python3
"""Weekly MAS Financial Institutions Directory snapshot and diff for the watchlist.

Discovers every licence category from the directory's own home page (new
categories are picked up automatically), pages each category through the
keyless server-rendered resultpartial endpoint, matches watchlist companies by
exact normalised name or alias (unique on both sides, weak names need the
register's website to be the company's own domain), fetches the detail page only for
matched institutions, and writes the committed snapshot
data/masfid/watchlist-licences.json. A licence newly granted, a changed
activity set or a revoked licence becomes an item in data/masfid-items.json,
which collect-candidates.py folds into data/candidates.json.

The first run (no previous snapshot) seeds the snapshot and emits no items.
Fails open: any failed listing page or detail fetch leaves the snapshot
untouched and writes no items; exit 1 only when every request failed.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
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
SNAPSHOT_FILE = REPO_ROOT / "data" / "masfid" / "watchlist-licences.json"
ITEMS_FILE = REPO_ROOT / "data" / "masfid-items.json"

BASE_URL = "https://eservices.mas.gov.sg"
HOME_URL = f"{BASE_URL}/fid"
LIST_URL = f"{BASE_URL}/fid/custom/resultpartial"
DETAIL_URL = f"{BASE_URL}/fid/institution/detail/{{slug}}"
PAGE_SIZE = 100
MAX_PAGES = 30
PACE_SECS = 1.0
TIMEOUT_SECS = 30
MIN_KEY_LEN = 3
SOURCE_LABEL = "MAS Financial Institutions Directory"

UA_LADDER = build_ladder("mas-fid")

CATEGORY_LINK_RE = re.compile(r'href="/fid/institution\?category=([^"&]+)"')
ENTRY_RE = re.compile(
    r'<div class="inner">\s*<a href="/fid/institution/detail/([^"]+)">\s*'
    r'<h3[^>]*>([^<]*)</h3>(.*?)(?=<div class="inner">|\Z)',
    re.DOTALL,
)
WEBSITE_RE = re.compile(r'<a class="font-resize" target="_blank" href="([^"]+)"')
TOTAL_RE = re.compile(r'data-total="(\d+)"')
LICENCE_SPLIT_RE = re.compile(r'<h3 class="header-inner-2 toggle">')
ACTIVITY_RE = re.compile(r'<h4[^>]*>(.*?)</h4>|<li>(.*?)</li>', re.DOTALL)

Fetcher = Callable[[str], str]


def fetch_text(url: str) -> str:
    last_error: Exception | None = None
    for user_agent in UA_LADDER:
        req = request.Request(url, headers={"User-Agent": user_agent})
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as e:
            if e.code not in GATED_STATUSES:
                raise
            last_error = e
    assert last_error is not None
    raise last_error


def _clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def discover_categories(home_page: str) -> list[str]:
    seen: dict[str, None] = {}
    for raw in CATEGORY_LINK_RE.findall(home_page):
        name = parse.unquote(raw).strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


def list_url(category: str, page: int) -> str:
    query = parse.urlencode({"category": category, "page": page, "count": PAGE_SIZE})
    return f"{LIST_URL}?{query}"


def parse_listing(page: str) -> tuple[list[dict[str, str]], int]:
    entries = [
        {
            "slug": slug,
            "name": _clean(name),
            "website": (WEBSITE_RE.search(rest).group(1) if WEBSITE_RE.search(rest) else ""),
        }
        for slug, name, rest in ENTRY_RE.findall(page)
    ]
    total = TOTAL_RE.search(page)
    return entries, int(total.group(1)) if total else 1


def crawl_directory(
    fetcher: Fetcher, sleep: Callable[[float], None]
) -> tuple[dict[str, dict[str, Any]], int, int]:
    ok = 0
    failed = 0
    try:
        categories = discover_categories(fetcher(HOME_URL))
        ok += 1
    except (error.URLError, TimeoutError, OSError):
        return {}, 0, 1
    if not categories:
        return {}, ok, 1

    institutions: dict[str, dict[str, Any]] = {}
    for category in categories:
        page = 1
        while page <= MAX_PAGES:
            sleep(PACE_SECS)
            try:
                entries, total = parse_listing(fetcher(list_url(category, page)))
            except (error.URLError, TimeoutError, OSError) as e:
                failed += 1
                print(f"mas_fid: {category} page {page} failed: {e}", file=sys.stderr)
                break
            ok += 1
            for entry in entries:
                record = institutions.setdefault(
                    entry["slug"], {"name": entry["name"], "website": "", "categories": []}
                )
                record["website"] = record["website"] or entry["website"]
                if category not in record["categories"]:
                    record["categories"].append(category)
            if page >= total or not entries:
                break
            page += 1
    return institutions, ok, failed


def parse_detail(page: str) -> dict[str, list[str]]:
    start = page.find('<h2 class="header">Licence Type/Status</h2>')
    end = page.find('<div class="key-personnel">')
    if start < 0:
        return {}
    block = page[start : end if end > start else len(page)]
    licences: dict[str, list[str]] = {}
    for chunk in LICENCE_SPLIT_RE.split(block)[1:]:
        title, _, body = chunk.partition("</h3>")
        activities: list[str] = []
        for heading, item in ACTIVITY_RE.findall(body):
            text = _clean(heading or item)
            if text:
                activities.append(text)
        licences[_clean(title)] = sorted(set(activities))
    return licences


def _company_keys(company: dict[str, Any]) -> dict[str, bool]:
    keys: dict[str, bool] = {}
    for name in [company.get("name") or "", *(company.get("aliases") or [])]:
        key = normalise_name(name)
        if len(key) < MIN_KEY_LEN:
            continue
        keys[key] = keys.get(key, False) or term_is_strong(name)
    return keys


def build_index(
    companies: list[dict[str, Any]],
) -> dict[str, tuple[dict[str, Any], bool]]:
    owners: dict[str, list[tuple[dict[str, Any], bool]]] = {}
    for company in companies:
        for key, strong in _company_keys(company).items():
            owners.setdefault(key, []).append((company, strong))
    return {key: hits[0] for key, hits in owners.items() if len(hits) == 1}


def match_institutions(
    companies: list[dict[str, Any]], institutions: dict[str, dict[str, Any]]
) -> dict[str, str]:
    index = build_index(companies)
    by_key: dict[str, list[str]] = {}
    for slug, record in institutions.items():
        by_key.setdefault(normalise_name(record["name"]), []).append(slug)

    matched: dict[str, str] = {}
    claims: dict[str, list[str]] = {}
    for key, slugs in by_key.items():
        hit = index.get(key)
        if hit is None or len(slugs) != 1:
            continue
        company, strong = hit
        record = institutions[slugs[0]]
        host = _host(record["website"]) if record["website"] else ""
        own_domain = bool(host) and host in homepage_domains(company)
        if not strong and not own_domain:
            continue
        claims.setdefault(company["name"], []).append(slugs[0])
    for name, slugs in claims.items():
        if len(slugs) == 1:
            matched[name] = slugs[0]
    return dict(sorted(matched.items()))


def build_records(
    matched: dict[str, str],
    institutions: dict[str, dict[str, Any]],
    fetcher: Fetcher,
    sleep: Callable[[float], None],
) -> tuple[dict[str, dict[str, Any]], int, int]:
    records: dict[str, dict[str, Any]] = {}
    ok = 0
    failed = 0
    for name, slug in matched.items():
        institution = institutions[slug]
        sleep(PACE_SECS)
        try:
            detail = parse_detail(fetcher(DETAIL_URL.format(slug=slug)))
        except (error.URLError, TimeoutError, OSError) as e:
            failed += 1
            print(f"mas_fid: detail {slug} failed: {e}", file=sys.stderr)
            continue
        ok += 1
        licences = {category: detail.get(category, []) for category in institution["categories"]}
        for category, activities in detail.items():
            licences.setdefault(category, activities)
        records[name] = {
            "register_name": institution["name"],
            "slug": slug,
            "url": DETAIL_URL.format(slug=slug),
            "licences": dict(sorted(licences.items())),
        }
    return records, ok, failed


def _digest(activities: list[str]) -> str:
    return hashlib.sha1("|".join(activities).encode("utf-8")).hexdigest()[:8]


def _item(
    company: str, record: dict[str, Any], category: str, action: str, headline: str, detail: str, now: str
) -> dict[str, Any]:
    return {
        "company": company,
        "headline": headline,
        "description": detail,
        "source": SOURCE_LABEL,
        "pubDate": now,
        "link": record["url"],
        "dedup_key": f"masfid://{slug_value(company)}/{slug_value(category)}/{action}",
        "source_kind": "mas_fid",
    }


def _activity_text(activities: list[str]) -> str:
    return "; ".join(activities) if activities else "no activities listed"


def diff_items(
    previous: dict[str, dict[str, Any]] | None,
    current: dict[str, dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    if previous is None:
        return []
    items: list[dict[str, Any]] = []
    for company, record in current.items():
        before = (previous.get(company) or {}).get("licences") or {}
        for category, activities in record["licences"].items():
            if category not in before:
                items.append(
                    _item(
                        company,
                        record,
                        category,
                        "granted",
                        f"{company} newly listed in the MAS directory as {category}",
                        f"Register entry {record['register_name']}. Activities: {_activity_text(activities)}.",
                        now,
                    )
                )
            elif activities != before[category]:
                items.append(
                    _item(
                        company,
                        record,
                        category,
                        f"changed-{_digest(activities)}",
                        f"{company} {category} licence activities changed in the MAS directory",
                        f"Now: {_activity_text(activities)}. Was: {_activity_text(before[category])}.",
                        now,
                    )
                )
    for company, record in previous.items():
        now_licences = (current.get(company) or {}).get("licences") or {}
        for category in (record.get("licences") or {}):
            if category not in now_licences:
                items.append(
                    _item(
                        company,
                        record,
                        category,
                        "revoked",
                        f"{company} no longer listed in the MAS directory as {category}",
                        f"Register entry {record.get('register_name', company)} dropped this licence category.",
                        now,
                    )
                )
    return items


def load_snapshot(path: Path) -> dict[str, dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"mas_fid: unreadable snapshot {path}: {e}", file=sys.stderr)
        return None
    companies = data.get("companies") if isinstance(data, dict) else None
    return companies if isinstance(companies, dict) else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def run(
    companies: list[dict[str, Any]],
    snapshot_path: Path,
    items_path: Path,
    fetcher: Fetcher = fetch_text,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    institutions, list_ok, list_failed = crawl_directory(fetcher, sleep)
    matched = match_institutions(companies, institutions) if list_failed == 0 else {}
    records: dict[str, dict[str, Any]] = {}
    detail_ok = detail_failed = 0
    if list_failed == 0 and list_ok > 0:
        records, detail_ok, detail_failed = build_records(matched, institutions, fetcher, sleep)

    complete = list_ok > 1 and list_failed == 0 and detail_failed == 0
    all_failed = list_ok == 0 or (list_ok == 1 and list_failed > 0)
    items: list[dict[str, Any]] = []
    if complete:
        items = diff_items(load_snapshot(snapshot_path), records, stamp)
        write_json(snapshot_path, {"source": HOME_URL, "companies": records})

    write_json(
        items_path,
        {
            "generated_at": stamp,
            "items": items,
            "institutions_listed": len(institutions),
            "watchlist_matched": len(matched),
            "list_pages_ok": list_ok,
            "list_pages_failed": list_failed,
            "detail_failed": detail_failed,
            "snapshot_updated": complete,
        },
    )
    print(
        f"mas_fid: institutions={len(institutions)} pages_ok={list_ok} pages_failed={list_failed} "
        f"matched={len(matched)} detail_failed={detail_failed} items={len(items)} "
        f"snapshot_updated={complete}",
        file=sys.stderr,
    )
    if all_failed:
        print("::warning::mas_fid: every MAS directory fetch failed", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    return run(companies, SNAPSHOT_FILE, ITEMS_FILE)


if __name__ == "__main__":
    sys.exit(main())
