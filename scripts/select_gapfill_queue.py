#!/usr/bin/env python3
"""Pick the next batch of gap-fill candidates for Layer 2 by least-recently-queried.

Layer 2 (agent supplement) operates under a 50-op budget against ~150 watchlisted
companies. Without rotation it tends to re-query the same prefix every day. This
script writes `signals/agent-queue.txt` — a deterministic ordered list of names
that the prompt consumes as its authoritative gap-fill cohort.

Selection:
  1. Exclude companies "covered" in the last 7 UTC days (any `## <name>` H2 in
     a `signals/updates/<date>.md` file inside that window). Covered companies
     aren't gap-fill candidates today.
  2. Of the remaining, sort by ascending `last_queried` date pulled from
     `signals/agent-queue-state.json`. Missing entries are treated as never
     queried and sort to the top. Ties break alphabetically (case-insensitive)
     for determinism.
  3. Take the top N (CLI/env tunable, default 60).

Alongside the txt (whose one-name-per-line shape is load-bearing for the
workflow layer guard, slice_companies.py, and update_gapfill_state.py) it
writes `data/agent-open-questions.json`: one `{name, open_questions}` entry
per queued company, where `open_questions` is up to OPEN_QUESTIONS_CAP
questions lifted from the `## Open questions` section of that company's
`signals/briefs/<slug>/LIVING_BRIEF.md`. Fail open — missing brief, missing
section, or any parse trouble yields an empty list, never a crash.

Pure stdlib. Idempotent. Safe to re-run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_SIZE = 60
COVERED_WINDOW_DAYS = 7
OPEN_QUESTIONS_CAP = 4
H2_RE = re.compile(r"^## (.+)$")
RUN_AT_RE = re.compile(r"^## Run at\b", re.IGNORECASE)
OPEN_QUESTIONS_H2_RE = re.compile(r"^## open questions\s*$", re.IGNORECASE)
# Mirrors the slug rule in prompts/synthesis.md (and tests/_slug.py).
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return NON_ALNUM_RE.sub("-", name.lower()).strip("-")


def load_open_questions(briefs_dir: Path, name: str, cap: int = OPEN_QUESTIONS_CAP) -> list[str]:
    """Questions from `## Open questions` in the company's living brief.

    Fail open: missing brief, missing/empty section, or any parse trouble
    returns an empty list. Only top-level `- ` bullets count; the `_none open_`
    placeholder is prose, not a bullet, so it naturally yields nothing.
    """
    try:
        brief = briefs_dir / slug(name) / "LIVING_BRIEF.md"
        if not brief.is_file():
            return []
        questions: list[str] = []
        in_section = False
        for line in brief.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                in_section = bool(OPEN_QUESTIONS_H2_RE.match(line))
                continue
            if not in_section:
                continue
            # Top-level bullets only — indented lines are sub-bullets/continuations.
            if line.startswith("- "):
                question = line[2:].strip()
                if question:
                    questions.append(question)
                if len(questions) >= cap:
                    break
        return questions[:cap]
    except Exception:
        return []


def load_companies(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    return [c["name"] for c in data]


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    last = loaded.get("last_queried", {})
    return last if isinstance(last, dict) else {}


def covered_in_window(updates_dir: Path, today: dt.date, days: int) -> set[str]:
    covered: set[str] = set()
    if not updates_dir.exists():
        return covered
    for delta in range(days):
        d = today - dt.timedelta(days=delta)
        f = updates_dir / f"{d.isoformat()}.md"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if RUN_AT_RE.match(line):
                continue
            m = H2_RE.match(line)
            if m:
                covered.add(m.group(1).strip())
    return covered


def select_queue(
    companies: list[str],
    state: dict[str, str],
    covered: set[str],
    size: int,
) -> list[str]:
    candidates = [c for c in companies if c not in covered]
    # "" sorts before any ISO date, so never-queried companies surface first.
    candidates.sort(key=lambda c: (state.get(c, ""), c.lower()))
    return candidates[:size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--size",
        type=int,
        default=int(os.environ.get("GAPFILL_QUEUE_SIZE", DEFAULT_QUEUE_SIZE)),
        help=f"Queue size (default: {DEFAULT_QUEUE_SIZE}; env GAPFILL_QUEUE_SIZE).",
    )
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        help="UTC date used as 'today' for the covered-window scan (default: today).",
    )
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    companies = load_companies(repo / "data" / "companies.json")
    state = load_state(repo / "signals" / "agent-queue-state.json")
    today = dt.date.fromisoformat(args.date)
    covered = covered_in_window(repo / "signals" / "updates", today, COVERED_WINDOW_DAYS)

    queue = select_queue(companies, state, covered, args.size)

    out = repo / "signals" / "agent-queue.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(queue) + ("\n" if queue else ""))

    # Companion JSON: same queue, enriched with each company's open brief
    # questions so Layer 2 can research them. The txt above stays names-only.
    briefs_dir = repo / "signals" / "briefs"
    entries = [
        {"name": name, "open_questions": load_open_questions(briefs_dir, name)}
        for name in queue
    ]
    questions_out = repo / "data" / "agent-open-questions.json"
    questions_out.parent.mkdir(parents=True, exist_ok=True)
    questions_out.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")

    never_queried = sum(1 for c in queue if c not in state)
    with_questions = sum(1 for e in entries if e["open_questions"])
    total_questions = sum(len(e["open_questions"]) for e in entries)
    print(
        f"queue: {len(queue)}/{args.size} "
        f"({never_queried} never-queried, "
        f"{len(covered)} covered in last {COVERED_WINDOW_DAYS}d, "
        f"{len(companies)} total watchlisted); "
        f"open questions: {total_questions} across {with_questions} companies"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
