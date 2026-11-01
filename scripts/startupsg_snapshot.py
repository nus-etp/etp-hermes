#!/usr/bin/env python3
"""Daily Startup SG directory snapshot and diff for the watchlist.

Pages through the keyless Startup SG profile search API, matches profiles to
watchlist companies (identifiers.uen first, then for Singapore entries a
normalised name/alias match that must be unique on both sides; same-named
non-SG entries collide, e.g. Indonesian Assemblr vs an SG events startup), and
writes the committed snapshot
data/startupsg/watchlist-profiles.json. Every meaningful change against the
previous snapshot (stage change, funding flag false to true, employee range
change, newly matched profile) becomes a candidate-like item in
data/startupsg-items.json, which collect-candidates.py folds into
data/candidates.json for the normal Layer 1 relevance pass.

The first run (no previous snapshot) seeds the snapshot and emits no items.

Fails open: any page failure leaves the snapshot untouched and writes no items
(exit 0), because a partial crawl would make missing companies reappear as new
tomorrow. When every page fails the script exits 1 so the continue-on-error
workflow step shows red.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from user_agents import GATED_STATUSES, build_ladder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
SNAPSHOT_FILE = REPO_ROOT / "data" / "startupsg" / "watchlist-profiles.json"
ITEMS_FILE = REPO_ROOT / "data" / "startupsg-items.json"

API_URL = "https://www.startupsg.gov.sg/api/v0/search/profiles/startup"
PROFILE_URL = "https://www.startupsg.gov.sg/profiles/{profile_id}"
PAGE_SIZE = 100
MAX_PAGES = 200
PAGE_INTERVAL_SECS = 1.0
TIMEOUT_SECS = 30
SOURCE_LABEL = "Startup SG"
SINGAPORE = "singapore"
VOLATILE_FIELDS = {"descriptor"}

UA_LADDER = build_ladder("startupsg")

LEGAL_SUFFIX_RE = re.compile(
    r"\b(pte\.?\s*ltd\.?|private\s+limited|pte\.?|ltd\.?|limited|llp|inc\.?|corp\.?)\s*$",
    re.IGNORECASE,
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalise_name(name: str) -> str:
    stripped = (name or "").strip()
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = LEGAL_SUFFIX_RE.sub("", stripped).strip(" .,")
    return NON_ALNUM_RE.sub("", stripped.lower())


def slug_value(value: Any) -> str:
    text = str(value).strip().lower()
    return NON_ALNUM_RE.sub("-", text).strip("-") or "none"


def fetch_json(url: str) -> Any:
    last_error: Exception | None = None
    for user_agent in UA_LADDER:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            if e.code not in GATED_STATUSES:
                raise
            last_error = e
    assert last_error is not None
    raise last_error


def page_url(offset: int) -> str:
    query = parse.urlencode(
        {"type": "listing", "sort": "-changed", "from": offset, "size": PAGE_SIZE}
    )
    return f"{API_URL}?{query}"


def fetch_all_profiles(
    fetcher: Callable[[str], Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], int, int]:
    profiles: list[dict[str, Any]] = []
    pages_ok = 0
    pages_failed = 0
    total: int | None = None
    offset = 0
    for page_index in range(MAX_PAGES):
        if page_index:
            sleep(PAGE_INTERVAL_SECS)
        try:
            body = fetcher(page_url(offset))
            data = body["data"]
            if not isinstance(data, list):
                raise ValueError("data is not a list")
            total = int(body.get("total") or 0) if total is None else total
        except (error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
            pages_failed += 1
            print(f"startupsg: page from={offset} failed: {e}", file=sys.stderr)
            if total is None:
                return profiles, pages_ok, pages_failed
            offset += PAGE_SIZE
            if offset >= total:
                break
            continue
        pages_ok += 1
        profiles.extend(p for p in data if isinstance(p, dict))
        offset += PAGE_SIZE
        if not data or offset >= total:
            break
    return profiles, pages_ok, pages_failed


def _named(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return ""


def profile_record(profile: dict[str, Any], matched_on: str) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "").strip()
    return {
        "uen": str(profile.get("uen") or "").strip(),
        "profile_id": profile_id,
        "profile_url": PROFILE_URL.format(profile_id=profile_id),
        "display_name": str(profile.get("displayName") or "").strip(),
        "stage": _named(profile.get("investmentStage")),
        "has_funding_rounds": bool(profile.get("hasFundingRounds")),
        "employees": _named(profile.get("rangeEmployee")),
        "descriptor": str(profile.get("companyDescriptor") or "").strip(),
        "matched_on": matched_on,
    }


def _company_name_keys(company: dict[str, Any]) -> set[str]:
    names = [company.get("name") or "", *(company.get("aliases") or [])]
    return {key for key in (normalise_name(n) for n in names) if key}


def _profile_name_keys(profile: dict[str, Any]) -> set[str]:
    names = [profile.get("displayName") or "", profile.get("registeredName") or ""]
    return {key for key in (normalise_name(n) for n in names) if key}


def match_profiles(
    companies: list[dict[str, Any]], profiles: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_uen: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        uen = str(profile.get("uen") or "").strip().upper()
        if uen:
            by_uen.setdefault(uen, []).append(profile)
        for key in _profile_name_keys(profile):
            by_name.setdefault(key, []).append(profile)

    matches: dict[str, tuple[dict[str, Any], str]] = {}
    for company in companies:
        uen = str((company.get("identifiers") or {}).get("uen") or "").strip().upper()
        if uen and len(by_uen.get(uen, [])) == 1:
            matches[company["name"]] = (by_uen[uen][0], "uen")
            continue
        if uen or str(company.get("country") or "").strip().lower() != SINGAPORE:
            continue
        hits = {
            id(p): p for key in _company_name_keys(company) for p in by_name.get(key, [])
        }
        if len(hits) == 1:
            matches[company["name"]] = (next(iter(hits.values())), "name")

    claims: dict[int, list[str]] = {}
    for name, (profile, _how) in matches.items():
        claims.setdefault(id(profile), []).append(name)
    result: dict[str, dict[str, Any]] = {}
    for name, (profile, how) in matches.items():
        claimants = claims[id(profile)]
        if len(claimants) > 1 and how != "uen":
            continue
        result[name] = profile_record(profile, how)
    return dict(sorted(result.items()))


def _item(
    company: str, record: dict[str, Any], field: str, value: Any, headline: str, now: str
) -> dict[str, Any]:
    identity = record["uen"] or f"id-{record['profile_id']}"
    funding = "yes" if record["has_funding_rounds"] else "no"
    details = (
        f"Self-reported Startup SG directory profile. Stage: {record['stage'] or 'n/a'}; "
        f"employees: {record['employees'] or 'n/a'}; funding rounds reported: {funding}."
    )
    if record["descriptor"]:
        details = f"{record['descriptor']} — {details}"
    return {
        "company": company,
        "headline": headline,
        "description": details,
        "source": SOURCE_LABEL,
        "pubDate": now,
        "link": record["profile_url"],
        "dedup_key": f"startupsg://{identity}/{field}/{slug_value(value)}",
        "source_kind": "startupsg",
    }


def diff_items(
    previous: dict[str, dict[str, Any]] | None,
    current: dict[str, dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    if previous is None:
        return []
    items: list[dict[str, Any]] = []
    for company, record in current.items():
        before = previous.get(company)
        if before is None:
            stage = record["stage"] or "unspecified stage"
            items.append(
                _item(
                    company,
                    record,
                    "profile",
                    "listed",
                    f"{company} listed on the Startup SG directory ({stage})",
                    now,
                )
            )
            continue
        if record["stage"] and record["stage"] != before.get("stage"):
            was = before.get("stage") or "unspecified"
            items.append(
                _item(
                    company,
                    record,
                    "stage",
                    record["stage"],
                    f"{company} now lists investment stage {record['stage']} on Startup SG (was {was})",
                    now,
                )
            )
        if record["has_funding_rounds"] and not before.get("has_funding_rounds"):
            items.append(
                _item(
                    company,
                    record,
                    "funding",
                    "true",
                    f"{company} now reports funding rounds on its Startup SG profile",
                    now,
                )
            )
        if record["employees"] and record["employees"] != before.get("employees"):
            was = before.get("employees") or "unspecified"
            items.append(
                _item(
                    company,
                    record,
                    "employees",
                    record["employees"],
                    f"{company} headcount on Startup SG now {record['employees']} (was {was})",
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
        print(f"startupsg: unreadable snapshot {path}: {e}", file=sys.stderr)
        return None
    companies = data.get("companies") if isinstance(data, dict) else None
    return companies if isinstance(companies, dict) else None


def snapshot_view(matched: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: {k: v for k, v in record.items() if k not in VOLATILE_FIELDS}
        for name, record in matched.items()
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def run(
    companies: list[dict[str, Any]],
    snapshot_path: Path,
    items_path: Path,
    fetcher: Callable[[str], Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    profiles, pages_ok, pages_failed = fetch_all_profiles(fetcher, sleep)
    complete = pages_ok > 0 and pages_failed == 0
    all_failed = pages_ok == 0

    items: list[dict[str, Any]] = []
    matched: dict[str, dict[str, Any]] = {}
    if complete:
        matched = match_profiles(companies, profiles)
        previous = load_snapshot(snapshot_path)
        items = diff_items(previous, matched, stamp)
        write_json(snapshot_path, {"source": API_URL, "companies": snapshot_view(matched)})

    write_json(
        items_path,
        {
            "generated_at": stamp,
            "items": items,
            "profiles_fetched": len(profiles),
            "watchlist_matched": len(matched),
            "pages_ok": pages_ok,
            "pages_failed": pages_failed,
            "snapshot_updated": complete,
            "error_rate_alarm": all_failed,
        },
    )
    print(
        f"startupsg: profiles={len(profiles)} pages_ok={pages_ok} pages_failed={pages_failed} "
        f"matched={len(matched)} items={len(items)} snapshot_updated={complete}",
        file=sys.stderr,
    )
    if all_failed:
        print(
            "::warning::startupsg: every Startup SG page fetch failed; the directory diff is starved",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    return run(companies, SNAPSHOT_FILE, ITEMS_FILE)


if __name__ == "__main__":
    sys.exit(main())
