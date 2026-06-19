#!/usr/bin/env python3
"""Deterministic candidate collection for Layer 1 (ingest).

Does everything in prompts/ingest.md steps 1-2.5 that needs no judgment:
fetches changed firehose feeds and per-company rss/github_org/lever_jobs
sources, parses them, applies date windows, dedupes against
signals/seen-urls.txt, runs the substring triage against company terms, and
folds in the pre-extracted html_scrape items from data/jina-items.json.

Writes data/candidates.json. The ingest prompt then only performs the LLM
relevance pass over the (small) candidate list instead of re-reading
companies.json, feeds.json, and seen-urls.txt into model context.

Fails open per source: a fetch or parse failure is recorded under
`fetch_failed` so the prompt can retry that URL with its own fetcher. The
script exits non-zero only when it cannot read its inputs or write its
output.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request

# Make the sibling `jina_fallback` module importable both when run directly
# (scripts/ is sys.path[0]) and under pytest's file-path module loader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jina_fallback import extract_items, fetch_reader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = REPO_ROOT / "data" / "feeds.json"
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
CHANGED_FILE = REPO_ROOT / "data" / "changed-sources.json"
JINA_ITEMS_FILE = REPO_ROOT / "data" / "jina-items.json"
SEEN_FILE = REPO_ROOT / "signals" / "seen-urls.txt"
OUT_FILE = REPO_ROOT / "data" / "candidates.json"

USER_AGENT = "etp-hermes-collect/1 (+https://github.com/luarss/etp-hermes)"
TIMEOUT_SECS = 25


def _window_days(env_name: str, default: int) -> timedelta:
    """Date-window length in days, overridable via env for the A/B backfill.

    The daily pipeline uses the defaults; the seeding job (ab-experiment.yml)
    widens these to pull a deeper historical pool in one batch.
    """
    try:
        return timedelta(days=int(os.environ.get(env_name, "") or default))
    except ValueError:
        return timedelta(days=default)


FIREHOSE_WINDOW = _window_days("COLLECT_FIREHOSE_DAYS", 7)
PER_COMPANY_WINDOW = _window_days("COLLECT_PER_COMPANY_DAYS", 14)
LEVER_WINDOW = _window_days("COLLECT_LEVER_DAYS", 30)

# Jina Reader fallback: when a firehose/rss feed's direct fetch or parse fails
# (host unreachable from the runner, but Jina can reach it), refetch it through
# r.jina.ai → Markdown → the shared heading/link heuristic. Keyless by default;
# JINA_API_KEY lifts the rate limit. Bounded per run so a wave of dead feeds
# can't blow Jina's free quota. Only firehose + rss are recovered this way —
# github_org/lever_jobs need fields (author/id, JSON) Markdown can't carry, so
# those stay in fetch_failed for the prompt to retry.
DEFAULT_JINA_FALLBACK_BUDGET = 25
JINA_FALLBACK_KINDS = {"firehose", "rss"}

ATOM_NS = "{http://www.w3.org/2005/Atom}"

# GitHub org Atom feeds mix releases and repo creations (signal) with bot
# pushes and chores (noise). Filter the noise deterministically; the prompt's
# relevance pass still backstops anything that slips through.
GITHUB_BOT_AUTHOR_RE = re.compile(r"(\[bot\]|dependabot|renovate)", re.IGNORECASE)
GITHUB_CHORE_TITLE_RE = re.compile(
    r"^(bump\b|update readme|chore[(:]|merge (pull request|branch))", re.IGNORECASE
)
GITHUB_KEEP_TITLE_RE = re.compile(
    r"(released|published a release|created a repository|created a tag .*release)",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def fetch(url: str) -> bytes:
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
        return resp.read()


def parse_date(value: str | None) -> datetime | None:
    """Parse RFC 822 (RSS) or ISO 8601 (Atom) dates. None when unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s).strip()


def parse_feed(body: bytes) -> list[dict[str, str]]:
    """Parse RSS 2.0 or Atom into [{title, link, description, pubDate, id, author}]."""
    root = ET.fromstring(body)
    items: list[dict[str, str]] = []

    for item in root.iter("item"):  # RSS 2.0
        items.append(
            {
                "title": _text(item.find("title")),
                "link": _text(item.find("link")),
                "description": _strip_html(_text(item.find("description")))[:500],
                "pubDate": _text(item.find("pubDate")) or _text(item.find("date")),
                "id": _text(item.find("guid")),
                "author": _text(item.find("author")),
            }
        )

    for entry in root.iter(f"{ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            if link_el.get("rel") in (None, "alternate"):
                link = link_el.get("href") or ""
                break
        items.append(
            {
                "title": _text(entry.find(f"{ATOM_NS}title")),
                "link": link,
                "description": _strip_html(
                    _text(entry.find(f"{ATOM_NS}summary"))
                    or _text(entry.find(f"{ATOM_NS}content"))
                )[:500],
                "pubDate": _text(entry.find(f"{ATOM_NS}published"))
                or _text(entry.find(f"{ATOM_NS}updated")),
                "id": _text(entry.find(f"{ATOM_NS}id")),
                "author": _text(entry.find(f"{ATOM_NS}author/{ATOM_NS}name")),
            }
        )

    return items


def within_window(pub: datetime | None, window: timedelta) -> bool:
    """Items with no parseable date pass (fail open); the relevance pass judges them."""
    if pub is None:
        return True
    return _now() - pub <= window


def github_entry_keep(entry: dict[str, str]) -> bool:
    title = entry["title"]
    if GITHUB_BOT_AUTHOR_RE.search(entry.get("author", "")) or GITHUB_BOT_AUTHOR_RE.search(title):
        return False
    if GITHUB_KEEP_TITLE_RE.search(title):
        return True
    if GITHUB_CHORE_TITLE_RE.search(title):
        return False
    # Push events: drop when the description (commit subjects) is all chores.
    desc = entry.get("description", "")
    if "pushed to" in title.lower() and GITHUB_CHORE_TITLE_RE.search(desc):
        return False
    return True


def lever_slug(url: str) -> str:
    path = parse.urlparse(url).path  # /v0/postings/<slug>
    return path.rstrip("/").rsplit("/", 1)[-1]


def collect(
    companies: list[dict[str, Any]],
    feeds: list[dict[str, Any]],
    changed: dict[str, Any] | None,
    jina: dict[str, Any] | None,
    seen: set[str],
    fetcher=fetch,
    reader=None,
    jina_api_key: str | None = None,
    jina_budget: int = DEFAULT_JINA_FALLBACK_BUDGET,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    fetch_failed: list[dict[str, str]] = []
    llm_fetch_required: list[dict[str, str]] = []
    seen_this_run: set[tuple[str, str]] = set()  # (company, dedup_key)

    def add(company: str, item: dict[str, Any]) -> None:
        key = (company, item["dedup_key"])
        if key in seen_this_run:
            return
        seen_this_run.add(key)
        candidates.append({"company": company, **item})

    terms = {
        c["name"]: [t.lower() for t in [c["name"], *(c.get("aliases") or [])]] for c in companies
    }

    # Jina Reader fallback state. reader=None disables it entirely (the default,
    # so existing callers/tests keep the direct-fetch-only behaviour); main()
    # passes the real fetch_reader.
    jina_state = {"calls": 0, "recovered": []}  # type: dict[str, Any]

    def jina_recover(
        url: str, *, kind: str, company: str | None, source_label: str, window: timedelta
    ) -> bool:
        """Refetch a failed feed via r.jina.ai and emit candidates from its items.

        Returns True when Jina reached the URL (caller should NOT record a fetch
        failure), False when the fallback is disabled, budget-capped, or Jina
        also failed.
        """
        if reader is None or jina_state["calls"] >= jina_budget:
            return False
        try:
            _status, markdown = reader(url, jina_api_key)
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            return False
        jina_state["calls"] += 1
        jina_state["recovered"].append(url)
        for it in extract_items(markdown, url):
            link = it["link"]
            if not link or link in seen:
                continue
            if not within_window(parse_date(it.get("pubDate")), window):
                continue
            item = {
                "headline": it["headline"],
                "description": "",
                "source": source_label,
                "pubDate": it.get("pubDate", ""),
                "link": link,
                "dedup_key": link,
                "source_kind": kind,
                "via_jina_fallback": True,
            }
            if company is None:  # firehose: substring-triage across the watchlist
                haystack = it["headline"].lower()
                for name, ts in terms.items():
                    if any(t in haystack for t in ts):
                        add(name, dict(item))
            else:
                add(company, item)
        return True

    # --- Firehose ---
    if changed is None:
        firehose_urls = {f["url"] for f in feeds}
    else:
        firehose_urls = set(changed.get("firehose") or [])
    for feed in feeds:
        if feed["url"] not in firehose_urls:
            continue
        try:
            entries = parse_feed(fetcher(feed["url"]))
        except (error.URLError, TimeoutError, OSError, ET.ParseError) as e:
            if jina_recover(
                feed["url"],
                kind="firehose",
                company=None,
                source_label=feed["name"],
                window=FIREHOSE_WINDOW,
            ):
                continue
            fetch_failed.append({"url": feed["url"], "kind": "firehose", "error": str(e)[:200]})
            continue
        for it in entries:
            link = it["link"]
            if not link or link in seen:
                continue
            if not within_window(parse_date(it["pubDate"]), FIREHOSE_WINDOW):
                continue
            haystack = f"{it['title']} {it['description']}".lower()
            for name, ts in terms.items():
                if any(t in haystack for t in ts):
                    add(
                        name,
                        {
                            "headline": it["title"],
                            "description": it["description"],
                            "source": feed["name"],
                            "pubDate": it["pubDate"],
                            "link": link,
                            "dedup_key": link,
                            "source_kind": "firehose",
                        },
                    )

    # --- Per-company curated sources ---
    if changed is None:
        changed_pc = {
            c["name"]: [s["url"] for s in c.get("sources") or [] if s.get("url")]
            for c in companies
        }
    else:
        changed_pc = changed.get("per_company") or {}

    jina_pc = (jina or {}).get("per_company") or {}

    for c in companies:
        name = c["name"]
        for s in c.get("sources") or []:
            url = s.get("url")
            if not url or url not in changed_pc.get(name, []):
                continue
            stype = s.get("type")
            label = s.get("label") or stype
            source_label = f"{name} · {label}"

            if stype == "html_scrape":
                pre = (jina_pc.get(name) or {}).get(url)
                if pre:
                    for it in pre:
                        if it["link"] in seen:
                            continue
                        if not within_window(parse_date(it.get("pubDate")), PER_COMPANY_WINDOW):
                            continue
                        add(
                            name,
                            {
                                "headline": it["headline"],
                                "description": "",
                                "source": source_label,
                                "pubDate": it.get("pubDate", ""),
                                "link": it["link"],
                                "dedup_key": it["link"],
                                "source_kind": "html_scrape",
                                "pre_extracted": True,
                            },
                        )
                else:
                    # No pre-extraction available (jina missed, failed, or
                    # deferred this URL) — the prompt parses the page itself.
                    llm_fetch_required.append(
                        {
                            "company": name,
                            "url": url,
                            "label": label,
                            "hint": s.get("hint", ""),
                        }
                    )
                continue

            try:
                body = fetcher(url)
            except (error.URLError, TimeoutError, OSError) as e:
                if stype == "rss" and jina_recover(
                    url,
                    kind="rss",
                    company=name,
                    source_label=source_label,
                    window=PER_COMPANY_WINDOW,
                ):
                    continue
                fetch_failed.append(
                    {"url": url, "kind": stype, "company": name, "error": str(e)[:200]}
                )
                continue

            if stype == "rss":
                try:
                    entries = parse_feed(body)
                except ET.ParseError as e:
                    if jina_recover(
                        url,
                        kind="rss",
                        company=name,
                        source_label=source_label,
                        window=PER_COMPANY_WINDOW,
                    ):
                        continue
                    fetch_failed.append(
                        {"url": url, "kind": stype, "company": name, "error": str(e)[:200]}
                    )
                    continue
                for it in entries:
                    link = it["link"]
                    if not link or link in seen:
                        continue
                    if not within_window(parse_date(it["pubDate"]), PER_COMPANY_WINDOW):
                        continue
                    add(
                        name,
                        {
                            "headline": it["title"],
                            "description": it["description"],
                            "source": source_label,
                            "pubDate": it["pubDate"],
                            "link": link,
                            "dedup_key": link,
                            "source_kind": "rss",
                        },
                    )

            elif stype == "github_org":
                try:
                    entries = parse_feed(body)
                except ET.ParseError as e:
                    fetch_failed.append(
                        {"url": url, "kind": stype, "company": name, "error": str(e)[:200]}
                    )
                    continue
                for it in entries:
                    key = it["id"] or it["link"]
                    if not key or key in seen:
                        continue
                    if not within_window(parse_date(it["pubDate"]), PER_COMPANY_WINDOW):
                        continue
                    if not github_entry_keep(it):
                        continue
                    add(
                        name,
                        {
                            "headline": it["title"],
                            "description": it["description"],
                            "source": source_label,
                            "pubDate": it["pubDate"],
                            "link": it["link"],
                            "dedup_key": key,
                            "source_kind": "github_org",
                        },
                    )

            elif stype == "lever_jobs":
                try:
                    postings = json.loads(body)
                except json.JSONDecodeError as e:
                    fetch_failed.append(
                        {"url": url, "kind": stype, "company": name, "error": str(e)[:200]}
                    )
                    continue
                slug = lever_slug(url)
                for p in postings if isinstance(postings, list) else []:
                    key = f"lever://{slug}/{p.get('id')}"
                    if key in seen:
                        continue
                    created = p.get("createdAt")
                    if isinstance(created, (int, float)):
                        pub = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                        if _now() - pub > LEVER_WINDOW:
                            continue
                        pub_str = pub.date().isoformat()
                    else:
                        pub_str = ""
                    team = (p.get("categories") or {}).get("team") or ""
                    headline = f"{p.get('text', '')} — {team}" if team else p.get("text", "")
                    add(
                        name,
                        {
                            "headline": headline,
                            "description": "",
                            "source": source_label,
                            "pubDate": pub_str,
                            "link": p.get("hostedUrl", ""),
                            "dedup_key": key,
                            "source_kind": "lever_jobs",
                        },
                    )

    matched = sorted({c["company"] for c in candidates})
    descriptions = {c["name"]: c.get("description", "") for c in companies if c["name"] in matched}

    jina_candidates = sum(1 for c in candidates if c.get("via_jina_fallback"))

    return {
        "generated_at": _now().isoformat(timespec="seconds"),
        "candidates": candidates,
        "companies": descriptions,
        "llm_fetch_required": llm_fetch_required,
        "fetch_failed": fetch_failed,
        "jina_recovered": sorted(set(jina_state["recovered"])),
        "stats": {
            "candidates": len(candidates),
            "companies_matched": len(matched),
            "llm_fetch_required": len(llm_fetch_required),
            "fetch_failed": len(fetch_failed),
            "jina_recovered": len(set(jina_state["recovered"])),
            "jina_candidates": jina_candidates,
        },
    }


def main() -> int:
    companies = _load_json(COMPANIES_FILE)
    feeds = _load_json(FEEDS_FILE)
    # COLLECT_ALL_SOURCES=1 ignores the changed-sources whitelist (fetch every
    # source) — used by the A/B backfill to build a wide pool. The daily path
    # leaves it unset and honours the whitelist.
    if os.environ.get("COLLECT_ALL_SOURCES") == "1":
        changed = None
    else:
        changed = _load_json(CHANGED_FILE) if CHANGED_FILE.exists() else None
    jina = _load_json(JINA_ITEMS_FILE) if JINA_ITEMS_FILE.exists() else None
    # COLLECT_SEEN_FILE overrides the dedup list. The backfill points this at an
    # empty file so it re-collects items both arms already judged — the A/B is a
    # policy comparison, not a freshness check.
    seen_path = Path(os.environ["COLLECT_SEEN_FILE"]) if os.environ.get("COLLECT_SEEN_FILE") else SEEN_FILE
    seen = load_seen(seen_path)

    try:
        jina_budget = int(os.environ.get("JINA_FALLBACK_BUDGET", str(DEFAULT_JINA_FALLBACK_BUDGET)))
    except ValueError:
        jina_budget = DEFAULT_JINA_FALLBACK_BUDGET

    out = collect(
        companies,
        feeds,
        changed,
        jina,
        seen,
        reader=fetch_reader,
        jina_api_key=os.environ.get("JINA_API_KEY") or None,
        jina_budget=jina_budget,
    )

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)

    s = out["stats"]
    print(
        f"collect-candidates: candidates={s['candidates']} "
        f"companies={s['companies_matched']} "
        f"llm_fetch_required={s['llm_fetch_required']} "
        f"fetch_failed={s['fetch_failed']} "
        f"jina_recovered={s['jina_recovered']} "
        f"jina_candidates={s['jina_candidates']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
