#!/usr/bin/env python3
"""SEC EDGAR daily form index delta for the watchlist.

Reads https://www.sec.gov/Archives/edgar/daily-index/<year>/QTR<n>/form.<YYYYMMDD>.idx
for each of the last --days calendar days (UTC) so a missed run is caught up,
keeps Form D, S-1, F-1 and F-4 filings (and their /A amendments), and matches
filer names to watchlist companies. A filer matches by identifiers.cik first;
otherwise by an exact normalised name or alias key (legal suffixes such as
Inc/Corp/LLC/Holdings stripped) that maps to exactly one watchlist company and
whose source name is distinctive (entity_terms.term_is_strong). A company that
carries a cik pin matches by CIK only, so a namesake filer is never attached to
it. Never a containment match.

SEC requires a declared contact User-Agent. It is read from SEC_EDGAR_USER_AGENT
(form: "Name contact@domain"); when unset the script prints a notice, writes no
items and exits 0 so forks and local runs are unaffected.

Matched filings become candidate-like items in data/edgar-items.json, which
collect-candidates.py folds into data/candidates.json for the Layer 1 pass.

SEC answers 403 (not 404) for an index file that does not exist, so weekend
days are never requested and a weekday 403 (holiday, today not yet published,
or a rejected User-Agent) counts as a failed day. Fails open: a failed day is
logged and skipped; the script exits 1 only when every requested day failed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entity_terms import term_is_strong  # noqa: E402
from startupsg_snapshot import LEGAL_SUFFIX_RE, NON_ALNUM_RE  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
ITEMS_FILE = REPO_ROOT / "data" / "edgar-items.json"

USER_AGENT_ENV = "SEC_EDGAR_USER_AGENT"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{stamp}.idx"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.htm"
SOURCE_LABEL = "SEC EDGAR"
DEFAULT_DAYS = 5
DEFAULT_PACE_SECONDS = 0.5
TIMEOUT_SECS = 60
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}
MIN_NAME_KEY_LENGTH = 3
WEEKEND_START = 5

FORM_LABELS = {
    "D": "Form D (notice of exempt securities offering)",
    "S-1": "Form S-1 (IPO registration statement)",
    "F-1": "Form F-1 (foreign issuer IPO registration statement)",
    "F-4": "Form F-4 (foreign issuer business-combination registration statement)",
}
LINE_RE = re.compile(r"^(?P<form>\S.*?)\s{2,}(?P<name>.+?)\s+(?P<cik>\d+)\s+(?P<filed>\d{8})\s+(?P<path>edgar/\S+)\s*$")
EXTRA_SUFFIX_RE = re.compile(
    r"\b(l\.?l\.?c\.?|l\.?p\.?|holdings?|co\.?|company|corporation|incorporated|plc|gmbh|ag|s\.?a\.?|"
    r"pte\.?\s*ltd\.?|ltd\.?|limited|inc\.?|corp\.?)\s*$",
    re.IGNORECASE,
)


class IndexMissing(Exception):
    pass


def normalise_filer_name(name: str) -> str:
    stripped = (name or "").strip()
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = LEGAL_SUFFIX_RE.sub("", stripped).strip(" .,")
        stripped = EXTRA_SUFFIX_RE.sub("", stripped).strip(" .,")
    return NON_ALNUM_RE.sub("", stripped.lower())


def base_form(form: str) -> str:
    return form[:-2] if form.endswith("/A") else form


def make_fetcher(user_agent: str) -> Callable[[str], str]:
    def fetch(url: str) -> str:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                raw = resp.read()
        except error.HTTPError as e:
            if e.code == 404:
                raise IndexMissing(url) from e
            raise
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")

    return fetch


def index_url(day: date) -> str:
    return INDEX_URL.format(year=day.year, quarter=(day.month - 1) // 3 + 1, stamp=day.strftime("%Y%m%d"))


def fetch_index(
    day: date,
    fetcher: Callable[[str], str],
    sleep: Callable[[float], None],
    pace_seconds: float,
) -> str:
    url = index_url(day)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        sleep(pace_seconds)
        try:
            return fetcher(url)
        except error.HTTPError as e:
            if e.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            print(f"edgar: HTTP {e.code} for {url} (attempt {attempt}/{MAX_ATTEMPTS}); backing off", file=sys.stderr)
            sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError("unreachable")


def parse_index(text: str, wanted: set[str]) -> list[dict[str, str]]:
    filings = []
    for line in text.splitlines():
        found = LINE_RE.match(line)
        if not found:
            continue
        form = found["form"].strip()
        if base_form(form) not in wanted:
            continue
        accession = found["path"].rsplit("/", 1)[-1].removesuffix(".txt")
        filings.append(
            {
                "form": form,
                "name": found["name"].strip(),
                "cik": found["cik"].lstrip("0") or "0",
                "filed": f"{found['filed'][:4]}-{found['filed'][4:6]}-{found['filed'][6:]}",
                "accession": accession,
            }
        )
    return filings


def filing_link(cik: str, accession: str) -> str:
    return FILING_INDEX_URL.format(cik=cik, folder=accession.replace("-", ""), accession=accession)


class Watchlist:
    def __init__(self, companies: list[dict[str, Any]]):
        self.by_cik: dict[str, str] = {}
        owners: dict[str, set[str]] = {}
        for company in companies:
            name = company.get("name")
            if not name:
                continue
            cik = str((company.get("identifiers") or {}).get("cik") or "").strip().lstrip("0")
            if cik:
                self.by_cik[cik] = name
                continue
            for raw in [name, *(str(a) for a in company.get("aliases") or [])]:
                if not term_is_strong(raw):
                    continue
                key = normalise_filer_name(raw)
                if len(key) >= MIN_NAME_KEY_LENGTH:
                    owners.setdefault(key, set()).add(name)
        self.by_name = {key: next(iter(names)) for key, names in owners.items() if len(names) == 1}

    def match(self, filing: dict[str, str]) -> tuple[str, str] | None:
        if filing["cik"] in self.by_cik:
            return self.by_cik[filing["cik"]], "cik"
        company = self.by_name.get(normalise_filer_name(filing["name"]))
        return (company, "name") if company else None


def build_item(company: str, how: str, filing: dict[str, str]) -> dict[str, Any]:
    label = FORM_LABELS[base_form(filing["form"])]
    amended = " amendment" if filing["form"].endswith("/A") else ""
    return {
        "company": company,
        "headline": f"{company} files {label}{amended}",
        "description": (
            f"SEC EDGAR filing {filing['accession']}: {filing['form']} by {filing['name']} "
            f"(CIK {filing['cik']}), filed {filing['filed']}."
        ),
        "pubDate": filing["filed"],
        "link": filing_link(filing["cik"], filing["accession"]),
        "dedup_key": f"edgar://{filing['accession']}",
        "source": SOURCE_LABEL,
        "source_kind": "edgar",
        "edgar_form": filing["form"],
        "cik": filing["cik"],
        "matched_on": how,
    }


def window_days(today: date, days: int) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(max(days, 1))]


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
    fetcher: Callable[[str], str],
    days: int = DEFAULT_DAYS,
    pace_seconds: float = DEFAULT_PACE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    window = window_days(moment.date(), days)
    watchlist = Watchlist(companies)
    wanted = set(FORM_LABELS)

    items: list[dict[str, Any]] = []
    days_ok = 0
    days_failed = 0
    days_missing = 0
    days_weekend = 0
    filings_scanned = 0
    for day in window:
        if day.weekday() >= WEEKEND_START:
            days_weekend += 1
            continue
        try:
            text = fetch_index(day, fetcher, sleep, pace_seconds)
        except IndexMissing:
            days_ok += 1
            days_missing += 1
            continue
        except (error.URLError, TimeoutError, OSError, ValueError) as e:
            days_failed += 1
            print(f"edgar: {day.isoformat()} failed: {e}", file=sys.stderr)
            continue
        days_ok += 1
        for filing in parse_index(text, wanted):
            filings_scanned += 1
            hit = watchlist.match(filing)
            if hit:
                items.append(build_item(hit[0], hit[1], filing))

    items = dedupe(items)
    all_failed = days_ok == 0 and days_failed > 0
    write_json(
        items_path,
        {
            "generated_at": moment.isoformat(timespec="seconds"),
            "dates": [d.isoformat() for d in window],
            "items": items,
            "filings_scanned": filings_scanned,
            "days_ok": days_ok,
            "days_missing": days_missing,
            "days_weekend": days_weekend,
            "days_failed": days_failed,
            "companies_matched": len({i["company"] for i in items}),
            "error_rate_alarm": all_failed,
        },
    )
    print(
        f"edgar: dates={window[-1]}..{window[0]} filings={filings_scanned} days_ok={days_ok} "
        f"days_missing={days_missing} days_failed={days_failed} items={len(items)} "
        f"companies={len({i['company'] for i in items})}",
        file=sys.stderr,
    )
    if all_failed:
        print("::warning::edgar: every daily index fetch failed; the SEC filing delta is starved", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="UTC calendar days to read, ending today")
    parser.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_SECONDS)
    parser.add_argument("--out", type=Path, default=ITEMS_FILE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    user_agent = os.environ.get(USER_AGENT_ENV, "").strip()
    if not user_agent:
        print(f"edgar: {USER_AGENT_ENV} is not set; SEC requires a declared contact User-Agent, skipping", file=sys.stderr)
        return 0
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    return run(companies, args.out, make_fetcher(user_agent), days=args.days, pace_seconds=args.pace_seconds)


if __name__ == "__main__":
    sys.exit(main())
