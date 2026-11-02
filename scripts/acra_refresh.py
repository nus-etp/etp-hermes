#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_PATH = REPO_ROOT / "data" / "companies.json"
OUTPUT_CSV = REPO_ROOT / "data" / "acra" / "watchlist-entities.csv"
CHANGES_MD = REPO_ROOT / "data" / "acra" / "changes.md"

COLLECTION_ID = "2"
EXPECTED_DATASET_COUNT = 27
METADATA_URL = "https://api-production.data.gov.sg/v2/public/api/collections/{collection_id}/metadata"
INITIATE_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/initiate-download"
POLL_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"
USER_AGENT = "hermeneut-acra-refresh/1.0 (+https://github.com/nus-etp/etp-hermes)"
RATE_LIMITED_CODE = 24
RATE_LIMIT_BACKOFF_SECONDS = 12
MAX_API_ATTEMPTS = 10
MAX_POLL_ATTEMPTS = 30
DEFAULT_PACE_SECONDS = 6.0
DEFAULT_MIN_SOURCE_ROWS = 1_500_000
MIN_NAME_KEY_LENGTH = 3

SOURCE_COLUMNS = [
    "uen", "issuance_agency_id", "entity_name", "entity_type_description",
    "business_constitution_description", "company_type_description",
    "paf_constitution_description", "entity_status_description",
    "registration_incorporation_date", "uen_issue_date", "address_type", "block",
    "street_name", "level_no", "unit_no", "building_name", "postal_code",
    "other_address_line1", "other_address_line2", "account_due_date", "annual_return_date",
    "primary_ssic_code", "primary_ssic_description", "primary_user_described_activity",
    "secondary_ssic_code", "secondary_ssic_description", "secondary_user_described_activity",
    "no_of_officers",
    *[f"former_entity_name{index}" for index in range(1, 16)],
    *[name for index in range(1, 6) for name in (f"uen_of_audit_firm{index}", f"name_of_audit_firm{index}")],
]
FORMER_NAME_COLUMNS = [f"former_entity_name{index}" for index in range(1, 16)]

OUTPUT_COLUMNS = [
    "uen",
    "entity_name",
    "former_names",
    "entity_status",
    "annual_return_date",
    "registration_date",
    "entity_type",
    "primary_ssic_code",
    "primary_ssic_description",
    "postal_code",
    "matched_company",
    "match",
]

DEAD_STATUS_PREFIXES = (
    "Struck Off",
    "Gazetted To Be Struck Off",
    "In Liquidation",
    "Dissolved",
    "Terminated",
    "Cancelled",
    "Cancellation In Progress",
    "Ceased Registration",
    "To Be Ceased",
    "Removed",
    "Registration expired",
)
HIGHLIGHT_STATUS_PREFIXES = ("Struck Off", "Gazetted To Be Struck Off", "In Liquidation", "Dissolved")

LEGAL_SUFFIX_RE = re.compile(
    r"\b(PTE\.?|PRIVATE|LTD\.?|LIMITED|LLP|INC\.?|CORP\.?|CORPORATION|CO\.?|SINGAPORE)\b"
)


class AcraError(Exception):
    pass


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def clean(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value.lower() == "na" else value


def name_key(name: str) -> str:
    text = name.upper().replace("&", " AND ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = LEGAL_SUFFIX_RE.sub(" ", text)
    return re.sub(r"[^A-Z0-9]", "", text)


def is_live(status: str) -> bool:
    return status.startswith("Live")


def is_dead(status: str) -> bool:
    return status.startswith(DEAD_STATUS_PREFIXES)


def is_highlight_death(status: str) -> bool:
    return status.startswith(HIGHLIGHT_STATUS_PREFIXES)


class Watchlist:
    def __init__(self, companies: list[dict]):
        self.uen_to_companies: dict[str, list[str]] = {}
        self.name_to_companies: dict[str, list[str]] = {}
        self.pinned_uen_by_company: dict[str, str] = {}
        self.inactive_companies: set[str] = set()
        for company in companies:
            name = company["name"]
            if company.get("status") == "inactive":
                self.inactive_companies.add(name)
            uen = ((company.get("identifiers") or {}).get("uen") or "").strip().upper()
            if uen:
                self.uen_to_companies.setdefault(uen, []).append(name)
                self.pinned_uen_by_company[name] = uen
                continue
            if company.get("country") != "Singapore":
                continue
            for label in [name, *company.get("aliases", [])]:
                key = name_key(label)
                if len(key) < MIN_NAME_KEY_LENGTH:
                    continue
                matched = self.name_to_companies.setdefault(key, [])
                if name not in matched:
                    matched.append(name)

    def match(self, row: dict) -> tuple[list[str], str] | None:
        uen = clean(row.get("uen")).upper()
        if uen in self.uen_to_companies:
            return self.uen_to_companies[uen], "uen"
        companies = self.name_to_companies.get(name_key(clean(row.get("entity_name"))))
        if companies:
            return companies, "name"
        for column in FORMER_NAME_COLUMNS:
            former = clean(row.get(column))
            if former:
                companies = self.name_to_companies.get(name_key(former))
                if companies:
                    return companies, "former_name"
        return None


def project_row(row: dict, companies: list[str], match_kind: str) -> dict:
    former_names = [clean(row.get(column)) for column in FORMER_NAME_COLUMNS]
    return {
        "uen": clean(row.get("uen")).upper(),
        "entity_name": clean(row.get("entity_name")),
        "former_names": "; ".join(name for name in former_names if name),
        "entity_status": clean(row.get("entity_status_description")),
        "annual_return_date": clean(row.get("annual_return_date")),
        "registration_date": clean(row.get("registration_incorporation_date")),
        "entity_type": clean(row.get("entity_type_description")),
        "primary_ssic_code": clean(row.get("primary_ssic_code")),
        "primary_ssic_description": clean(row.get("primary_ssic_description")),
        "postal_code": clean(row.get("postal_code")),
        "matched_company": "; ".join(sorted(companies, key=str.casefold)),
        "match": match_kind,
    }


def check_header(header: Sequence[str] | None, source: str) -> None:
    normalised = [column.strip() for column in header or []]
    if normalised != SOURCE_COLUMNS:
        raise AcraError(
            f"{source}: header does not match the expected {len(SOURCE_COLUMNS)} columns: {normalised}"
        )


def filter_csv(path: Path, watchlist: Watchlist, matched: dict[str, dict]) -> int:
    csv.field_size_limit(sys.maxsize)
    row_count = 0
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        check_header(reader.fieldnames, path.name)
        for row in reader:
            row_count += 1
            result = watchlist.match(row)
            if result is None:
                continue
            projected = project_row(row, *result)
            if projected["uen"]:
                matched[projected["uen"]] = projected
    return row_count


def sort_rows(rows) -> list[dict]:
    return sorted(rows, key=lambda row: (row["matched_company"].casefold(), row["uen"]))


def filter_directory(directory: Path, watchlist: Watchlist) -> tuple[list[dict], int]:
    paths = sorted(directory.glob("*.csv"))
    if not paths:
        raise AcraError(f"{directory}: no CSV files found")
    matched: dict[str, dict] = {}
    total = 0
    for path in paths:
        total += filter_csv(path, watchlist, matched)
    return sort_rows(matched.values()), total


def request_for(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def api_get(url: str, pace_seconds: float, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        sleep(pace_seconds)
        try:
            with opener(request_for(url), timeout=60) as response:
                payload: dict = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise AcraError(f"GET {url} failed: HTTP {error.code}") from error
            payload = {"code": RATE_LIMITED_CODE}
        if payload.get("code") == RATE_LIMITED_CODE:
            log(f"  rate limited (attempt {attempt}/{MAX_API_ATTEMPTS}); backing off")
            sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)
            continue
        if payload.get("code") != 0:
            raise AcraError(f"GET {url} returned code {payload.get('code')}: {payload.get('errorMsg')}")
        return payload["data"]
    raise AcraError(f"GET {url} still rate limited after {MAX_API_ATTEMPTS} attempts")


def fetch_collection_metadata(pace_seconds: float, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
    data = api_get(METADATA_URL.format(collection_id=COLLECTION_ID), pace_seconds, opener, sleep)
    metadata = data["collectionMetadata"]
    dataset_ids = metadata.get("childDatasets") or []
    if len(dataset_ids) != EXPECTED_DATASET_COUNT:
        raise AcraError(
            f"collection {COLLECTION_ID} lists {len(dataset_ids)} datasets, expected {EXPECTED_DATASET_COUNT}"
        )
    return metadata


def resolve_download_url(dataset_id: str, pace_seconds: float, opener=urllib.request.urlopen, sleep=time.sleep) -> str:
    initiated = api_get(INITIATE_URL.format(dataset_id=dataset_id), pace_seconds, opener, sleep)
    if initiated.get("url"):
        return initiated["url"]
    for _ in range(MAX_POLL_ATTEMPTS):
        polled = api_get(POLL_URL.format(dataset_id=dataset_id), pace_seconds, opener, sleep)
        if polled.get("url"):
            return polled["url"]
    raise AcraError(f"{dataset_id}: download not ready after {MAX_POLL_ATTEMPTS} polls")


def stream_to_file(url: str, destination: Path, opener=urllib.request.urlopen) -> int:
    with opener(request_for(url), timeout=600) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1 << 20)
    return destination.stat().st_size


def download_and_filter(
    watchlist: Watchlist,
    pace_seconds: float,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
) -> tuple[list[dict], int, str]:
    metadata = fetch_collection_metadata(pace_seconds, opener, sleep)
    snapshot_date = str(metadata.get("lastUpdatedAt") or "")[:10]
    log(f"collection {COLLECTION_ID} lastUpdatedAt={metadata.get('lastUpdatedAt')}")
    matched: dict[str, dict] = {}
    total = 0
    with tempfile.TemporaryDirectory(prefix="acra-") as temp_dir:
        for position, dataset_id in enumerate(metadata["childDatasets"], start=1):
            path = Path(temp_dir) / f"{dataset_id}.csv"
            size = stream_to_file(resolve_download_url(dataset_id, pace_seconds, opener, sleep), path, opener)
            rows = filter_csv(path, watchlist, matched)
            total += rows
            path.unlink()
            log(f"  [{position}/{EXPECTED_DATASET_COUNT}] {dataset_id}: {size:,} bytes, {rows:,} rows")
    return sort_rows(matched.values()), total, snapshot_date


def read_previous(path: Path) -> dict[str, dict] | None:
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["uen"]: row for row in csv.DictReader(handle)}


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def diff_rows(previous: dict[str, dict], current: list[dict]) -> dict[str, list]:
    changes: dict[str, list] = {
        "deaths": [],
        "status": [],
        "renames": [],
        "address": [],
        "ssic": [],
        "new": [],
        "removed": [],
    }
    current_by_uen = {row["uen"]: row for row in current}
    for row in current:
        old = previous.get(row["uen"])
        if old is None:
            changes["new"].append(row)
            continue
        if old["entity_status"] != row["entity_status"]:
            bucket = "deaths" if is_live(old["entity_status"]) and is_highlight_death(row["entity_status"]) else "status"
            changes[bucket].append((old, row))
        if old["entity_name"] != row["entity_name"]:
            changes["renames"].append((old, row))
        if old["postal_code"] != row["postal_code"]:
            changes["address"].append((old, row))
        if old["primary_ssic_code"] != row["primary_ssic_code"]:
            changes["ssic"].append((old, row))
    for uen, old in previous.items():
        if uen not in current_by_uen:
            changes["removed"].append(old)
    return changes


def find_auto_inactive(previous: dict[str, dict] | None, current: list[dict], watchlist: Watchlist) -> list[tuple[str, dict]]:
    if not previous:
        return []
    current_by_uen = {row["uen"]: row for row in current}
    applied = []
    for company, uen in sorted(watchlist.pinned_uen_by_company.items(), key=lambda item: item[0].casefold()):
        if company in watchlist.inactive_companies:
            continue
        old = previous.get(uen)
        new = current_by_uen.get(uen)
        if old is None or new is None:
            continue
        if is_live(old["entity_status"]) and is_dead(new["entity_status"]):
            applied.append((company, new))
    return applied


def find_pinned_dead_unapplied(current: list[dict], watchlist: Watchlist, applied: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    applied_names = {company for company, _ in applied}
    current_by_uen = {row["uen"]: row for row in current}
    proposals = []
    for company, uen in sorted(watchlist.pinned_uen_by_company.items(), key=lambda item: item[0].casefold()):
        if company in watchlist.inactive_companies or company in applied_names:
            continue
        row = current_by_uen.get(uen)
        if row and is_dead(row["entity_status"]):
            proposals.append((company, row))
    return proposals


def status_reason(row: dict, snapshot_date: str) -> str:
    label = f"{snapshot_date} snapshot" if snapshot_date else "ACRA snapshot"
    return f"ACRA: {row['entity_status']} ({label})"


def mark_inactive(text: str, company_name: str, reason: str) -> str:
    name_line = f'    "name": {json.dumps(company_name, ensure_ascii=False)},\n'
    start = text.find(name_line)
    if start < 0:
        raise AcraError(f"{company_name}: entry not found in companies.json")
    end = text.find("\n  }", start)
    entry = text[start:end]
    if '\n    "status":' in entry:
        return text
    country_match = re.search(r'\n    "country": [^\n]*\n', entry)
    anchor = country_match.end() if country_match else len(name_line)
    insert = (
        '    "status": "inactive",\n'
        f'    "status_reason": {json.dumps(reason, ensure_ascii=False)},\n'
    )
    position = start + anchor
    return text[:position] + insert + text[position:]


def apply_inactive(companies_path: Path, applied: list[tuple[str, dict]], snapshot_date: str) -> None:
    if not applied:
        return
    text = companies_path.read_text(encoding="utf-8")
    for company, row in applied:
        text = mark_inactive(text, company, status_reason(row, snapshot_date))
    parsed = {entry["name"]: entry for entry in json.loads(text)}
    for company, _ in applied:
        if parsed[company].get("status") != "inactive":
            raise AcraError(f"{company}: failed to mark inactive")
    companies_path.write_text(text, encoding="utf-8")


def describe(row: dict) -> str:
    return f"`{row['uen']}` {row['entity_name']} → **{row['matched_company']}** ({row['match']})"


def render_report(
    snapshot_date: str,
    previous: dict[str, dict] | None,
    current: list[dict],
    total_rows: int,
    applied: list[tuple[str, dict]],
    unapplied: list[tuple[str, dict]],
) -> str:
    matched_companies = {name for row in current for name in row["matched_company"].split("; ")}
    lines = [
        "# ACRA watchlist refresh",
        "",
        f"- Snapshot: {snapshot_date or 'unknown'} (data.gov.sg collection {COLLECTION_ID})",
        f"- Source rows scanned: {total_rows:,}",
        f"- Matched entities: {len(current)} across {len(matched_companies)} watchlist companies",
        "",
    ]
    if previous is None:
        non_live = [row for row in current if not is_live(row["entity_status"])]
        lines += [
            "Baseline snapshot: no previous `watchlist-entities.csv` to diff against.",
            "",
            f"{len(non_live)} matched entities are not Live; "
            f"{sum(1 for row in non_live if row['match'] == 'uen')} of them are UEN-pinned.",
            "",
        ]
        return "\n".join(lines)
    changes = diff_rows(previous, current)

    def section(title: str, items: list[str]) -> None:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        lines.extend(items or ["_none_"])
        lines.append("")

    section(
        "Applied to companies.json: marked inactive",
        [f"- **{company}**: `{row['uen']}` {row['entity_status']}" for company, row in applied],
    )
    section(
        "Live → Struck Off / Liquidation / Dissolved",
        [f"- {describe(new)}: {old['entity_status']} → **{new['entity_status']}**" for old, new in changes["deaths"]],
    )
    section(
        "Other status changes",
        [f"- {describe(new)}: {old['entity_status'] or '—'} → {new['entity_status'] or '—'}" for old, new in changes["status"]],
    )
    section(
        "Renames (consider adopting the new name or adding an alias)",
        [f"- {describe(new)}: was {old['entity_name']}" for old, new in changes["renames"]],
    )
    section(
        "Address changes (postal code)",
        [f"- {describe(new)}: {old['postal_code'] or '—'} → {new['postal_code'] or '—'}" for old, new in changes["address"]],
    )
    section(
        "Primary SSIC changes",
        [
            f"- {describe(new)}: {old['primary_ssic_code'] or '—'} → {new['primary_ssic_code'] or '—'}"
            f" {new['primary_ssic_description']}".rstrip()
            for old, new in changes["ssic"]
        ],
    )
    section(
        "Newly matched entities (name matches are proposals only; pin `identifiers.uen` to confirm)",
        [f"- {describe(row)}: {row['entity_status']}, registered {row['registration_date'] or '—'}" for row in changes["new"]],
    )
    section(
        "No longer matched",
        [f"- {describe(row)}" for row in changes["removed"]],
    )
    section(
        "UEN-pinned companies with a non-Live record not auto-applied (review manually)",
        [f"- **{company}**: `{row['uen']}` {row['entity_status']}" for company, row in unapplied],
    )
    return "\n".join(lines)


def run(
    rows: list[dict],
    total_rows: int,
    snapshot_date: str,
    watchlist: Watchlist,
    output_csv: Path,
    changes_md: Path,
    companies_path: Path,
    min_source_rows: int,
) -> list[tuple[str, dict]]:
    if total_rows < max(min_source_rows, 1):
        raise AcraError(f"scanned {total_rows:,} source rows, below the floor of {max(min_source_rows, 1):,}")
    if not rows:
        raise AcraError("no ACRA rows matched the watchlist")
    previous = read_previous(output_csv)
    applied = find_auto_inactive(previous, rows, watchlist)
    unapplied = find_pinned_dead_unapplied(rows, watchlist, applied) if previous is not None else []
    apply_inactive(companies_path, applied, snapshot_date)
    write_rows(output_csv, rows)
    changes_md.parent.mkdir(parents=True, exist_ok=True)
    changes_md.write_text(render_report(snapshot_date, previous, rows, total_rows, applied, unapplied), encoding="utf-8")
    return applied


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the watchlist-filtered ACRA snapshot.")
    parser.add_argument("--from-dir", type=Path, help="read already-downloaded collection CSVs instead of downloading")
    parser.add_argument("--snapshot-date", default="", help="snapshot date label for --from-dir (YYYY-MM-DD)")
    parser.add_argument("--companies", type=Path, default=COMPANIES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV)
    parser.add_argument("--changes", type=Path, default=CHANGES_MD)
    parser.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_SECONDS)
    parser.add_argument("--min-source-rows", type=int, default=DEFAULT_MIN_SOURCE_ROWS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    watchlist = Watchlist(json.loads(args.companies.read_text(encoding="utf-8")))
    try:
        if args.from_dir:
            rows, total_rows = filter_directory(args.from_dir, watchlist)
            snapshot_date = args.snapshot_date
        else:
            rows, total_rows, snapshot_date = download_and_filter(watchlist, args.pace_seconds)
            snapshot_date = args.snapshot_date or snapshot_date
        applied = run(
            rows,
            total_rows,
            snapshot_date,
            watchlist,
            args.output,
            args.changes,
            args.companies,
            args.min_source_rows,
        )
    except AcraError as error:
        log(f"acra_refresh: FAIL {error}")
        return 1
    log(
        f"acra_refresh: {len(rows)} matched rows from {total_rows:,} source rows; "
        f"{len(applied)} companies marked inactive"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
