#!/usr/bin/env python3
"""Daily sitemap lastmod diff for watchlist company websites.

For every active watchlist company with its own domain (harvested from its
sources[] / identifiers URLs by generate_match_terms.homepage_domains), the
script discovers the site's sitemap (robots.txt Sitemap: lines, else
/sitemap.xml then /sitemap_index.xml), follows sitemap indexes to a bounded
depth and fetch count, and records {page_url: lastmod} after dropping noise
(listing/tag/category/author/pagination pages, legal pages, assets, the
homepage, locale duplicates of the same path, pages on other hosts). Child
sitemaps for images, videos, products, tags, categories, authors and
collections are never fetched.

State lives in data/sitemap-cache.json (gitignored, carried across runs by the
GHA sitemap-cache-* key). Each page that is NEW against the previous state, or
whose lastmod moved forward, becomes a candidate-like item in
data/sitemap-items.json that collect-candidates.py folds into
data/candidates.json for the normal Layer 1 relevance pass (not
pre_extracted). A domain with no previous state is seeded only, so a cache loss
re-seeds instead of flooding. Items must carry a lastmod inside --window-days
(a newly listed page with no lastmod at all is allowed), are capped per domain
per run, and a domain whose changes exceed BULK_MIN_CHANGES and BULK_RATIO of
its known pages (a regenerated sitemap or a URL-scheme migration) emits none.

Fails open per domain: a network error or a missing sitemap carries the
previous state forward; a partial crawl (a failed or skipped child sitemap,
URL cap hit) merges into the previous state so unvisited pages do not
reappear as new tomorrow. Exits 1 only when every attempted domain was
unreachable (min ALARM_MIN_ATTEMPTS), so the continue-on-error step shows red.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_match_terms import homepage_domains  # noqa: E402
from user_agents import GATED_STATUSES, build_ladder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
STATE_FILE = REPO_ROOT / "data" / "sitemap-cache.json"
ITEMS_FILE = REPO_ROOT / "data" / "sitemap-items.json"
CHANGED_FILE = REPO_ROOT / "data" / "changed-sources.json"

TIMEOUT_SECS = 20
REQUEST_INTERVAL_SECS = 1.0
MAX_BODY_BYTES = 20 * 1024 * 1024
MAX_SITEMAP_FETCHES = 15
MAX_INDEX_DEPTH = 2
MAX_ROBOTS_SITEMAPS = 5
MAX_URLS_PER_DOMAIN = 5000
MAX_ITEMS_PER_DOMAIN = 5
DEFAULT_WINDOW_DAYS = 14
BULK_MIN_CHANGES = 10
BULK_RATIO = 0.5
ALARM_MIN_ATTEMPTS = 4
DEFAULT_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")

NON_SITE_HOSTS = (
    "recruitee.com",
    "breezy.hr",
    "smartrecruiters.com",
    "ashbyhq.com",
    "notion.so",
    "wixsite.com",
)

UA_LADDER = build_ladder("sitemap")

ROBOTS_SITEMAP_RE = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$")
ASSET_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|ico|pdf|zip|mp4|mov|mp3|css|js|xml|json|txt|docx?|xlsx?|pptx?)$",
    re.IGNORECASE,
)
SLUG_SPLIT_RE = re.compile(r"[-_+\s]+")
NOISE_SLUG_RE = re.compile(r"^(unsubscribe|communication-preferences|elementor-\d+$)")

SKIP_SITEMAP_TOKENS = {
    "image",
    "images",
    "video",
    "videos",
    "product",
    "products",
    "attachment",
    "attachments",
    "tag",
    "tags",
    "category",
    "categories",
    "cat",
    "author",
    "authors",
    "collection",
    "collections",
    "format",
    "users",
    "user",
    "taxonomies",
}

NOISE_SEGMENTS = {
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "authors",
    "wp-content",
    "wp-includes",
    "wp-json",
    "wp-admin",
    "feed",
    "cdn-cgi",
    "search",
    "cart",
    "checkout",
    "account",
    "login",
    "signin",
    "signup",
    "register",
    "product",
    "products",
    "collections",
    "shop",
    "attachment",
    "uploads",
    "assets",
    "static",
    "privacy",
    "privacy-policy",
    "terms",
    "terms-of-service",
    "terms-of-use",
    "terms-and-conditions",
    "cookie-policy",
    "cookies",
    "disclaimer",
    "sitemap",
}

NOISE_QUERY_KEYS = {"page", "paged", "lang", "hl", "locale", "replytocom", "s"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def fetch_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for user_agent in UA_LADDER:
        req = request.Request(url, headers={"User-Agent": user_agent})
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                body = resp.read(MAX_BODY_BYTES + 1)
        except error.HTTPError as e:
            if e.code not in GATED_STATUSES:
                raise
            last_error = e
            continue
        if len(body) > MAX_BODY_BYTES:
            raise ValueError(f"body over {MAX_BODY_BYTES} bytes")
        return body
    assert last_error is not None
    raise last_error


def maybe_gunzip(body: bytes) -> bytes:
    if not body.startswith(b"\x1f\x8b"):
        return body
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
        data = handle.read(MAX_BODY_BYTES + 1)
    if len(data) > MAX_BODY_BYTES:
        raise ValueError(f"gunzipped body over {MAX_BODY_BYTES} bytes")
    return data


def parse_lastmod(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalise_lastmod(value: str | None) -> str:
    parsed = parse_lastmod(value)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def parse_sitemap(body: bytes) -> tuple[str, list[tuple[str, str]]]:
    root = ET.fromstring(maybe_gunzip(body))
    kind = _local(root.tag)
    if kind not in {"urlset", "sitemapindex"}:
        raise ValueError(f"not a sitemap: <{kind}>")
    entries: list[tuple[str, str]] = []
    for node in root:
        loc = ""
        lastmod = ""
        for child in node:
            name = _local(child.tag)
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = (child.text or "").strip()
        if loc:
            entries.append((loc, lastmod))
    return kind, entries


def robots_sitemaps(text: str) -> list[str]:
    found: list[str] = []
    for match in ROBOTS_SITEMAP_RE.finditer(text):
        url = match.group(1).strip()
        if url.lower().startswith(("http://", "https://")) and url not in found:
            found.append(url)
    return found[:MAX_ROBOTS_SITEMAPS]


def is_skipped_sitemap(url: str) -> bool:
    last = parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    query = parse.urlparse(url).query.lower()
    tokens = set(TOKEN_SPLIT_RE.split(last)) | set(TOKEN_SPLIT_RE.split(query))
    return bool(tokens & SKIP_SITEMAP_TOKENS)


def on_domain(url: str, domain: str) -> bool:
    host = parse.urlparse(url).netloc.lower().split(":")[0]
    return host == domain or host.endswith("." + domain)


def _segments(path: str) -> list[str]:
    return [s for s in path.lower().split("/") if s]


def is_noise_url(url: str) -> bool:
    parsed = parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    segments = _segments(parse.unquote(parsed.path))
    content = segments[1:] if segments and LOCALE_SEGMENT_RE.match(segments[0]) else segments
    if not content:
        return True
    if ASSET_EXT_RE.search(parsed.path):
        return True
    if any(seg in NOISE_SEGMENTS or NOISE_SLUG_RE.match(seg) for seg in segments):
        return True
    if any(seg == "page" and nxt.isdigit() for seg, nxt in zip(segments, segments[1:])):
        return True
    query_keys = {k.lower() for k in parse.parse_qs(parsed.query, keep_blank_values=True)}
    return bool(query_keys & NOISE_QUERY_KEYS)


def _locale_rank(url: str) -> tuple[int, str]:
    segments = _segments(parse.urlparse(url).path)
    if not segments or not LOCALE_SEGMENT_RE.match(segments[0]):
        return (0, url)
    if segments[0].startswith("en"):
        return (1, url)
    return (2, url)


def _canonical_path(url: str) -> str:
    parsed = parse.urlparse(url)
    segments = _segments(parsed.path)
    if segments and LOCALE_SEGMENT_RE.match(segments[0]):
        segments = segments[1:]
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + "/" + "/".join(segments)


def eligible_urls(raw: dict[str, str], domain: str) -> dict[str, str]:
    groups: dict[str, list[str]] = {}
    for url in raw:
        if not on_domain(url, domain) or is_noise_url(url):
            continue
        groups.setdefault(_canonical_path(url), []).append(url)
    kept: dict[str, str] = {}
    for urls in groups.values():
        best = min(urls, key=_locale_rank)
        kept[best] = raw[best]
    return dict(sorted(kept.items()))


def site_bases(domain: str) -> list[str]:
    bases = [f"https://{domain}"]
    if not domain.startswith("www."):
        bases.append(f"https://www.{domain}")
    return bases


def crawl_domain(
    domain: str,
    fetcher: Callable[[str], bytes] = fetch_bytes,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    calls = {"count": 0}

    def get(url: str) -> bytes:
        if calls["count"]:
            sleep(REQUEST_INTERVAL_SECS)
        calls["count"] += 1
        return fetcher(url)

    base = ""
    robots: list[str] = []
    for candidate in site_bases(domain):
        try:
            robots = robots_sitemaps(get(candidate + "/robots.txt").decode("utf-8", "replace"))
            base = candidate
            break
        except error.HTTPError:
            base = candidate
            break
        except (error.URLError, TimeoutError, OSError, ValueError) as e:
            print(f"sitemap: {candidate}/robots.txt unreachable: {e}", file=sys.stderr)
    if not base:
        return {"status": "unreachable", "urls": {}, "sitemaps": [], "fetches": calls["count"]}

    raw: dict[str, str] = {}
    parsed_sitemaps: list[str] = []
    incomplete = False
    fetches = 0
    visited: set[str] = set()

    def crawl(start: str) -> bool:
        nonlocal incomplete, fetches
        queue: list[tuple[str, int]] = [(start, 0)]
        found = False
        while queue:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            if fetches >= MAX_SITEMAP_FETCHES:
                incomplete = True
                return found
            visited.add(url)
            fetches += 1
            try:
                kind, entries = parse_sitemap(get(url))
            except error.HTTPError as e:
                if depth or start in robots:
                    incomplete = True
                print(f"sitemap: {url}: HTTP {e.code}", file=sys.stderr)
                continue
            except (error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as e:
                if depth or start in robots:
                    incomplete = True
                print(f"sitemap: {url}: {str(e)[:120]}", file=sys.stderr)
                continue
            found = True
            parsed_sitemaps.append(url)
            if kind == "sitemapindex":
                if depth + 1 > MAX_INDEX_DEPTH:
                    incomplete = True
                    continue
                children = [
                    (loc, parse_lastmod(lastmod))
                    for loc, lastmod in entries
                    if not is_skipped_sitemap(loc)
                ]
                floor = datetime.min.replace(tzinfo=timezone.utc)
                children.sort(key=lambda pair: pair[1] or floor, reverse=True)
                queue.extend((loc, depth + 1) for loc, _ in children)
                continue
            for loc, lastmod in entries:
                if len(raw) >= MAX_URLS_PER_DOMAIN:
                    incomplete = True
                    break
                raw[loc.split("#", 1)[0]] = normalise_lastmod(lastmod)
        return found

    found_any = False
    for sitemap_url in robots:
        found_any = crawl(sitemap_url) or found_any
    if not found_any:
        for path in DEFAULT_SITEMAP_PATHS:
            if crawl(base + path):
                found_any = True
                break

    if not found_any:
        return {"status": "none", "urls": {}, "sitemaps": [], "fetches": calls["count"]}
    return {
        "status": "partial" if incomplete else "ok",
        "urls": eligible_urls(raw, domain),
        "sitemaps": parsed_sitemaps,
        "fetches": calls["count"],
    }


def slug_title(url: str) -> str:
    segments = [s for s in parse.unquote(parse.urlparse(url).path).split("/") if s]
    while segments and segments[-1].isdigit():
        segments.pop()
    if not segments:
        return url
    slug = ASSET_EXT_RE.sub("", re.sub(r"\.html?$", "", segments[-1], flags=re.IGNORECASE))
    words = [w for w in SLUG_SPLIT_RE.split(slug) if w]
    if not words:
        return url
    return " ".join(w[:1].upper() + w[1:] for w in words)


def domain_changes(
    previous: dict[str, str], current: dict[str, str]
) -> list[tuple[str, str, str]]:
    changes: list[tuple[str, str, str]] = []
    for url, lastmod in current.items():
        if url not in previous:
            changes.append((url, lastmod, "new"))
            continue
        before = parse_lastmod(previous[url])
        after = parse_lastmod(lastmod)
        if before and after and after > before:
            changes.append((url, lastmod, "updated"))
    return changes


def is_bulk_change(changes: list[Any], previous: dict[str, str]) -> bool:
    return len(changes) > max(BULK_MIN_CHANGES, BULK_RATIO * len(previous))


def domain_items(
    company: str,
    domain: str,
    changes: list[tuple[str, str, str]],
    now: datetime,
    window_days: int,
    cap: int = MAX_ITEMS_PER_DOMAIN,
) -> list[dict[str, Any]]:
    stamp = now.isoformat(timespec="seconds")
    cutoff = now - timedelta(days=window_days)
    fresh: list[tuple[datetime, str, str, str]] = []
    for url, lastmod, change in changes:
        moment = parse_lastmod(lastmod)
        if moment is None and change == "updated":
            continue
        if moment is not None and moment < cutoff:
            continue
        fresh.append((moment or now, url, lastmod, change))
    fresh.sort(key=lambda row: (row[0], row[1]), reverse=True)
    items: list[dict[str, Any]] = []
    for _moment, url, lastmod, change in fresh[:cap]:
        verb = "New page listed" if change == "new" else "Page updated"
        when = f" (lastmod {lastmod[:10]})" if lastmod else ""
        items.append(
            {
                "company": company,
                "headline": slug_title(url),
                "description": f"{verb} in the sitemap of {company}'s own website {domain}{when}. "
                "Title derived from the URL slug; the page itself was not fetched.",
                "source": f"{domain} sitemap",
                "pubDate": lastmod or stamp,
                "link": url,
                "dedup_key": url,
                "source_kind": "sitemap",
                "sitemap_change": change,
            }
        )
    return items


def watchlist_domains(companies: list[dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for company in sorted(companies, key=lambda c: c.get("name", "")):
        if company.get("status") == "inactive":
            continue
        for domain in homepage_domains(company):
            if any(domain == h or domain.endswith("." + h) for h in NON_SITE_HOSTS):
                continue
            owners.setdefault(domain, company["name"])
    return dict(sorted(owners.items()))


def merge_urls(previous: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    merged = dict(previous)
    merged.update(current)
    if len(merged) > 2 * MAX_URLS_PER_DOMAIN:
        ranked = sorted(merged.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        merged = dict(ranked[: 2 * MAX_URLS_PER_DOMAIN])
    return dict(sorted(merged.items()))


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"sitemap: unreadable state {path}: {e}", file=sys.stderr)
        return {}
    domains = data.get("domains") if isinstance(data, dict) else None
    return domains if isinstance(domains, dict) else {}


def _tracked_urls(entry: dict[str, Any] | None) -> dict[str, str] | None:
    urls = (entry or {}).get("urls")
    return urls if isinstance(urls, dict) else None


def page_lastmod_moved(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]], url: str
) -> bool | None:
    for domain, entry in current.items():
        urls = _tracked_urls(entry)
        if not urls or url not in urls:
            continue
        before = _tracked_urls(previous.get(domain))
        if before is None:
            return None
        if url not in before:
            return True
        old, new = parse_lastmod(before[url]), parse_lastmod(urls[url])
        if old is None or new is None:
            return None
        return new > old
    return None


def url_variants(url: str) -> list[str]:
    parts = parse.urlsplit(url)
    host = parts.netloc.lower()
    bare = host.removeprefix("www.")
    path = parts.path or "/"
    paths = {path, path.rstrip("/") or "/", path.rstrip("/") + "/"}
    variants = [
        parse.urlunsplit((parts.scheme, netloc, candidate, parts.query, ""))
        for netloc in (host, bare, "www." + bare)
        for candidate in sorted(paths)
    ]
    return list(dict.fromkeys(variants))


def html_scrape_urls(companies: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for company in companies:
        name = company.get("name")
        for source in company.get("sources") or []:
            if name and source.get("type") == "html_scrape" and source.get("url"):
                pairs.append((name, source["url"]))
    return pairs


def unmoved_lastmod_urls(
    companies: list[dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    trusted_domains: set[str],
) -> dict[str, list[str]]:
    trusted_previous = {d: e for d, e in previous.items() if d in trusted_domains}
    trusted_current = {d: e for d, e in current.items() if d in trusted_domains}
    unmoved: dict[str, list[str]] = {}
    for name, url in html_scrape_urls(companies):
        verdicts = [
            page_lastmod_moved(trusted_previous, trusted_current, variant)
            for variant in url_variants(url)
        ]
        known = [verdict for verdict in verdicts if verdict is not None]
        if known and not any(known):
            unmoved.setdefault(name, []).append(url)
    return unmoved


def gate_changed_sources(changed_path: Path, unmoved: dict[str, list[str]]) -> int:
    if not changed_path.exists():
        return 0
    try:
        payload = json.loads(changed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"sitemap: cannot gate {changed_path}: {e}", file=sys.stderr)
        return 0
    per_company = payload.get("per_company")
    if not isinstance(per_company, dict):
        return 0
    gated: dict[str, list[str]] = {}
    for name, urls in list(per_company.items()):
        skip = set(unmoved.get(name, []))
        dropped = [url for url in urls if url in skip]
        if not dropped:
            continue
        gated[name] = dropped
        kept = [url for url in urls if url not in skip]
        if kept:
            per_company[name] = kept
        else:
            del per_company[name]
    count = sum(len(urls) for urls in gated.values())
    if count:
        payload["sitemap_gated"] = gated
        write_json(changed_path, payload)
    return count


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def run(
    companies: list[dict[str, Any]],
    state_path: Path,
    items_path: Path,
    fetcher: Callable[[str], bytes] = fetch_bytes,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int | None = None,
    only: str | None = None,
    changed_path: Path | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat(timespec="seconds")
    owners = watchlist_domains(companies)
    previous = load_state(state_path)
    state = {domain: entry for domain, entry in previous.items() if domain in owners}

    targets = list(owners.items())
    if only:
        needle = only.lower()
        targets = [(d, c) for d, c in targets if needle in d or needle in c.lower()]
    if limit is not None:
        targets = targets[:limit]

    items: list[dict[str, Any]] = []
    stats = {"domains": len(targets), "reached": 0, "with_sitemap": 0, "partial": 0, "seeded": 0}
    bulk_suppressed: list[str] = []
    trusted_domains: set[str] = set()
    for domain, company in targets:
        result = crawl_domain(domain, fetcher, sleep)
        before = state.get(domain) or {}
        prior_urls = _tracked_urls(before)
        entry = {
            "company": company,
            "status": result["status"],
            "checked_at": stamp,
            "sitemaps": result["sitemaps"] or before.get("sitemaps") or [],
            "urls": prior_urls,
        }
        if result["status"] != "unreachable":
            stats["reached"] += 1
        if result["status"] == "ok":
            trusted_domains.add(domain)
        if result["status"] in {"ok", "partial"}:
            stats["with_sitemap"] += 1
            current = result["urls"]
            if result["status"] == "partial":
                stats["partial"] += 1
            if prior_urls is None:
                stats["seeded"] += 1
            else:
                changes = domain_changes(prior_urls, current)
                if is_bulk_change(changes, prior_urls):
                    bulk_suppressed.append(domain)
                else:
                    items.extend(domain_items(company, domain, changes, moment, window_days))
            entry["urls"] = merge_urls(prior_urls or {}, current) if result["status"] == "partial" else current
        state[domain] = entry
        print(
            f"sitemap: {domain} ({company}) status={result['status']} "
            f"urls={len(result['urls'])} fetches={result['fetches']}",
            file=sys.stderr,
        )

    write_json(state_path, {"domains": dict(sorted(state.items()))})
    stats["sitemap_gated"] = 0
    if changed_path is not None:
        unmoved = unmoved_lastmod_urls(companies, previous, state, trusted_domains)
        stats["sitemap_gated"] = gate_changed_sources(changed_path, unmoved)
    all_unreachable = len(targets) >= ALARM_MIN_ATTEMPTS and stats["reached"] == 0
    write_json(
        items_path,
        {
            "generated_at": stamp,
            "items": items,
            "stats": {**stats, "items": len(items), "bulk_suppressed": bulk_suppressed},
            "error_rate_alarm": all_unreachable,
        },
    )
    print(
        f"sitemap: domains={stats['domains']} reached={stats['reached']} "
        f"with_sitemap={stats['with_sitemap']} partial={stats['partial']} seeded={stats['seeded']} "
        f"bulk_suppressed={len(bulk_suppressed)} items={len(items)} "
        f"sitemap_gated={stats['sitemap_gated']}",
        file=sys.stderr,
    )
    if all_unreachable:
        print("::warning::sitemap: every watchlist domain was unreachable; the sitemap diff is starved", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--items", type=Path, default=ITEMS_FILE)
    parser.add_argument("--companies", type=Path, default=COMPANIES_FILE)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--changed", type=Path, default=CHANGED_FILE)
    args = parser.parse_args()
    companies = json.loads(args.companies.read_text(encoding="utf-8"))
    return run(
        companies,
        args.state,
        args.items,
        window_days=args.window_days,
        limit=args.limit,
        only=args.only,
        changed_path=args.changed,
    )


if __name__ == "__main__":
    sys.exit(main())
