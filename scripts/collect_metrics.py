#!/usr/bin/env python3
"""Collect a daily public-attention metric snapshot per watchlisted company.

Reads `data/companies.json`. For each company, queries a small set of free,
public, rate-limited endpoints and appends one JSON record per UTC day to
`data/metrics/<slug>.jsonl`. Designed to be idempotent: re-running on the same
UTC date replaces the last row instead of appending a duplicate.

Sources:
  - GitHub org/user stats — only when the company has a `github_org` source.
  - Lever open postings — only when the company has a `lever_jobs` source.
  - Hacker News Algolia mention count (rolling 30 days) — all companies.
  - GDELT DOC 2.0 global news article count (rolling 7 days) — only when the
    company has at least one structured `sources` entry. Skipped for the
    long tail of leaf-watchlist (NUS GRIP) companies because GDELT's 5-second
    per-IP rate limit makes covering 200+ names take ~20 minutes per run,
    and those companies have no measurable global-news surface anyway.

Fields missing from a record mean "didn't try" (no applicable source). A null
value means "tried but the endpoint failed or returned nothing". Numeric values
are real counts. The renderer relies on this distinction to decide which
subplots to draw.

Pure stdlib (urllib). Set GITHUB_TOKEN to lift GitHub's rate limit from
60/hr to 5,000/hr.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_PATH = REPO_ROOT / "data" / "companies.json"
METRICS_DIR = REPO_ROOT / "data" / "metrics"

USER_AGENT = "etp-hermes-metrics/1.0 (+https://github.com)"
HN_PAUSE_S = 0.3
GDELT_PAUSE_S = 5.5  # GDELT enforces ~one request per 5s per IP.
GDELT_TIMEOUT_S = 45  # GDELT's tail latency is high; give it more headroom than other endpoints.
GDELT_BACKOFF_S = 12  # Initial backoff after a 429/rate-limit signal; doubled each retry.
GDELT_MAX_ATTEMPTS = 3
GITHUB_PAUSE_S = 0.1
HTTP_TIMEOUT_S = 20


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def parse_github_org(source_url: str) -> str | None:
    # Source URL is shaped like "https://github.com/<org>.atom".
    m = re.search(r"github\.com/([^/]+?)(?:\.atom)?$", source_url)
    return m.group(1) if m else None


def github_stats(org: str, token: str | None) -> dict[str, int | None] | None:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # /orgs/ first; /users/ fallback (some "orgs" in companies.json are user accounts).
    profile: dict[str, Any] | None = None
    is_org = True
    for path in (f"orgs/{org}", f"users/{org}"):
        try:
            profile = http_get_json(f"https://api.github.com/{path}", headers=headers)
            is_org = path.startswith("orgs/")
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return None
        except Exception:
            return None
    if not profile:
        return None

    followers = profile.get("followers")
    public_repos = profile.get("public_repos")

    # Sum stargazers across repos (paginated). Cap at 5 pages = 500 repos.
    stars: int | None = 0
    repos_path = "orgs" if is_org else "users"
    try:
        for page in range(1, 6):
            time.sleep(GITHUB_PAUSE_S)
            url = (
                f"https://api.github.com/{repos_path}/{org}/repos"
                f"?per_page=100&page={page}&type=public"
            )
            repos = http_get_json(url, headers=headers)
            if not repos:
                break
            stars += sum(int(r.get("stargazers_count") or 0) for r in repos)
            if len(repos) < 100:
                break
    except Exception:
        stars = None

    return {"stars": stars, "followers": followers, "repos": public_repos}


def lever_open_jobs(source_url: str) -> int | None:
    try:
        data = http_get_json(source_url)
        if isinstance(data, list):
            return len(data)
        return None
    except Exception:
        return None


def hn_mentions_30d(name: str, now: dt.datetime) -> int | None:
    epoch_cutoff = int((now - dt.timedelta(days=30)).timestamp())
    q = urllib.parse.quote(f'"{name}"')
    url = (
        f"https://hn.algolia.com/api/v1/search"
        f"?query={q}&numericFilters=created_at_i%3E{epoch_cutoff}&hitsPerPage=0"
    )
    try:
        data = http_get_json(url)
        n = data.get("nbHits")
        return int(n) if n is not None else None
    except Exception:
        return None


def gdelt_articles_7d(name: str) -> int | None:
    # GDELT signals rate-limit violations two ways: an HTTP 429, or a plaintext
    # "Please limit requests" page served with HTTP 200. Both get exponential
    # backoff and multiple retries. Socket timeouts are also retried (with the
    # standard inter-request pause). JSON-parse failures on a non-rate-limit
    # body — e.g. "The specified phrase is too short." for 2-char names — are
    # treated as permanent and not retried.
    q = urllib.parse.quote(f'"{name}"')
    url = (
        f"https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={q}&mode=ArtList&maxrecords=250&timespan=7d&format=json"
    )
    backoff = GDELT_BACKOFF_S
    for attempt in range(GDELT_MAX_ATTEMPTS):
        last = attempt == GDELT_MAX_ATTEMPTS - 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=GDELT_TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and not last:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if last:
                return None
            time.sleep(GDELT_PAUSE_S)
            continue
        except Exception:
            return None

        if "Please limit requests" in body:
            if last:
                return None
            time.sleep(backoff)
            backoff *= 2
            continue
        try:
            data = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            return None
        articles = data.get("articles") or []
        return len(articles)
    return None


def find_source(company: dict[str, Any], source_type: str) -> dict[str, Any] | None:
    for s in company.get("sources") or []:
        if s.get("type") == source_type:
            return s
    return None


def collect_one(company: dict[str, Any], now: dt.datetime, gh_token: str | None) -> dict[str, Any]:
    name = company["name"]
    record: dict[str, Any] = {"date": now.strftime("%Y-%m-%d")}

    gh_src = find_source(company, "github_org")
    if gh_src:
        org = parse_github_org(gh_src.get("url", ""))
        if org:
            record["github"] = github_stats(org, gh_token)

    lever_src = find_source(company, "lever_jobs")
    if lever_src:
        record["lever"] = {"open": lever_open_jobs(lever_src["url"])}

    record["hn_30d"] = hn_mentions_30d(name, now)
    time.sleep(HN_PAUSE_S)

    # GDELT is rate-limited to one request per 5s per IP. Reserve it for
    # companies we've deemed worth tracking structurally (i.e. have any
    # `sources` entry); the long tail of leaf-watchlist names returns
    # zeros anyway and would bloat the workflow runtime.
    if company.get("sources"):
        record["gdelt_7d"] = gdelt_articles_7d(name)
        time.sleep(GDELT_PAUSE_S)

    return record


def upsert_record(jsonl_path: Path, record: dict[str, Any]) -> None:
    """Append the record, or replace the trailing line if its date matches."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if jsonl_path.exists():
        existing = jsonl_path.read_text().splitlines()
    if existing:
        try:
            last = json.loads(existing[-1])
            if last.get("date") == record["date"]:
                existing = existing[:-1]
        except json.JSONDecodeError:
            pass
    existing.append(json.dumps(record, separators=(",", ":"), sort_keys=True))
    jsonl_path.write_text("\n".join(existing) + "\n")


def iter_selected(
    companies: list[dict[str, Any]], only: Iterable[str] | None
) -> Iterable[dict[str, Any]]:
    if not only:
        yield from companies
        return
    wanted = {s.strip().lower() for s in only if s.strip()}
    for c in companies:
        if c["name"].lower() in wanted or slugify(c["name"]) in wanted:
            yield c


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default=os.environ.get("METRICS_ONLY", ""),
        help="Comma-separated company names or slugs to limit collection to.",
    )
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date for the snapshot (default: today).",
    )
    args = parser.parse_args()

    companies = json.loads(COMPANIES_PATH.read_text())
    now = dt.datetime.fromisoformat(args.date).replace(tzinfo=dt.timezone.utc)
    only = [s for s in args.only.split(",") if s.strip()] if args.only else None
    gh_token = os.environ.get("GITHUB_TOKEN") or None

    METRICS_DIR.mkdir(parents=True, exist_ok=True)

    n_done = 0
    for company in iter_selected(companies, only):
        slug = slugify(company["name"])
        try:
            record = collect_one(company, now, gh_token)
        except Exception as e:
            print(f"  ! {company['name']}: collect failed: {e}", file=sys.stderr)
            continue
        upsert_record(METRICS_DIR / f"{slug}.jsonl", record)
        n_done += 1
        summary = ", ".join(
            f"{k}={v}"
            for k, v in record.items()
            if k != "date" and not isinstance(v, dict)
        )
        print(f"  - {company['name']} [{slug}] {summary}")

    print(f"collected: {n_done} companies on {now.date().isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
