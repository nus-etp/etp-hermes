#!/usr/bin/env python3
"""Singapore government procurement awards (GeBIZ) won by watchlist companies.

Reads the keyless data.gov.sg datastore copy of "Government Procurement via
GeBIZ" (Ministry of Finance, ~18k award rows with tender number, description,
agency, award date, supplier name and amount; no UEN column). Supplier names
are dirty legal names, so a supplier counts as a watchlist company only when
its normalised name (startupsg_snapshot.normalise_name) equals the normalised
name or alias of exactly one Singapore watchlist company, or of a Pte alias of
a company filed under another country (Softgridinc is listed as China). The
source term must be strong per entity_terms.term_is_strong: a bare generic
name such as Aleph or AiRTS would match namesake suppliers (the GeBIZ "Aleph"
is a UX consultancy, not the process-AI startup). No containment, no guessing.

The matched awards are kept in the committed snapshot
data/gebiz/watchlist-awards.json ({dedup_key: first_seen}). An award absent
from the snapshot and dated within --window-days becomes a candidate-like item
in data/gebiz-items.json, which collect-candidates.py folds into
data/candidates.json for the normal Layer 1 relevance pass. The dataset is
republished in bulk (annually, with occasional refreshes), so the workflow runs
this weekly; the snapshot, not the cadence, prevents re-emitting an award, and
the window keeps the first run (no snapshot) from emitting the whole backlog.

Fails open: a failed or malformed fetch leaves the snapshot untouched and
writes no items; the script exits 1 only when the dataset could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entity_terms import term_is_strong  # noqa: E402
from startupsg_snapshot import normalise_name, slug_value  # noqa: E402
from user_agents import honest_ua  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
SNAPSHOT_FILE = REPO_ROOT / "data" / "gebiz" / "watchlist-awards.json"
ITEMS_FILE = REPO_ROOT / "data" / "gebiz-items.json"

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
DATASET_ID = "d_acde1106003906a75c3fa052592f2fcb"
FIELDS = ["tender_no", "tender_description", "agency", "award_date", "tender_detail_status", "supplier_name", "awarded_amt"]
USER_AGENT = honest_ua("gebiz-awards")
SOURCE_LABEL = "GeBIZ (data.gov.sg)"
SINGAPORE = "singapore"
PAGE_SIZE = 5000
MIN_PAGE_SIZE = 500
MAX_PAGES = 40
PAYLOAD_TOO_LARGE = 413
DEFAULT_WINDOW_DAYS = 730
DEFAULT_PACE_SECONDS = 6.0
MAX_API_ATTEMPTS = 6
RATE_LIMIT_BACKOFF_SECONDS = 12
RATE_LIMITED_CODE = 24
TIMEOUT_SECS = 90
MIN_NAME_KEY_LENGTH = 3
SG_ENTITY_ALIAS_RE = re.compile(r"\bpte\b\.?", re.IGNORECASE)
MAX_DESCRIPTION_CHARS = 200


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


def search_url(offset: int, limit: int) -> str:
    query = parse.urlencode(
        {"resource_id": DATASET_ID, "fields": ",".join(FIELDS), "limit": limit, "offset": offset}
    )
    return f"{DATASTORE_URL}?{query}"


def record_url(tender_no: str, supplier_name: str) -> str:
    filters = json.dumps({"tender_no": tender_no, "supplier_name": supplier_name}, separators=(",", ":"))
    return f"{DATASTORE_URL}?{parse.urlencode({'resource_id': DATASET_ID, 'filters': filters})}"


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
            print(f"gebiz: {e} (attempt {attempt}/{MAX_API_ATTEMPTS}); backing off", file=sys.stderr)
            sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)
    raise RateLimited(f"still rate limited after {MAX_API_ATTEMPTS} attempts")


def fetch_all(
    fetcher: Callable[[str], Any],
    sleep: Callable[[float], None],
    pace_seconds: float,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    page_size = PAGE_SIZE
    for _ in range(MAX_PAGES):
        try:
            body = paced_get(search_url(offset, page_size), fetcher, sleep, pace_seconds)
        except error.HTTPError as e:
            if e.code != PAYLOAD_TOO_LARGE or page_size <= MIN_PAGE_SIZE:
                raise
            page_size = max(page_size // 2, MIN_PAGE_SIZE)
            print(f"gebiz: response too large; retrying with limit={page_size}", file=sys.stderr)
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
            return records
    raise ValueError(f"dataset exceeds {MAX_PAGES} pages")


def _name_sources(company: dict[str, Any]) -> list[str]:
    aliases = [str(a) for a in company.get("aliases") or []]
    if str(company.get("country") or "").strip().lower() == SINGAPORE:
        return [str(company.get("name") or ""), *aliases]
    return [alias for alias in aliases if SG_ENTITY_ALIAS_RE.search(alias)]


class Watchlist:
    def __init__(self, companies: list[dict[str, Any]]):
        names: dict[str, set[str]] = {}
        for company in companies:
            name = company.get("name")
            if not name:
                continue
            for raw in _name_sources(company):
                key = normalise_name(raw)
                if len(key) >= MIN_NAME_KEY_LENGTH and term_is_strong(raw):
                    names.setdefault(key, set()).add(name)
        self.by_name = {key: next(iter(owners)) for key, owners in names.items() if len(owners) == 1}

    def match(self, supplier_name: str) -> str | None:
        return self.by_name.get(normalise_name(supplier_name))


def parse_award_date(value: Any) -> date | None:
    parts = str(value or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(part) for part in parts)
        return date(year, month, day)
    except ValueError:
        return None


def parse_amount(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def format_amount(amount: float | None) -> str:
    if amount is None:
        return "amount not disclosed"
    if amount >= 1_000_000:
        return f"S${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"S${amount / 1_000:.0f}K"
    return f"S${amount:,.0f}"


def clean_description(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"


def build_award(company: str, record: dict[str, Any], awarded_on: date) -> dict[str, Any]:
    tender_no = str(record.get("tender_no") or "").strip()
    supplier = str(record.get("supplier_name") or "").strip()
    agency = " ".join(str(record.get("agency") or "").split()) or "a Singapore government agency"
    description = clean_description(record.get("tender_description")) or tender_no
    amount = parse_amount(record.get("awarded_amt"))
    amount_label = format_amount(amount)
    details = (
        f"GeBIZ tender {tender_no} awarded {awarded_on.isoformat()} to {supplier} by {agency}. "
        f"Status: {record.get('tender_detail_status') or 'n/a'}; awarded amount: {amount_label}. "
        f"Tender description: {description}"
    )
    return {
        "company": company,
        "headline": f"{company} awarded {agency} tender: {description} ({amount_label})",
        "description": details,
        "pubDate": awarded_on.isoformat(),
        "link": record_url(tender_no, supplier),
        "dedup_key": f"gebiz://{tender_no}/{slug_value(normalise_name(supplier))}",
        "source": SOURCE_LABEL,
        "source_kind": "gebiz",
        "agency": agency,
        "awarded_amt": amount,
    }


def matched_awards(records: list[dict[str, Any]], watchlist: Watchlist) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        tender_no = str(record.get("tender_no") or "").strip()
        awarded_on = parse_award_date(record.get("award_date"))
        company = watchlist.match(str(record.get("supplier_name") or ""))
        if not tender_no or not awarded_on or not company:
            continue
        award = build_award(company, record, awarded_on)
        unique.setdefault(award["dedup_key"], award)
    return sorted(unique.values(), key=lambda a: (a["pubDate"], a["company"], a["dedup_key"]), reverse=True)


def load_snapshot(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"gebiz: unreadable snapshot {path}: {e}", file=sys.stderr)
        return None
    awards = data.get("awards") if isinstance(data, dict) else None
    return dict(awards) if isinstance(awards, dict) else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def run(
    companies: list[dict[str, Any]],
    snapshot_path: Path,
    items_path: Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    fetcher: Callable[[str], Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    today = moment.date()
    try:
        records = fetch_all(fetcher, sleep, pace_seconds)
    except (error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, RateLimited) as e:
        print(f"gebiz: dataset fetch failed: {e}", file=sys.stderr)
        print("::warning::gebiz: GeBIZ awards dataset unreachable; the procurement delta is starved", file=sys.stderr)
        return 1

    awards = matched_awards(records, Watchlist(companies))
    previous = load_snapshot(snapshot_path)
    known = previous or {}
    fresh = [a for a in awards if a["dedup_key"] not in known]
    items = [a for a in fresh if (today - date.fromisoformat(a["pubDate"])).days <= window_days]

    snapshot = dict(known)
    for award in fresh:
        snapshot[award["dedup_key"]] = today.isoformat()
    write_json(snapshot_path, {"awards": snapshot})
    write_json(
        items_path,
        {
            "generated_at": moment.isoformat(timespec="seconds"),
            "items": items,
            "records_scanned": len(records),
            "awards_matched": len(awards),
            "seeded": previous is None,
            "companies_matched": len({a["company"] for a in awards}),
        },
    )
    print(
        f"gebiz: records={len(records)} matched={len(awards)} new={len(fresh)} items={len(items)} "
        f"companies={len({a['company'] for a in awards})} seeded={previous is None}",
        file=sys.stderr,
    )
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS, help="only emit awards dated within this many days")
    parser.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_SECONDS)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_FILE)
    parser.add_argument("--out", type=Path, default=ITEMS_FILE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    return run(companies, args.snapshot, args.out, window_days=args.window_days, pace_seconds=args.pace_seconds)


if __name__ == "__main__":
    sys.exit(main())
