#!/usr/bin/env python3
"""Compare the production (v1) and experimental (v2) pipeline arms for one day.

Reads the four daily digest files:

  v1: signals/updates/<date>.md      signals/agent/<date>.md
  v2: signals/v2/updates/<date>.md   signals/v2/agent/<date>.md

extracts every kept item (company, headline, URL), and writes:

  signals/ab/metrics.jsonl — one row per compared day (idempotent: re-running
      the same date replaces that date's row).
  signals/ab/report.md — rolling history table built from every row on disk,
      plus a "disagreements" section for the latest date listing the items
      unique to each arm. Those disagreements are the human review queue: an
      item only v1 kept is a candidate v2 false-negative and vice versa.

Pure stdlib. Tolerates missing files (an arm that didn't run scores zero).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent

ITEM_RE = re.compile(r"^- \*\*(?P<headline>.*)\*\*\s+—\s+(?P<rest>.*)$")
HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
RUN_AT_RE = re.compile(r"^## Run at\b", re.IGNORECASE)

# Same tracking params the prompts strip, so URL overlap compares fairly.
TRACKING_KEYS = {"ref", "source", "gclid", "fbclid"}


def normalize_url(url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme:
        return url
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(query), ""))


def parse_digest(path: Path) -> list[dict]:
    """Extract items from a Layer 1 or Layer 2 digest file.

    Layer 1 files use ``## <Company>``; Layer 2 files (H1 starts with
    "# Agent supplement") use ``## <Cohort>`` / ``### <Company>``. Returns
    a list of {company, headline, url} dicts.
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    company_level = 3 if any(l.startswith("# Agent supplement") for l in lines[:5]) else 2

    items: list[dict] = []
    company: str | None = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            if level == company_level and not RUN_AT_RE.match(line):
                company = m.group(2).strip()
            elif level < company_level:
                company = None
            continue
        im = ITEM_RE.match(line)
        if im and company:
            url_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if url_line:
                # `rest` is "<source> · <pubDate>"; keep the source for the judge.
                source = im.group("rest").split(" · ")[0].strip()
                items.append(
                    {
                        "company": company,
                        "headline": im.group("headline").strip(),
                        "url": normalize_url(url_line),
                        "source": source,
                    }
                )
    return items


def collect_arm(repo: Path, date: str, v2: bool) -> list[dict]:
    base = repo / "signals" / "v2" if v2 else repo / "signals"
    items = parse_digest(base / "updates" / f"{date}.md")
    items += parse_digest(base / "agent" / f"{date}.md")
    # Dedup by URL within the arm (an item can appear in both layers).
    seen: set[str] = set()
    out = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
    return out


def compare(v1: list[dict], v2: list[dict]) -> dict:
    u1 = {it["url"] for it in v1}
    u2 = {it["url"] for it in v2}
    union = u1 | u2
    return {
        "v1_items": len(u1),
        "v2_items": len(u2),
        "v1_companies": len({it["company"] for it in v1}),
        "v2_companies": len({it["company"] for it in v2}),
        "overlap": len(u1 & u2),
        "v1_only": len(u1 - u2),
        "v2_only": len(u2 - u1),
        "jaccard": round(len(u1 & u2) / len(union), 3) if union else None,
    }


def load_candidate_index(repo: Path) -> dict[str, dict]:
    """Map normalized candidate URL -> {description, company_description}.

    Read from ``data/candidates.json`` (present in the working tree on the day
    of the run) so each disagreement carries the context the blind judge needs
    to rule on it — the article's own blurb plus the *company* description that
    settles same-name identity. Returns an empty index if the file is absent
    (e.g. replaying an old date), in which case rows fall back to digest-only
    fields.
    """
    path = repo / "data" / "candidates.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    companies = data.get("companies", {}) or {}
    index: dict[str, dict] = {}
    for cand in data.get("candidates", []) or []:
        link = cand.get("link") or ""
        if not link:
            continue
        index[normalize_url(link)] = {
            "description": (cand.get("description") or "").strip(),
            "company_description": (companies.get(cand.get("company", "")) or "").strip(),
        }
    return index


def build_disagreements(date: str, v1: list[dict], v2: list[dict], index: dict[str, dict]) -> list[dict]:
    """One row per item kept by exactly one arm — the discordant pairs to label.

    ``label`` starts null; ``scripts/ab_judge.py`` fills it. Each row is
    self-contained (carries the context for judging) so the test can run long
    after ``candidates.json`` is regenerated away.
    """
    u1 = {it["url"] for it in v1}
    u2 = {it["url"] for it in v2}
    rows: list[dict] = []
    for arm, items, other in (("v1", v1, u2), ("v2", v2, u1)):
        for it in items:
            if it["url"] in other:
                continue  # in both arms = agreement, not a disagreement
            ctx = index.get(it["url"], {})
            rows.append(
                {
                    "date": date,
                    "url": it["url"],
                    "company": it["company"],
                    "headline": it["headline"],
                    "source": it.get("source", ""),
                    "description": ctx.get("description", ""),
                    "company_description": ctx.get("company_description", ""),
                    "kept_by": arm,
                    "origin": "daily",
                    "label": None,
                    "label_model": None,
                    "label_reason": None,
                }
            )
    return rows


def merge_disagreements(path: Path, date: str, fresh: list[dict]) -> None:
    """Append today's disagreements, preserving any labels already assigned.

    Idempotent per date: existing rows for *other* dates are untouched; for the
    given date, a fresh row reuses the prior row's label fields when the (date,
    url) pair already exists, and rows that are no longer disagreements drop
    out. Never clobbers a label the judge has already written.
    """
    existing = load_rows(path)
    prior_for_date = {
        r["url"]: r for r in existing if r.get("date") == date
    }
    merged = [r for r in existing if r.get("date") != date]
    for row in fresh:
        prev = prior_for_date.get(row["url"])
        if prev and prev.get("label") is not None:
            row = {**row, **{k: prev.get(k) for k in ("label", "label_model", "label_reason")}}
        merged.append(row)
    merged.sort(key=lambda r: (r["date"], r["url"]))
    write_rows(path, merged)


def load_rows(metrics_path: Path) -> list[dict]:
    if not metrics_path.exists():
        return []
    rows = []
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_rows(metrics_path: Path, rows: list[dict]) -> None:
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )


def render_report(rows: list[dict], latest: dict, v1: list[dict], v2: list[dict]) -> str:
    u1 = {it["url"] for it in v1}
    u2 = {it["url"] for it in v2}

    lines = [
        "# A/B report — v1 (production) vs v2 (freed judgment)",
        "",
        "v1 encodes editorial judgment as prompt rules; v2 states the goal and",
        "lets the model judge. Items unique to one arm are the disagreements —",
        "review them to decide which policy filters better.",
        "",
        "## History",
        "",
        "| date | v1 items | v2 items | overlap | v1 only | v2 only | jaccard | v1 companies | v2 companies |",
        "|------|----------|----------|---------|---------|---------|---------|--------------|--------------|",
    ]
    for r in rows:
        jac = "—" if r.get("jaccard") is None else f"{r['jaccard']:.3f}"
        lines.append(
            f"| {r['date']} | {r['v1_items']} | {r['v2_items']} | {r['overlap']} "
            f"| {r['v1_only']} | {r['v2_only']} | {jac} "
            f"| {r['v1_companies']} | {r['v2_companies']} |"
        )

    def disagreement_section(title: str, items: list[dict], other_urls: set[str]) -> list[str]:
        unique = [it for it in items if it["url"] not in other_urls]
        out = ["", f"## {title} ({len(unique)})", ""]
        if not unique:
            out.append("_none_")
            return out
        by_company: dict[str, list[dict]] = {}
        for it in unique:
            by_company.setdefault(it["company"], []).append(it)
        for company in sorted(by_company):
            out.append(f"### {company}")
            for it in by_company[company]:
                out.append(f"- **{it['headline']}**")
                out.append(f"  {it['url']}")
            out.append("")
        if out[-1] == "":
            out.pop()
        return out

    lines += ["", f"## Latest disagreements — {latest['date']}"]
    lines += disagreement_section("Kept only by v1 (candidate v2 misses)", v1, u2)
    lines += disagreement_section("Kept only by v2 (candidate v1 misses)", v2, u1)
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", default=str(REPO_ROOT), help="Repo root (default: parent of this script)."
    )
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date to compare (YYYY-MM-DD; default: today).",
    )
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()

    v1 = collect_arm(repo, args.date, v2=False)
    v2 = collect_arm(repo, args.date, v2=True)
    row = {"date": args.date, **compare(v1, v2)}

    metrics_path = repo / "signals" / "ab" / "metrics.jsonl"
    rows = [r for r in load_rows(metrics_path) if r.get("date") != args.date]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    write_rows(metrics_path, rows)

    report_path = repo / "signals" / "ab" / "report.md"
    report_path.write_text(render_report(rows, row, v1, v2), encoding="utf-8")

    # Item-level discordant pairs for the significance test (ab_judge/ab_stats).
    index = load_candidate_index(repo)
    merge_disagreements(
        repo / "signals" / "ab" / "disagreements.jsonl",
        args.date,
        build_disagreements(args.date, v1, v2, index),
    )

    print(
        f"{args.date}: v1={row['v1_items']} v2={row['v2_items']} "
        f"overlap={row['overlap']} v1_only={row['v1_only']} v2_only={row['v2_only']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
