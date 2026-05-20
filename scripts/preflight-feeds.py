#!/usr/bin/env python3
"""Conditional preflight for the ingest pipeline.

Sends HTTP conditional GETs (If-None-Match + If-Modified-Since) against every
URL in data/feeds.json and every rss/github_org/lever_jobs source in
data/companies.json. Falls back to body SHA-256 comparison when the server
ignores conditional headers.

Outputs:
- data/feed-cache.json     — per-URL change-detection state, kept across runs.
- data/changed-sources.json — whitelist consumed by prompts/ingest.md. Lists
  only URLs that have changed since the last run; the agent skips everything
  else.

html_scrape sources are not preflighted — page bodies often contain dynamic
chrome that defeats hashing — so they are emitted as always-changed and the
agent fetches them on every run.

stdlib-only; no third-party deps. Exits 0 even on per-URL failures: a failed
URL is recorded with last_status<=0 and emitted as "changed" so the agent
retries it during the run.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = REPO_ROOT / "data" / "feeds.json"
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
CACHE_FILE = REPO_ROOT / "data" / "feed-cache.json"
CHANGED_FILE = REPO_ROOT / "data" / "changed-sources.json"

PREFLIGHTED_TYPES = {"rss", "github_org", "lever_jobs"}
ALWAYS_CHANGED_TYPES = {"html_scrape"}
USER_AGENT = "etp-hermes-preflight/1 (+https://github.com/luarss/etp-hermes)"
TIMEOUT_SECS = 20


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # corrupt cache — start fresh rather than fail the run
        return {}


def check_url(url: str, cache_entry: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    """Returns (changed, new_cache_entry). changed=True means the agent should process this URL."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if cache_entry:
        if cache_entry.get("etag"):
            headers["If-None-Match"] = cache_entry["etag"]
        if cache_entry.get("last_modified"):
            headers["If-Modified-Since"] = cache_entry["last_modified"]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    req = request.Request(url, headers=headers, method="GET")

    try:
        with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            status = resp.status
            body = resp.read()
            new_hash = hashlib.sha256(body).hexdigest()
            new_etag = resp.headers.get("ETag")
            new_lm = resp.headers.get("Last-Modified")

            prior_hash = (cache_entry or {}).get("body_sha256")
            changed = prior_hash != new_hash

            entry = {
                "etag": new_etag or (cache_entry or {}).get("etag"),
                "last_modified": new_lm or (cache_entry or {}).get("last_modified"),
                "body_sha256": new_hash,
                "last_status": status,
                "last_run": now,
            }
            if changed:
                entry["last_changed"] = now
            elif cache_entry and cache_entry.get("last_changed"):
                entry["last_changed"] = cache_entry["last_changed"]
            return changed, entry
    except error.HTTPError as e:
        if e.code == 304:
            entry = dict(cache_entry or {})
            entry["last_status"] = 304
            entry["last_run"] = now
            return False, entry
        # Other HTTP error — treat as changed so the agent retries the URL.
        entry = dict(cache_entry or {})
        entry["last_status"] = e.code
        entry["last_run"] = now
        return True, entry
    except (error.URLError, TimeoutError, OSError) as e:
        # Network failure — treat as changed so the agent retries.
        entry = dict(cache_entry or {})
        entry["last_status"] = -1
        entry["last_error"] = str(e)[:200]
        entry["last_run"] = now
        return True, entry


def main() -> int:
    feeds = load_json(FEEDS_FILE)
    companies = load_json(COMPANIES_FILE)
    cache = load_cache()

    firehose_changed: list[str] = []
    per_company_changed: dict[str, list[str]] = {}

    summary = {
        "checked": 0,
        "changed": 0,
        "unchanged": 0,
        "errors": 0,
        "html_scrape_passthrough": 0,
    }

    for feed in feeds:
        url = feed["url"]
        summary["checked"] += 1
        changed, new_entry = check_url(url, cache.get(url))
        cache[url] = new_entry
        if new_entry.get("last_status", 0) <= 0:
            summary["errors"] += 1
        if changed:
            firehose_changed.append(url)
            summary["changed"] += 1
        else:
            summary["unchanged"] += 1

    for c in companies:
        name = c.get("name")
        sources = c.get("sources") or []
        changed_for_company: list[str] = []
        for s in sources:
            stype = s.get("type")
            url = s.get("url")
            if not url:
                continue
            if stype in ALWAYS_CHANGED_TYPES:
                changed_for_company.append(url)
                summary["html_scrape_passthrough"] += 1
                continue
            if stype not in PREFLIGHTED_TYPES:
                # Unknown type — be safe, emit as changed so the agent handles it.
                changed_for_company.append(url)
                continue
            summary["checked"] += 1
            changed, new_entry = check_url(url, cache.get(url))
            cache[url] = new_entry
            if new_entry.get("last_status", 0) <= 0:
                summary["errors"] += 1
            if changed:
                changed_for_company.append(url)
                summary["changed"] += 1
            else:
                summary["unchanged"] += 1
        if changed_for_company:
            per_company_changed[name] = changed_for_company

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "firehose": firehose_changed,
        "per_company": per_company_changed,
    }
    with CHANGED_FILE.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)

    print(
        f"preflight: checked={summary['checked']} "
        f"changed={summary['changed']} unchanged={summary['unchanged']} "
        f"errors={summary['errors']} "
        f"html_scrape_passthrough={summary['html_scrape_passthrough']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
