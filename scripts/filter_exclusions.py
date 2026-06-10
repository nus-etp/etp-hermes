#!/usr/bin/env python3
"""Post-process digest files to drop items matching per-company exclude_terms.

Reads `data/companies.json` for each company's `exclude_terms` (case-insensitive
substrings). Scans `signals/updates/<date>.md` and `signals/agent/<date>.md`
(plus the v2 A/B arm's counterparts under `signals/v2/`) for the dates given
on the CLI (defaulting to today's UTC date) and drops any item bullet whose
headline-line contains an exclude term for the company it sits under. Dropped
URLs are appended to the owning arm's seen-urls file (`signals/seen-urls.txt`
or `signals/v2/seen-urls.txt`) so the LLM doesn't re-judge them tomorrow.
Empty headings are pruned afterwards.

The Layer 1 file uses `## <CompanyName>` for the company heading (with
`## Run at <time>` re-run dividers ignored). The Layer 2 file uses `## <Cohort>`
and `### <CompanyName>`; cohort headings whose children all got dropped are
removed too.

Pure stdlib. Idempotent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ITEM_BULLET_RE = re.compile(r"^- \*\*")
HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
RUN_AT_RE = re.compile(r"^## Run at\b", re.IGNORECASE)


def load_exclude_terms(companies_path: Path) -> dict[str, list[str]]:
    companies = json.loads(companies_path.read_text())
    return {
        c["name"]: [t.lower() for t in c.get("exclude_terms", [])]
        for c in companies
        if c.get("exclude_terms")
    }


def detect_company_heading_level(lines: list[str]) -> int:
    """Return 2 for Layer 1-style files (## = company) or 3 for Layer 2-style."""
    for line in lines[:5]:
        if line.startswith("# Agent supplement"):
            return 3
    return 2


def filter_items(
    lines: list[str], excludes: dict[str, list[str]], company_level: int
) -> tuple[list[str], list[str]]:
    """Drop item bullets under any company whose exclude_terms hit the headline.

    Returns (kept_lines, dropped_urls).
    """
    out: list[str] = []
    dropped_urls: list[str] = []
    current_company: str | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if level == company_level and not RUN_AT_RE.match(line):
                current_company = title
            elif level < company_level:
                # H1 (digest title) or, for Layer 2, a cohort H2 → no current company.
                current_company = None
            # Deeper headings inherit current_company.
            out.append(line)
            i += 1
            continue

        if ITEM_BULLET_RE.match(line) and current_company:
            url_line = lines[i + 1] if i + 1 < len(lines) else ""
            terms = excludes.get(current_company, [])
            haystack = (line + " " + url_line).lower()
            if terms and any(t in haystack for t in terms):
                # Drop this item (headline line + URL line). The URL is on the
                # next indented line per the digest format documented in the
                # prompts.
                url = url_line.strip()
                if url:
                    dropped_urls.append(url)
                i += 2
                # Also swallow one trailing blank line so we don't accumulate gaps.
                if i < len(lines) and lines[i].strip() == "":
                    i += 1
                continue

        out.append(line)
        i += 1

    return out, dropped_urls


def prune_empty_headings(lines: list[str]) -> list[str]:
    """Remove heading lines whose section contains no items and no sub-headings.

    Processes deeper levels first so an emptied H3 can cascade to its parent H2.
    Stops at H2; the H1 digest title is always kept.
    """
    for level in (3, 2):
        marker = "#" * level + " "
        result: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith(marker):
                j = i + 1
                while j < len(lines):
                    nm = HEADING_RE.match(lines[j])
                    if nm and len(nm.group(1)) <= level:
                        break
                    j += 1
                body = lines[i + 1:j]
                has_items = any(ITEM_BULLET_RE.match(b) for b in body)
                has_subheadings = any(HEADING_RE.match(b) for b in body)
                if not has_items and not has_subheadings:
                    # Drop the heading and its blank body. Don't drop further;
                    # the outer iteration's range is unchanged but j advances i.
                    i = j
                    # Swallow one trailing blank line to keep spacing tidy.
                    if i < len(lines) and lines[i].strip() == "":
                        i += 1
                    continue
            result.append(line)
            i += 1
        lines = result
    return lines


def process_file(
    path: Path, excludes: dict[str, list[str]], seen_urls_path: Path
) -> int:
    """Filter one digest file in place. Returns count of dropped items."""
    text = path.read_text()
    lines = text.splitlines()
    company_level = detect_company_heading_level(lines)
    filtered, dropped = filter_items(lines, excludes, company_level)
    if not dropped:
        return 0
    pruned = prune_empty_headings(filtered)
    seen_urls_path.parent.mkdir(parents=True, exist_ok=True)
    with seen_urls_path.open("a", encoding="utf-8") as f:
        for url in dropped:
            f.write(url + "\n")
    # Preserve trailing newline iff the original had one.
    out = "\n".join(pruned)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    path.write_text(out)
    return len(dropped)


def default_targets(repo: Path, today: str) -> list[Path]:
    return [
        repo / "signals" / "updates" / f"{today}.md",
        repo / "signals" / "agent" / f"{today}.md",
        repo / "signals" / "v2" / "updates" / f"{today}.md",
        repo / "signals" / "v2" / "agent" / f"{today}.md",
    ]


def seen_urls_for(path: Path, repo: Path) -> Path:
    """Route dropped URLs to the arm that owns the file (v2 arm has its own state)."""
    try:
        rel = path.resolve().relative_to(repo)
    except ValueError:
        rel = None
    if rel is not None and rel.parts[:2] == ("signals", "v2"):
        return repo / "signals" / "v2" / "seen-urls.txt"
    return repo / "signals" / "seen-urls.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repo root (default: parent of this script).",
    )
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date to filter (YYYY-MM-DD; default: today).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Specific files to filter; overrides --date discovery if given.",
    )
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    excludes = load_exclude_terms(repo / "data" / "companies.json")

    if args.paths:
        targets = [Path(p) for p in args.paths]
    else:
        targets = default_targets(repo, args.date)

    if not excludes:
        print("no exclude_terms configured; nothing to do")
        return 0

    total = 0
    for path in targets:
        if not path.exists():
            continue
        dropped = process_file(path, excludes, seen_urls_for(path, repo))
        if dropped:
            print(f"{path.relative_to(repo)}: dropped {dropped} item(s)")
            total += dropped

    if total == 0:
        print("no items dropped")
    else:
        print(f"total: {total} item(s) dropped, URLs appended to per-arm seen-urls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
