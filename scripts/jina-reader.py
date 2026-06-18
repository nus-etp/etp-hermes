#!/usr/bin/env python3
"""Prefetch html_scrape sources via Jina Reader, extract items deterministically.

Reads data/companies.json + data/changed-sources.json. For every html_scrape
source listed as changed, fetches https://r.jina.ai/<source-url> to get clean
Markdown, then runs a heading-plus-link heuristic to extract candidate items.
Writes data/jina-items.json (consumed by prompts/ingest.md) and a markdown
cache under data/jina-cache/ (gitignored).

Fails open: HTTP errors and extraction misses are recorded and the prompt's
existing LLM fallback path picks them up. The script always exits 0.

Daily call budget is bounded by JINA_DAILY_BUDGET (default 80) so a flaky
cache or a hand-triggered re-run can't blow Jina's 100/day free quota.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error
from urllib import request  # noqa: F401  (kept as the `jina.request` monkeypatch target in tests)

# Make the sibling `jina_fallback` module importable both when run directly
# (scripts/ is sys.path[0]) and under pytest's file-path module loader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jina_fallback import extract_items, fetch_reader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
CHANGED_FILE = REPO_ROOT / "data" / "changed-sources.json"
CACHE_DIR = REPO_ROOT / "data" / "jina-cache"
CACHE_INDEX_FILE = CACHE_DIR / "index.json"
ITEMS_FILE = REPO_ROOT / "data" / "jina-items.json"

DEFAULT_BUDGET = 80
CACHE_TTL = timedelta(hours=23)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_cache_index() -> dict[str, Any]:
    if not CACHE_INDEX_FILE.exists():
        return {}
    try:
        with CACHE_INDEX_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _cache_path_for(url: str) -> Path:
    sha = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{sha}.md"


def _cache_fresh(entry: dict[str, Any]) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        fetched_at = datetime.fromisoformat(ts)
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at < CACHE_TTL


def _iter_html_scrape_targets(
    companies: list[dict[str, Any]],
    changed_per_company: dict[str, list[str]] | None,
) -> list[tuple[str, dict[str, Any]]]:
    """(company_name, source) pairs to fetch. None changed_per_company means cold-start (all)."""
    targets: list[tuple[str, dict[str, Any]]] = []
    for c in companies:
        name = c.get("name")
        if not name:
            continue
        for s in c.get("sources") or []:
            if s.get("type") != "html_scrape":
                continue
            url = s.get("url")
            if not url:
                continue
            if changed_per_company is None:
                targets.append((name, s))
                continue
            if url in changed_per_company.get(name, []):
                targets.append((name, s))
    return targets


def main() -> int:
    companies = _load_json(COMPANIES_FILE)
    changed_per_company: dict[str, list[str]] | None
    if CHANGED_FILE.exists():
        changed = _load_json(CHANGED_FILE)
        changed_per_company = changed.get("per_company") or {}
    else:
        changed_per_company = None  # cold-start: fetch all html_scrape sources

    targets = _iter_html_scrape_targets(companies, changed_per_company)

    cache_index = _load_cache_index()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("JINA_API_KEY") or None
    try:
        budget = int(os.environ.get("JINA_DAILY_BUDGET", str(DEFAULT_BUDGET)))
    except ValueError:
        budget = DEFAULT_BUDGET

    per_company_items: dict[str, dict[str, list[dict[str, Any]]]] = {}
    extraction_failed: list[str] = []
    deferred: list[str] = []
    network_calls = 0
    cache_hits = 0
    errors = 0

    for company_name, source in targets:
        url = source["url"]
        cache_entry = cache_index.get(url) or {}
        cache_file = _cache_path_for(url)

        markdown: str | None = None
        status: int | None = None

        if _cache_fresh(cache_entry) and cache_file.exists():
            try:
                markdown = cache_file.read_text(encoding="utf-8")
                status = int(cache_entry.get("status", 200))
                cache_hits += 1
            except OSError as e:
                print(f"jina-reader: cache read failed for {url}: {e}", file=sys.stderr)
                markdown = None

        if markdown is None:
            if network_calls >= budget:
                deferred.append(url)
                print(
                    f"jina-reader: budget {budget} reached, deferring {url}",
                    file=sys.stderr,
                )
                continue
            try:
                status, markdown = fetch_reader(url, api_key)
                network_calls += 1
            except error.HTTPError as e:
                errors += 1
                extraction_failed.append(url)
                cache_index[url] = {
                    "fetched_at": _now_iso(),
                    "status": e.code,
                    "error": str(e)[:200],
                }
                print(f"jina-reader: HTTP {e.code} for {url}", file=sys.stderr)
                continue
            except (error.URLError, TimeoutError, OSError) as e:
                errors += 1
                extraction_failed.append(url)
                cache_index[url] = {
                    "fetched_at": _now_iso(),
                    "status": -1,
                    "error": str(e)[:200],
                }
                print(f"jina-reader: network error for {url}: {e}", file=sys.stderr)
                continue

            try:
                cache_file.write_text(markdown, encoding="utf-8")
            except OSError as e:
                print(
                    f"jina-reader: cache write failed for {url}: {e}",
                    file=sys.stderr,
                )

            cache_index[url] = {
                "fetched_at": _now_iso(),
                "status": status,
                "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                "cache_file": cache_file.name,
            }

        items = extract_items(markdown, url)
        if not items:
            extraction_failed.append(url)
            continue
        label = source.get("label") or ""
        for item in items:
            item["label"] = label
        per_company_items.setdefault(company_name, {})[url] = items

    try:
        with CACHE_INDEX_FILE.open("w", encoding="utf-8") as f:
            json.dump(cache_index, f, indent=2, sort_keys=True)
    except OSError as e:
        print(f"jina-reader: cache index write failed: {e}", file=sys.stderr)

    output = {
        "generated_at": _now_iso(),
        "per_company": per_company_items,
        "extraction_failed": sorted(set(extraction_failed)),
        "deferred": deferred,
        "budget_used": network_calls,
        "budget_limit": budget,
        "cache_hits": cache_hits,
    }
    ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ITEMS_FILE.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=True)

    total_items = sum(
        len(items) for sources in per_company_items.values() for items in sources.values()
    )
    print(
        f"jina-reader: targets={len(targets)} fetched={network_calls} "
        f"cache_hits={cache_hits} errors={errors} "
        f"extracted_items={total_items} extraction_failed={len(set(extraction_failed))} "
        f"deferred={len(deferred)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
