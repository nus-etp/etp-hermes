#!/usr/bin/env python3
"""Dead-feed detector — surfaces sources that have stopped delivering.

Reads data/feed-cache.json (per-URL state written by preflight-feeds.py) plus
data/feeds.json + data/companies.json (to label each URL), and classifies every
preflighted source:

- DEAD:  consecutive_failures >= DEAD_FEED_FAIL_THRESHOLD (default 3) — the
         host has been unreachable / erroring for several runs in a row.
- STALE: reachable, but last_changed is older than DEAD_FEED_STALE_DAYS
         (default 45) — the feed responds but hasn't published anything new.
- OK:    everything else.

Writes:
- data/dead-feeds.json  — machine list (gitignored, regenerated per run).
- signals/feed-health.md — committed human report, worst-first.

This is a reporting/observability step, not a gate: it always exits 0 (a
missing cache just yields an empty report). The actual recovery is the
deterministic r.jina.ai fallback in collect-candidates.py, which refetches
failed firehose/rss feeds through Jina Reader on the same run — so a DEAD
firehose/rss feed here is one that even Jina couldn't help, or a kind
(github_org/lever_jobs) the Markdown fallback doesn't cover.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = REPO_ROOT / "data" / "feeds.json"
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
CACHE_FILE = REPO_ROOT / "data" / "feed-cache.json"
OUT_JSON = REPO_ROOT / "data" / "dead-feeds.json"
OUT_MD = REPO_ROOT / "signals" / "feed-health.md"

DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_STALE_DAYS = 45

# Kinds the deterministic Jina fallback in collect-candidates can recover.
JINA_RECOVERABLE = {"firehose", "rss"}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def build_url_meta() -> dict[str, dict[str, str]]:
    """url -> {name, kind} for every preflightable source (firehose + per-company)."""
    meta: dict[str, dict[str, str]] = {}
    if FEEDS_FILE.exists():
        for f in _load_json(FEEDS_FILE):
            url = f.get("url")
            if url:
                meta[url] = {"name": f.get("name", url), "kind": "firehose"}
    if COMPANIES_FILE.exists():
        for c in _load_json(COMPANIES_FILE):
            name = c.get("name", "")
            for s in c.get("sources") or []:
                url = s.get("url")
                if not url:
                    continue
                label = s.get("label") or s.get("type") or "source"
                # Don't clobber a firehose label if a company reuses the URL.
                meta.setdefault(url, {"name": f"{name} · {label}", "kind": s.get("type", "")})
    return meta


def classify(
    cache: dict[str, Any],
    meta: dict[str, dict[str, str]],
    fail_threshold: int,
    stale_days: int,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    dead: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []

    for url, entry in cache.items():
        info = meta.get(url, {"name": url, "kind": ""})
        kind = info.get("kind", "")
        fails = int(entry.get("consecutive_failures", 0) or 0)
        last_changed = _parse_iso(entry.get("last_changed"))
        days_stale = (now - last_changed).days if last_changed else None

        row = {
            "url": url,
            "name": info.get("name", url),
            "kind": kind,
            "consecutive_failures": fails,
            "last_status": entry.get("last_status"),
            "last_error": entry.get("last_error", ""),
            "last_changed": entry.get("last_changed", ""),
            "days_stale": days_stale,
            "jina_recoverable": kind in JINA_RECOVERABLE,
        }

        if fails >= fail_threshold:
            dead.append(row)
        elif days_stale is not None and days_stale > stale_days:
            stale.append(row)

    dead.sort(key=lambda r: r["consecutive_failures"], reverse=True)
    stale.sort(key=lambda r: (r["days_stale"] or 0), reverse=True)
    return {"dead": dead, "stale": stale}


def _md_table(rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> str:
    header = "| " + " | ".join(label for _, label in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [header, sep]
    for r in rows:
        cells = []
        for key, _ in cols:
            val = r.get(key, "")
            cells.append("" if val is None else str(val).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_markdown(
    result: dict[str, list[dict[str, Any]]],
    *,
    total: int,
    fail_threshold: int,
    stale_days: int,
    now: datetime,
) -> str:
    dead, stale = result["dead"], result["stale"]
    out = [
        "# Feed health",
        "",
        f"_Last checked: {now.date().isoformat()} (UTC)_  ",
        (
            f"{len(dead)} dead · {len(stale)} stale · {total} sources tracked  "
            f"(dead = ≥{fail_threshold} consecutive failures; "
            f"stale = no new items in >{stale_days} days)"
        ),
        "",
        "Dead **firehose/rss** feeds are auto-recovered each run via the r.jina.ai "
        "fallback in `collect-candidates.py`; a dead feed below is one even Jina "
        "couldn't reach, or a kind (`github_org`/`lever_jobs`) the Markdown "
        "fallback doesn't cover. Prune or replace those in `data/feeds.json` / "
        "`data/companies.json`.",
        "",
    ]

    out.append("## Dead feeds")
    out.append("")
    if dead:
        out.append(
            _md_table(
                dead,
                [
                    ("name", "Feed"),
                    ("kind", "Kind"),
                    ("consecutive_failures", "Fails"),
                    ("last_status", "Last status"),
                    ("jina_recoverable", "Jina-recoverable"),
                    ("last_error", "Last error"),
                    ("url", "URL"),
                ],
            )
        )
    else:
        out.append("_None._")
    out.append("")

    out.append("## Stale feeds")
    out.append("")
    if stale:
        out.append(
            _md_table(
                stale,
                [
                    ("name", "Feed"),
                    ("kind", "Kind"),
                    ("days_stale", "Days since new item"),
                    ("last_changed", "Last change"),
                    ("url", "URL"),
                ],
            )
        )
    else:
        out.append("_None._")
    out.append("")
    return "\n".join(out)


def main() -> int:
    try:
        fail_threshold = int(os.environ.get("DEAD_FEED_FAIL_THRESHOLD", DEFAULT_FAIL_THRESHOLD))
    except ValueError:
        fail_threshold = DEFAULT_FAIL_THRESHOLD
    try:
        stale_days = int(os.environ.get("DEAD_FEED_STALE_DAYS", DEFAULT_STALE_DAYS))
    except ValueError:
        stale_days = DEFAULT_STALE_DAYS

    now = datetime.now(timezone.utc)
    cache: dict[str, Any] = _load_json(CACHE_FILE) if CACHE_FILE.exists() else {}
    meta = build_url_meta()
    result = classify(cache, meta, fail_threshold, stale_days, now)

    out_json = {
        "generated_at": now.isoformat(timespec="seconds"),
        "fail_threshold": fail_threshold,
        "stale_days": stale_days,
        "dead": result["dead"],
        "stale": result["stale"],
        "stats": {
            "tracked": len(cache),
            "dead": len(result["dead"]),
            "stale": len(result["stale"]),
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(out_json, f, indent=2, sort_keys=True)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        render_markdown(
            result,
            total=len(cache),
            fail_threshold=fail_threshold,
            stale_days=stale_days,
            now=now,
        ),
        encoding="utf-8",
    )

    print(
        f"detect-dead-feeds: tracked={len(cache)} "
        f"dead={len(result['dead'])} stale={len(result['stale'])}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
