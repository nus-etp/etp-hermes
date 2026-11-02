#!/usr/bin/env python3
"""Daily IPOS patent and trade mark delta for the watchlist.

Reads the keyless data.gov.sg datastore copies of the IPOS registers
(IPOS Trade Mark Applications, IPOS Patent Applications), filtered on
lastModifiedDate for each of the last --days Singapore calendar days so a
missed run is caught up. Applicants are matched to watchlist companies by
UEN (identifiers.uen) first, then by a normalised name/alias that must map to
exactly one watchlist company and belong to a Singapore-incorporated
applicant. Name keys come from Singapore companies' names and aliases, plus
any non-Singapore company's alias that names a Pte entity. IPOS often files a
local company under an internal E-prefixed code instead of its ACRA UEN, so a
UEN-pinned company may still name-match, but only an applicant whose code is
empty or IPOS-internal; a different ACRA UEN means a namesake.

Every matched record becomes a candidate-like item in data/ipos-items.json,
which collect-candidates.py folds into data/candidates.json for the normal
Layer 1 relevance pass. The dedup key carries the record's status so a later
publication or grant of the same application is a new event while repeated
edits at the same status are not.

Fails open: a failed day/dataset fetch is logged and skipped; the script
exits 1 only when every fetch failed, so the continue-on-error workflow step
shows red.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from startupsg_snapshot import normalise_name, slug_value  # noqa: E402
from user_agents import honest_ua  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
ITEMS_FILE = REPO_ROOT / "data" / "ipos-items.json"

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
USER_AGENT = honest_ua("ipos-delta")
SOURCE_LABEL = "IPOS (data.gov.sg)"
SINGAPORE = "singapore"
SG_COUNTRY_CODE = "SG"
SGT = timezone(timedelta(hours=8))
PAGE_SIZE = 1000
MIN_PAGE_SIZE = 50
MAX_PAGES = 60
PAYLOAD_TOO_LARGE = 413
DEFAULT_DAYS = 4
DEFAULT_PACE_SECONDS = 6.0
MAX_API_ATTEMPTS = 6
RATE_LIMIT_BACKOFF_SECONDS = 12
RATE_LIMITED_CODE = 24
TIMEOUT_SECS = 60
MIN_NAME_KEY_LENGTH = 3
IPOS_INTERNAL_CODE_RE = re.compile(r"^E\d{8}[A-Z]$")
SG_ENTITY_ALIAS_RE = re.compile(r"\bpte\b\.?", re.IGNORECASE)

DATASETS = {
    "trademark": {
        "dataset_id": "d_6145acb2130bf781165258e76a584383",
        "fields": [
            "applicationNumber",
            "filingDate",
            "applicationType",
            "markStatus",
            "markStatusDate",
            "publicationDate",
            "lastModifiedDate",
            "markData_json",
            "goodsAndServicesSpecifications_json",
            "currentApplicantProprietorDetails_json",
        ],
    },
    "patent": {
        "dataset_id": "d_cb395225d1dcf91cc795f83e572c5e0e",
        "fields": [
            "applicationNumber",
            "applicationStatus",
            "applicationType",
            "filingDate",
            "lodgementDate",
            "titleOfInvention",
            "dateOfPublication",
            "lastModifiedDate",
            "applicants_json",
        ],
    },
}


class RateLimited(Exception):
    pass


def fetch_json(url: str) -> Any:
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(f"HTTP 429 for {url}") from e
        raise
    if isinstance(payload, dict) and payload.get("code") == RATE_LIMITED_CODE:
        raise RateLimited(f"rate limited: {payload.get('errorMsg') or payload.get('name')}")
    return payload


def search_url(dataset_id: str, fields: list[str], filters: dict[str, str], offset: int, limit: int) -> str:
    query = parse.urlencode(
        {
            "resource_id": dataset_id,
            "filters": json.dumps(filters, separators=(",", ":")),
            "fields": ",".join(fields),
            "limit": limit,
            "offset": offset,
        }
    )
    return f"{DATASTORE_URL}?{query}"


def record_url(dataset_id: str, application_number: str) -> str:
    query = parse.urlencode(
        {
            "resource_id": dataset_id,
            "filters": json.dumps({"applicationNumber": application_number}, separators=(",", ":")),
        }
    )
    return f"{DATASTORE_URL}?{query}"


def paced_get(
    url: str,
    fetcher: Callable[[str], Any],
    sleep: Callable[[float], None],
    pace_seconds: float,
) -> Any:
    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        sleep(pace_seconds)
        try:
            return fetcher(url)
        except RateLimited as e:
            print(f"ipos: {e} (attempt {attempt}/{MAX_API_ATTEMPTS}); backing off", file=sys.stderr)
            sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)
    raise RateLimited(f"still rate limited after {MAX_API_ATTEMPTS} attempts")


def fetch_day(
    kind: str,
    day: str,
    fetcher: Callable[[str], Any],
    sleep: Callable[[float], None],
    pace_seconds: float,
) -> list[dict[str, Any]]:
    spec = DATASETS[kind]
    records: list[dict[str, Any]] = []
    offset = 0
    page_size = PAGE_SIZE
    for _ in range(MAX_PAGES):
        url = search_url(spec["dataset_id"], spec["fields"], {"lastModifiedDate": day}, offset, page_size)
        try:
            body = paced_get(url, fetcher, sleep, pace_seconds)
        except error.HTTPError as e:
            if e.code != PAYLOAD_TOO_LARGE or page_size <= MIN_PAGE_SIZE:
                raise
            page_size = max(page_size // 2, MIN_PAGE_SIZE)
            print(f"ipos: {kind} {day} response too large; retrying with limit={page_size}", file=sys.stderr)
            continue
        if not isinstance(body, dict) or not body.get("success"):
            raise ValueError(f"unexpected response: {str(body)[:200]}")
        result = body["result"]
        page = result.get("records")
        if not isinstance(page, list):
            raise ValueError("records is not a list")
        records.extend(r for r in page if isinstance(r, dict))
        offset += len(page)
        if not page or offset >= int(result.get("total") or 0):
            break
    return records


def parse_json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
    else:
        return []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def _country_code(applicant: dict[str, Any]) -> str:
    country = applicant.get("countryOfIncorporationOrResidence")
    if isinstance(country, dict):
        return str(country.get("code") or "").strip().upper()
    return ""


def applicants(kind: str, record: dict[str, Any]) -> list[dict[str, str]]:
    column = "currentApplicantProprietorDetails_json" if kind == "trademark" else "applicants_json"
    result = []
    for entry in parse_json_list(record.get(column)):
        name = str(entry.get("name") or "").strip()
        uen = str(entry.get("uenCompanyCode") or "").strip().upper()
        if name or uen:
            result.append({"name": name, "uen": uen, "country": _country_code(entry)})
    return result


def _name_sources(company: dict[str, Any]) -> list[str]:
    aliases = [str(a) for a in company.get("aliases") or []]
    if str(company.get("country") or "").strip().lower() == SINGAPORE:
        return [str(company.get("name") or ""), *aliases]
    return [alias for alias in aliases if SG_ENTITY_ALIAS_RE.search(alias)]


class Watchlist:
    def __init__(self, companies: list[dict[str, Any]]):
        self.by_uen: dict[str, str] = {}
        self.pinned: set[str] = set()
        names: dict[str, set[str]] = {}
        for company in companies:
            name = company.get("name")
            if not name:
                continue
            uen = str((company.get("identifiers") or {}).get("uen") or "").strip().upper()
            if uen:
                self.by_uen[uen] = name
                self.pinned.add(name)
            for raw in _name_sources(company):
                key = normalise_name(raw)
                if len(key) >= MIN_NAME_KEY_LENGTH:
                    names.setdefault(key, set()).add(name)
        self.by_name = {key: next(iter(owners)) for key, owners in names.items() if len(owners) == 1}

    def match(self, applicant: dict[str, str]) -> tuple[str, str] | None:
        if applicant["uen"] in self.by_uen:
            return self.by_uen[applicant["uen"]], "uen"
        if applicant["country"] != SG_COUNTRY_CODE:
            return None
        company = self.by_name.get(normalise_name(applicant["name"]))
        if not company:
            return None
        carries_registry_uen = bool(applicant["uen"]) and not IPOS_INTERNAL_CODE_RE.match(applicant["uen"])
        if company in self.pinned and carries_registry_uen:
            return None
        return company, "name"


def _mark_words(record: dict[str, Any]) -> str:
    for entry in parse_json_list(record.get("markData_json")):
        words = str(entry.get("wordsInMark") or entry.get("chineseCharacter") or "").strip()
        if words:
            return words
        device = str(entry.get("descrOfDevice") or "").strip()
        if device:
            return f"device: {device}"
    return ""


def _classes(record: dict[str, Any]) -> list[str]:
    classes = []
    for entry in parse_json_list(record.get("goodsAndServicesSpecifications_json")):
        number = str(entry.get("classNum") or "").replace("Class", "").strip().lstrip("0")
        if number and number not in classes:
            classes.append(number)
    return classes


def _goods(record: dict[str, Any], limit: int = 5) -> str:
    descriptions = []
    for entry in parse_json_list(record.get("goodsAndServicesSpecifications_json")):
        for good in entry.get("goodsServices") or []:
            text = str((good or {}).get("itemDesc") or "").strip()
            if text:
                descriptions.append(text)
    shown = "; ".join(descriptions[:limit])
    return f"{shown}; …" if len(descriptions) > limit else shown


def event_date(record: dict[str, Any]) -> str:
    return str(record.get("lastModifiedDate") or "").strip()[:10]


def trademark_item(company: str, record: dict[str, Any], window_start: str) -> dict[str, Any]:
    number = str(record.get("applicationNumber") or "").strip()
    status = str(record.get("markStatus") or "").strip() or "unknown status"
    mark = _mark_words(record) or number
    classes = _classes(record)
    class_label = f" (class {', '.join(classes)})" if classes else ""
    filing = str(record.get("filingDate") or "").strip()[:10]
    if filing and filing >= window_start:
        headline = f"{company} files trademark '{mark}'{class_label}"
    else:
        headline = f"{company} trademark '{mark}'{class_label}: {status}"
    details = (
        f"IPOS trade mark {number} ({record.get('applicationType') or 'Trade Mark'}). "
        f"Status: {status} (as of {record.get('markStatusDate') or 'n/a'}); "
        f"filed {filing or 'n/a'}; published {record.get('publicationDate') or 'n/a'}; "
        f"register updated {record.get('lastModifiedDate') or 'n/a'}."
    )
    goods = _goods(record)
    if goods:
        details = f"{details} Goods/services: {goods}"
    return {
        "headline": headline,
        "description": details,
        "pubDate": event_date(record),
        "link": record_url(DATASETS["trademark"]["dataset_id"], number),
        "dedup_key": f"ipos://trademark/{number}/{slug_value(status)}",
    }


def patent_item(company: str, record: dict[str, Any], window_start: str) -> dict[str, Any]:
    number = str(record.get("applicationNumber") or "").strip()
    status = str(record.get("applicationStatus") or "").strip() or "unknown status"
    title = " ".join(str(record.get("titleOfInvention") or "").split()) or number
    lodged = str(record.get("lodgementDate") or record.get("filingDate") or "").strip()[:10]
    if lodged and lodged >= window_start:
        headline = f"{company} patent application: {title}"
    else:
        headline = f"{company} patent ({status}): {title}"
    details = (
        f"IPOS patent application {number} ({record.get('applicationType') or 'n/a'}). "
        f"Status: {status}; filed {record.get('filingDate') or 'n/a'}; "
        f"lodged {record.get('lodgementDate') or 'n/a'}; "
        f"published {record.get('dateOfPublication') or 'n/a'}; "
        f"register updated {record.get('lastModifiedDate') or 'n/a'}."
    )
    return {
        "headline": headline,
        "description": details,
        "pubDate": event_date(record),
        "link": record_url(DATASETS["patent"]["dataset_id"], number),
        "dedup_key": f"ipos://patent/{number}/{slug_value(status)}",
    }


def build_items(
    kind: str, records: list[dict[str, Any]], watchlist: Watchlist, window_start: str
) -> list[dict[str, Any]]:
    builder = trademark_item if kind == "trademark" else patent_item
    items = []
    for record in records:
        if not str(record.get("applicationNumber") or "").strip():
            continue
        matched: dict[str, str] = {}
        for applicant in applicants(kind, record):
            hit = watchlist.match(applicant)
            if hit:
                company, how = hit
                matched.setdefault(company, how)
        for company, how in sorted(matched.items()):
            item = builder(company, record, window_start)
            item.update(
                {
                    "company": company,
                    "source": SOURCE_LABEL,
                    "source_kind": "ipos",
                    "ipos_type": kind,
                    "matched_on": how,
                }
            )
            items.append(item)
    return items


def window_days(today: date, days: int) -> list[str]:
    return [(today - timedelta(days=offset)).isoformat() for offset in range(max(days, 1))]


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        unique.setdefault((item["company"], item["dedup_key"]), item)
    return sorted(unique.values(), key=lambda i: (i["pubDate"], i["company"], i["dedup_key"]), reverse=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def run(
    companies: list[dict[str, Any]],
    items_path: Path,
    days: int = DEFAULT_DAYS,
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    fetcher: Callable[[str], Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    dates = window_days(moment.astimezone(SGT).date(), days)
    window_start = min(dates)
    watchlist = Watchlist(companies)

    items: list[dict[str, Any]] = []
    fetches_ok = 0
    fetches_failed = 0
    records_scanned = 0
    for kind in DATASETS:
        for day in dates:
            try:
                records = fetch_day(kind, day, fetcher, sleep, pace_seconds)
            except (error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, RateLimited) as e:
                fetches_failed += 1
                print(f"ipos: {kind} {day} failed: {e}", file=sys.stderr)
                continue
            fetches_ok += 1
            records_scanned += len(records)
            items.extend(build_items(kind, records, watchlist, window_start))

    items = dedupe(items)
    all_failed = fetches_ok == 0
    write_json(
        items_path,
        {
            "generated_at": moment.isoformat(timespec="seconds"),
            "dates": dates,
            "items": items,
            "records_scanned": records_scanned,
            "fetches_ok": fetches_ok,
            "fetches_failed": fetches_failed,
            "companies_matched": len({i["company"] for i in items}),
            "error_rate_alarm": all_failed,
        },
    )
    print(
        f"ipos: dates={dates[-1]}..{dates[0]} records={records_scanned} fetches_ok={fetches_ok} "
        f"fetches_failed={fetches_failed} items={len(items)} "
        f"companies={len({i['company'] for i in items})}",
        file=sys.stderr,
    )
    if all_failed:
        print("::warning::ipos: every IPOS datastore fetch failed; the patent/trademark delta is starved", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Singapore calendar days to read, ending today")
    parser.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_SECONDS)
    parser.add_argument("--out", type=Path, default=ITEMS_FILE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    return run(companies, args.out, days=args.days, pace_seconds=args.pace_seconds)


if __name__ == "__main__":
    sys.exit(main())
