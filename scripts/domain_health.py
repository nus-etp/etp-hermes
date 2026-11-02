#!/usr/bin/env python3
"""Monthly domain liveness + RDAP expiry report for the watchlist's own domains.

For every company in data/companies.json, its own domains are derived from
`sources[]`/`identifiers` (generate_match_terms.homepage_domains), a company
name or alias that is itself a domain (`Hubtech.asia`), and a domain in the
description whose brand label equals the compacted name (`pennycillin.co`).
Each registrable domain gets a DNS lookup (apex, then `www.`), an HTTP GET over
the shared UA ladder (https, then http), and an RDAP expiry lookup through the
rdap.org bootstrap redirector.

Classes, worst first: dead, expired, unreachable, expiring, redirected-offsite,
ok. Writes signals/domain-health.md (committed) and data/domain-health.json
(gitignored). Purely observational: never edits companies.json. Fails open per
domain; exits 1 without writing only when no domain resolved at all, which
means the runner has no network rather than that every company died.

Usage:
  python3 scripts/domain_health.py [--limit 30] [--workers 8] [--expiry-days 30]
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_match_terms import homepage_domains, is_third_party  # noqa: E402
from user_agents import GATED_STATUSES, build_ladder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
OUT_JSON = REPO_ROOT / "data" / "domain-health.json"
OUT_MD = REPO_ROOT / "signals" / "domain-health.md"

UA_LADDER = build_ladder("domain-health")
RDAP_URL = "https://rdap.org/domain/{domain}"
TIMEOUT_SECS = 20
DEFAULT_WORKERS = 8
DEFAULT_EXPIRY_DAYS = 30
RDAP_MIN_INTERVAL_SECS = 1.0
RDAP_RETRY_BACKOFF_SECS = (5, 15)

STATUS_ORDER = ("dead", "expired", "unreachable", "expiring", "redirected-offsite", "ok")

EXTRA_THIRD_PARTY_HOSTS = (
    "recruitee.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "breezy.hr",
    "notion.so",
    "wixsite.com",
    "webflow.io",
    "framer.website",
    "framer.ai",
    "vercel.app",
    "netlify.app",
    "carrd.co",
    "linktr.ee",
    "apple.com",
    "startupsg.gov.sg",
    "gov.sg",
    "edu.sg",
)

SECOND_LEVEL_LABELS = {"com", "co", "net", "org", "edu", "gov", "ac", "or", "biz", "info", "mil"}

DOMAIN_RE = re.compile(
    r"(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,6})(?![\w-])",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+")
DISTRACTOR_RE = re.compile(r"\b(drop|unrelated|lookalike|namesake|distinct|not|unless)\b", re.IGNORECASE)


def registrable_domain(host: str) -> str:
    labels = [label for label in host.lower().strip(".").split(".") if label]
    if len(labels) < 2:
        return ""
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL_LABELS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def brand_label(domain: str) -> str:
    return domain.split(".")[0]


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_excluded_host(host: str) -> bool:
    if is_third_party(host):
        return True
    return any(host == h or host.endswith("." + h) for h in EXTRA_THIRD_PARTY_HOSTS)


def _names(company: dict[str, Any]) -> list[str]:
    return [company.get("name") or "", *(company.get("aliases") or [])]


def _name_domains(company: dict[str, Any]) -> list[str]:
    found = []
    for name in _names(company):
        match = DOMAIN_RE.fullmatch(name.strip())
        if match:
            found.append(match.group(1))
    return found


def _description_domains(company: dict[str, Any]) -> list[str]:
    keys = {compact(name) for name in _names(company) if len(compact(name)) >= 4}
    sentences = SENTENCE_SPLIT_RE.split(company.get("description") or "")
    text = " ".join(sentence for sentence in sentences if not DISTRACTOR_RE.search(sentence))
    found = []
    for candidate in DOMAIN_RE.findall(text):
        domain = registrable_domain(candidate)
        if not domain:
            continue
        brand = brand_label(domain)
        if compact(brand) in keys or compact(brand + domain.split(".")[1]) in keys:
            found.append(domain)
    return found


def company_domains(company: dict[str, Any]) -> list[str]:
    hosts = homepage_domains(company) + _name_domains(company) + _description_domains(company)
    out: list[str] = []
    for host in hosts:
        domain = registrable_domain(host)
        if not domain or is_excluded_host(host) or is_excluded_host(domain):
            continue
        if domain not in out:
            out.append(domain)
    return out


def collect_domains(companies: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[str]]:
    by_domain: dict[str, list[str]] = {}
    without: list[str] = []
    for company in companies:
        name = company.get("name") or ""
        domains = company_domains(company)
        if not domains:
            without.append(name)
        for domain in domains:
            by_domain.setdefault(domain, [])
            if name not in by_domain[domain]:
                by_domain[domain].append(name)
    return by_domain, without


def resolve(domain: str) -> dict[str, Any]:
    for host in (domain, f"www.{domain}"):
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            last_error = str(exc)[:200]
            continue
        addresses = sorted({info[4][0] for info in infos})
        if addresses:
            return {"resolved": True, "host": host, "addresses": addresses[:4]}
        last_error = "no addresses"
    return {"resolved": False, "host": None, "error": last_error}


def _get(url: str) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url}
    for user_agent in UA_LADDER:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"}, method="GET")
        try:
            with request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
                return {"url": url, "status": resp.status, "final_url": resp.geturl()}
        except error.HTTPError as exc:
            result = {"url": url, "status": exc.code, "final_url": exc.geturl() or url}
            if exc.code in GATED_STATUSES:
                continue
            return result
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {"url": url, "status": None, "error": str(getattr(exc, "reason", exc))[:200]}
    return result


def http_check(host: str) -> dict[str, Any]:
    secure = _get(f"https://{host}/")
    if secure.get("status") is not None:
        return secure
    plain = _get(f"http://{host}/")
    if plain.get("status") is not None:
        plain["https_error"] = secure.get("error")
        return plain
    return secure


class Pacer:
    def __init__(self, interval: float, sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = interval
        self.sleep = sleep
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                self.sleep(self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval


RDAP_PACER = Pacer(RDAP_MIN_INTERVAL_SECS)


def fetch_rdap(domain: str, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any] | None:
    url = RDAP_URL.format(domain=domain)
    headers = {"User-Agent": UA_LADDER[0], "Accept": "application/rdap+json, application/json"}
    for attempt in range(len(RDAP_RETRY_BACKOFF_SECS) + 1):
        RDAP_PACER.wait()
        try:
            with request.urlopen(request.Request(url, headers=headers), timeout=TIMEOUT_SECS) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except error.HTTPError as exc:
            if exc.code in {429, 503} and attempt < len(RDAP_RETRY_BACKOFF_SECS):
                sleep(RDAP_RETRY_BACKOFF_SECS[attempt])
                continue
            if exc.code == 404:
                return None
            raise
    return None


def rdap_expiry(payload: dict[str, Any] | None) -> str | None:
    for event in (payload or {}).get("events") or []:
        if str(event.get("eventAction", "")).lower() == "expiration" and event.get("eventDate"):
            return str(event["eventDate"])
    return None


def rdap_check(domain: str, fetcher: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    try:
        payload = fetcher(domain)
    except error.HTTPError as exc:
        return {"expiration": None, "error": f"HTTP {exc.code}"}
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"expiration": None, "error": str(getattr(exc, "reason", exc))[:200]}
    if payload is None:
        return {"expiration": None, "error": "no RDAP record (unsupported TLD or not registered)"}
    expiration = rdap_expiry(payload)
    return {"expiration": expiration, "error": None if expiration else "no expiration event"}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_offsite(domain: str, final_url: str | None) -> bool:
    if not final_url:
        return False
    final_host = urlparse(final_url).netloc.lower().split(":")[0]
    final_domain = registrable_domain(final_host)
    if not final_domain or final_domain == domain:
        return False
    return brand_label(final_domain) != brand_label(domain)


def classify(record: dict[str, Any], now: datetime, expiry_days: int) -> tuple[str, list[str]]:
    notes: list[str] = []
    dns = record.get("dns") or {}
    http = record.get("http") or {}
    rdap = record.get("rdap") or {}
    expiration = _parse_date(rdap.get("expiration"))
    days_left = (expiration - now).days if expiration else None
    if days_left is not None:
        record["rdap"]["days_left"] = days_left
    status = http.get("status")
    if http.get("https_error"):
        notes.append(f"https failed, served over http ({http['https_error']})")
    if status in GATED_STATUSES:
        notes.append(f"HTTP {status} to every UA (host answered)")
    elif status is not None and status >= 400:
        notes.append(f"HTTP {status}")
    if not dns.get("resolved"):
        return "dead", notes
    if days_left is not None and days_left < 0:
        return "expired", notes
    if status is None or status >= 500:
        return "unreachable", notes
    if days_left is not None and days_left <= expiry_days:
        return "expiring", notes
    if is_offsite(record["domain"], http.get("final_url")):
        return "redirected-offsite", notes
    return "ok", notes


def check_domain(
    domain: str,
    companies: list[str],
    *,
    now: datetime,
    expiry_days: int,
    resolver: Callable[[str], dict[str, Any]] = resolve,
    http: Callable[[str], dict[str, Any]] = http_check,
    rdap_fetcher: Callable[[str], dict[str, Any] | None] = fetch_rdap,
) -> dict[str, Any]:
    record: dict[str, Any] = {"domain": domain, "companies": companies}
    try:
        record["dns"] = resolver(domain)
    except Exception as exc:
        record["dns"] = {"resolved": False, "host": None, "error": str(exc)[:200]}
    if record["dns"].get("resolved"):
        try:
            record["http"] = http(record["dns"]["host"])
        except Exception as exc:
            record["http"] = {"status": None, "error": str(exc)[:200]}
    else:
        record["http"] = {"status": None, "error": "skipped: no DNS"}
    try:
        record["rdap"] = rdap_check(domain, rdap_fetcher)
    except Exception as exc:
        record["rdap"] = {"expiration": None, "error": str(exc)[:200]}
    record["status"], record["notes"] = classify(record, now, expiry_days)
    return record


def run(
    companies: list[dict[str, Any]],
    *,
    now: datetime,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    workers: int = DEFAULT_WORKERS,
    limit: int | None = None,
    checker: Callable[..., dict[str, Any]] = check_domain,
) -> dict[str, Any]:
    by_domain, without = collect_domains(companies)
    domains = sorted(by_domain)
    if limit is not None:
        domains = domains[:limit]

    def _one(domain: str) -> dict[str, Any]:
        return checker(domain, by_domain[domain], now=now, expiry_days=expiry_days)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        records = list(pool.map(_one, domains))
    rank = {status: index for index, status in enumerate(STATUS_ORDER)}
    records.sort(key=lambda r: (rank.get(r["status"], len(rank)), r["domain"]))
    counts = {status: sum(1 for r in records if r["status"] == status) for status in STATUS_ORDER}
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "expiry_days": expiry_days,
        "companies_total": len(companies),
        "companies_without_domain": len(without),
        "domains_total": len(by_domain),
        "domains_checked": len(records),
        "counts": counts,
        "domains": records,
    }


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _http_cell(record: dict[str, Any]) -> str:
    http = record.get("http") or {}
    if http.get("status") is None:
        return _cell(http.get("error") or "")
    return str(http["status"])


def _final_cell(record: dict[str, Any]) -> str:
    http = record.get("http") or {}
    final_url = http.get("final_url") or ""
    return final_url if final_url.rstrip("/") != (http.get("url") or "").rstrip("/") else ""


def _expiry_cell(record: dict[str, Any]) -> str:
    rdap = record.get("rdap") or {}
    if rdap.get("expiration"):
        days = rdap.get("days_left")
        return f"{rdap['expiration'][:10]} ({days}d)" if days is not None else rdap["expiration"][:10]
    return "unknown"


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    summary = " · ".join(f"{counts[s]} {s}" for s in STATUS_ORDER)
    lines = [
        "# Domain health",
        "",
        f"_Last checked: {report['checked_at'][:10]} (UTC)_  ",
        f"{summary} · {report['domains_checked']} domains checked "
        f"({report['companies_without_domain']} of {report['companies_total']} companies have no derivable domain)",
        "",
        "Classes, worst first: **dead** = no A/AAAA record for the apex or `www.`; "
        "**expired** = RDAP expiration date has passed; **unreachable** = DNS resolves but HTTP fails or answers 5xx; "
        f"**expiring** = RDAP expiration within {report['expiry_days']} days; "
        "**redirected-offsite** = the homepage lands on a different registrable domain with a different brand label "
        "(possible acquisition, rename or parking). RDAP `unknown` = the TLD has no RDAP service (many ccTLDs) or no expiration event. "
        "Observational only — review and update `data/companies.json` by hand.",
        "",
    ]
    headers = "| Domain | Companies | HTTP | Final URL | Expires | Notes |"
    for status in STATUS_ORDER:
        rows = [r for r in report["domains"] if r["status"] == status]
        if not rows:
            continue
        lines += [f"## {status} ({len(rows)})", "", headers, "| --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            notes = "; ".join(r.get("notes") or [])
            if status == "dead":
                notes = "; ".join(filter(None, [_cell((r.get("dns") or {}).get("error")), notes]))
            lines.append(
                f"| {r['domain']} | {_cell(', '.join(r['companies']))} | {_http_cell(r)} | "
                f"{_cell(_final_cell(r))} | {_expiry_cell(r)} | {_cell(notes)} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_companies(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--companies", type=Path, default=COMPANIES_FILE)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--expiry-days", type=int, default=DEFAULT_EXPIRY_DAYS)
    args = parser.parse_args(argv)

    report = run(
        _load_companies(args.companies),
        now=datetime.now(timezone.utc),
        expiry_days=args.expiry_days,
        workers=args.workers,
        limit=args.limit,
        checker=check_domain,
    )
    if report["domains_checked"] and report["counts"]["dead"] == report["domains_checked"]:
        print("domain_health: no domain resolved — runner looks offline; not writing", file=sys.stderr)
        return 1
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(render_markdown(report), encoding="utf-8")
    counts = ", ".join(f"{k}={v}" for k, v in report["counts"].items())
    print(f"domain_health: {report['domains_checked']} domains checked ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
