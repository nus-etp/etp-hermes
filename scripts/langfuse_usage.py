#!/usr/bin/env python3
"""Roll up one month of Langfuse usage (traces, observations, tokens, cost).

The repo *emits* Langfuse traces and scores in two places — the production
hermes plugin (``HERMES_LANGFUSE_ENV=production``) and the nightly LLM eval
suite (``HERMES_LANGFUSE_ENV=eval``). This script reads that usage back out:
for a given UTC month it queries Langfuse's public **Daily Metrics API**
(``GET /api/public/metrics/daily``, Basic-auth with the public/secret key
pair), aggregates the per-day rows, and writes two committed artifacts:

  - ``data/langfuse-usage/<YYYY-MM>.json`` — the machine-readable snapshot for
    that month (grand totals, a per-environment breakdown, and a per-model
    breakdown).
  - ``signals/langfuse-usage.md`` — a human report: a month-over-month history
    table built from *every* snapshot on disk, plus a detail view of the month
    just collected.

Re-running for the same month overwrites that month's JSON in place (idempotent)
and regenerates the Markdown from whatever snapshots exist.

Reuses the production plugin's credentials so no new secret is needed:
``HERMES_LANGFUSE_PUBLIC_KEY`` / ``HERMES_LANGFUSE_SECRET_KEY`` /
``HERMES_LANGFUSE_BASE_URL`` (base URL defaults to Langfuse Cloud). The set of
environments broken out is configurable via ``LANGFUSE_USAGE_ENVIRONMENTS``
(comma-separated; default ``production,eval``).

Fail-open on *missing* credentials: prints a notice and exits 0 so forks and
local runs without the secrets don't break. A genuine API/HTTP failure when
credentials *are* present raises, so the monthly workflow surfaces it.

Pure stdlib (urllib).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "langfuse-usage"
MD_PATH = REPO_ROOT / "signals" / "langfuse-usage.md"

DEFAULT_BASE_URL = "https://cloud.langfuse.com"
DEFAULT_ENVIRONMENTS = ["production", "eval"]
HTTP_TIMEOUT_S = 60  # Daily Metrics API uses a 1-minute server-side timeout.
PAGE_LIMIT = 100  # A month has <=31 day-rows; one page nearly always suffices.


# --------------------------------------------------------------------------- #
# Month range
# --------------------------------------------------------------------------- #
def month_range(month: str) -> tuple[str, str]:
    """Return (fromTimestamp, toTimestamp) ISO-Z bounds for a ``YYYY-MM`` month.

    ``toTimestamp`` is the first instant of the *next* month; the Daily Metrics
    API treats it as an exclusive upper bound (``timestamp < toTimestamp``).
    """
    year, mon = (int(p) for p in month.split("-"))
    start = dt.datetime(year, mon, 1, tzinfo=dt.timezone.utc)
    nxt = dt.datetime(year + (mon == 12), (mon % 12) + 1, 1, tzinfo=dt.timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return iso(start), iso(nxt)


def current_month_utc(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m")


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
def fetch_daily(
    base_url: str,
    auth_header: str,
    from_ts: str,
    to_ts: str,
    environment: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all day-rows for the window, optionally filtered to one environment.

    Walks pagination via the response ``meta.totalPages``. Returns the raw
    per-day records (``date``, ``countTraces``, ``countObservations``,
    ``totalCost``, ``usage[]``).
    """
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {
            "fromTimestamp": from_ts,
            "toTimestamp": to_ts,
            "page": str(page),
            "limit": str(PAGE_LIMIT),
        }
        if environment:
            params["environment"] = environment
        url = f"{base_url}/api/public/metrics/daily?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": auth_header, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows.extend(payload.get("data") or [])
        meta = payload.get("meta") or {}
        total_pages = int(meta.get("totalPages") or 1)
        if page >= total_pages:
            break
        page += 1
    return rows


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _empty_totals() -> dict[str, Any]:
    return {
        "days": 0,
        "countTraces": 0,
        "countObservations": 0,
        "inputUsage": 0,
        "outputUsage": 0,
        "totalUsage": 0,
        "totalCost": 0.0,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse per-day rows into month totals + a per-model breakdown."""
    totals = _empty_totals()
    models: dict[str, dict[str, Any]] = {}
    for day in rows:
        totals["days"] += 1
        totals["countTraces"] += int(day.get("countTraces") or 0)
        totals["countObservations"] += int(day.get("countObservations") or 0)
        totals["totalCost"] += float(day.get("totalCost") or 0.0)
        for u in day.get("usage") or []:
            name = u.get("model") or "(unknown)"
            m = models.setdefault(
                name,
                {
                    "model": name,
                    "inputUsage": 0,
                    "outputUsage": 0,
                    "totalUsage": 0,
                    "countObservations": 0,
                    "countTraces": 0,
                    "totalCost": 0.0,
                },
            )
            m["inputUsage"] += int(u.get("inputUsage") or 0)
            m["outputUsage"] += int(u.get("outputUsage") or 0)
            m["totalUsage"] += int(u.get("totalUsage") or 0)
            m["countObservations"] += int(u.get("countObservations") or 0)
            m["countTraces"] += int(u.get("countTraces") or 0)
            m["totalCost"] += float(u.get("totalCost") or 0.0)
            totals["inputUsage"] += int(u.get("inputUsage") or 0)
            totals["outputUsage"] += int(u.get("outputUsage") or 0)
            totals["totalUsage"] += int(u.get("totalUsage") or 0)
    totals["totalCost"] = round(totals["totalCost"], 6)
    by_model = sorted(models.values(), key=lambda m: m["totalCost"], reverse=True)
    for m in by_model:
        m["totalCost"] = round(m["totalCost"], 6)
    return {"totals": totals, "by_model": by_model}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def humanize_count(n: float) -> str:
    """Compact human count: 1234 -> '1.2K', 1_500_000 -> '1.5M'."""
    n = float(n)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return str(int(n))


def fmt_cost(c: float) -> str:
    """Dollar cost: more decimals for small amounts so cents don't vanish."""
    c = float(c)
    if c == 0:
        return "$0"
    if abs(c) < 1:
        return f"${c:.4f}"
    return f"${c:,.2f}"


def _totals_row(label: str, t: dict[str, Any]) -> str:
    return (
        f"| {label} | {t['countTraces']:,} | {t['countObservations']:,} "
        f"| {humanize_count(t['totalUsage'])} | {fmt_cost(t['totalCost'])} |"
    )


def build_markdown(snapshots: list[dict[str, Any]], generated_at: str) -> str:
    """Render the report: month history table + latest-month detail.

    ``snapshots`` is the list of every committed monthly snapshot (newest
    first). The detail section describes ``snapshots[0]``.
    """
    lines: list[str] = [
        "# Langfuse usage",
        "",
        "Monthly rollup of Langfuse usage across the production hermes pipeline "
        "and the nightly LLM eval suite. Generated by "
        "`scripts/langfuse_usage.py`; do not edit by hand.",
        "",
        f"_Last updated: {generated_at}_",
        "",
    ]

    lines += [
        "## Monthly history",
        "",
        "| Month | Traces | Observations | Tokens | Cost |",
        "|-------|-------:|-------------:|-------:|-----:|",
    ]
    for snap in snapshots:
        lines.append(_totals_row(snap["month"], snap["totals"]))
    lines.append("")

    if not snapshots:
        return "\n".join(lines) + "\n"

    latest = snapshots[0]
    lines += [
        f"## {latest['month']} detail",
        "",
        f"Window: `{latest['from']}` → `{latest['to']}` (exclusive). "
        f"Active days: {latest['totals']['days']}.",
        "",
        "### By environment",
        "",
        "| Environment | Traces | Observations | Tokens | Cost |",
        "|-------------|-------:|-------------:|-------:|-----:|",
    ]
    for env, t in latest.get("by_environment", {}).items():
        lines.append(_totals_row(env, t))
    lines.append(_totals_row("**all**", latest["totals"]))
    lines.append("")

    lines += [
        "### By model",
        "",
        "| Model | Input | Output | Total tokens | Cost |",
        "|-------|------:|-------:|-------------:|-----:|",
    ]
    for m in latest.get("by_model", []):
        lines.append(
            f"| {m['model']} | {humanize_count(m['inputUsage'])} "
            f"| {humanize_count(m['outputUsage'])} "
            f"| {humanize_count(m['totalUsage'])} | {fmt_cost(m['totalCost'])} |"
        )
    if not latest.get("by_model"):
        lines.append("| _(no model usage recorded)_ | | | | |")
    lines.append("")

    return "\n".join(lines) + "\n"


def load_snapshots(out_dir: Path) -> list[dict[str, Any]]:
    """Load every ``<YYYY-MM>.json`` snapshot, newest month first."""
    snaps: list[dict[str, Any]] = []
    if not out_dir.exists():
        return snaps
    for path in sorted(out_dir.glob("*.json")):
        try:
            snap = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(snap, dict) and snap.get("month"):
            snaps.append(snap)
    snaps.sort(key=lambda s: s["month"], reverse=True)
    return snaps


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def collect_month(
    month: str,
    base_url: str,
    auth_header: str,
    environments: list[str],
) -> dict[str, Any]:
    """Build the snapshot dict for one month (no I/O)."""
    from_ts, to_ts = month_range(month)

    grand = aggregate(fetch_daily(base_url, auth_header, from_ts, to_ts))
    by_environment: dict[str, Any] = {}
    for env in environments:
        env_rows = fetch_daily(base_url, auth_header, from_ts, to_ts, environment=env)
        by_environment[env] = aggregate(env_rows)["totals"]

    return {
        "month": month,
        "from": from_ts,
        "to": to_ts,
        "base_url": base_url,
        "totals": grand["totals"],
        "by_environment": by_environment,
        "by_model": grand["by_model"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--month",
        default=os.environ.get("LANGFUSE_USAGE_MONTH", "") or current_month_utc(),
        help="UTC month to collect, as YYYY-MM (default: current month).",
    )
    args = parser.parse_args()

    pub = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("HERMES_LANGFUSE_SECRET_KEY")
    if not (pub and sec):
        print(
            "langfuse-usage: HERMES_LANGFUSE_PUBLIC_KEY/SECRET_KEY not set — "
            "skipping (fail-open).",
            file=sys.stderr,
        )
        return 0

    base_url = (os.environ.get("HERMES_LANGFUSE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    raw_envs = os.environ.get("LANGFUSE_USAGE_ENVIRONMENTS", "")
    environments = [e.strip() for e in raw_envs.split(",") if e.strip()] or list(
        DEFAULT_ENVIRONMENTS
    )
    token = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    auth_header = f"Basic {token}"

    try:
        snapshot = collect_month(args.month, base_url, auth_header, environments)
    except urllib.error.HTTPError as e:
        print(f"langfuse-usage: HTTP {e.code} from {e.url}: {e.reason}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.month}.json"
    out_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")

    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    MD_PATH.write_text(build_markdown(load_snapshots(OUT_DIR), generated_at))

    t = snapshot["totals"]
    print(
        f"langfuse-usage: {args.month} — {t['countTraces']:,} traces, "
        f"{t['countObservations']:,} observations, {humanize_count(t['totalUsage'])} "
        f"tokens, {fmt_cost(t['totalCost'])} across {t['days']} active day(s). "
        f"Wrote {out_path.relative_to(REPO_ROOT)} and {MD_PATH.relative_to(REPO_ROOT)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
