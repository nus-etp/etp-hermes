#!/usr/bin/env python3
"""Daily blind-judge sample over Layer 1's verdicts.

Runs after Layer 1. Samples up to ``--kept`` items the pipeline kept (today's
``signals/updates/<date>.md``) and up to ``--dropped`` candidates it dropped
(``data/candidates.json`` entries whose link is absent from the updates), and
asks the blind relevance judge from ``scripts/ab_judge.py`` to rule on each —
never telling it which way the pipeline went. Writes one row per item to
``data/harness/judged/<date>.jsonl``; rows where ``verdict_judge`` differs
from ``verdict_pipeline`` become scenario cards via write_scenarios.py.

Sampling is seeded by the date, so a re-run picks the same items. Fail-open:
no ``DEEPSEEK_API_KEY`` writes nothing and exits 0; a per-item judge failure
records ``verdict_judge: null``. Pure stdlib (via ab_llm/ab_judge/ab_compare).
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_SAMPLE = 40


def load_script(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_candidates(repo: Path) -> dict:
    path = repo / "data" / "candidates.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def kept_items(compare, repo: Path, date: str, candidates: dict) -> list[dict]:
    company_descriptions = candidates.get("companies") or {}
    blurbs = candidate_blurbs(compare, candidates)
    items = []
    for item in compare.parse_digest(repo / "signals" / "updates" / f"{date}.md"):
        items.append(
            {
                "company": item["company"],
                "headline": item["headline"],
                "url": item["url"],
                "source": item.get("source", ""),
                "description": blurbs.get(item["url"], ""),
                "company_description": str(company_descriptions.get(item["company"]) or ""),
                "verdict_pipeline": "keep",
            }
        )
    return unique_by_url(items)


def candidate_blurbs(compare, candidates: dict) -> dict[str, str]:
    blurbs: dict[str, str] = {}
    for candidate in candidates.get("candidates") or []:
        link = candidate.get("link") or ""
        if link:
            blurbs.setdefault(compare.normalize_url(link), str(candidate.get("description") or "").strip())
    return blurbs


def dropped_items(compare, candidates: dict, kept_urls: set[str]) -> list[dict]:
    company_descriptions = candidates.get("companies") or {}
    items = []
    for candidate in candidates.get("candidates") or []:
        link = candidate.get("link") or ""
        if not link:
            continue
        url = compare.normalize_url(link)
        if url in kept_urls:
            continue
        company = str(candidate.get("company") or "")
        items.append(
            {
                "company": company,
                "headline": str(candidate.get("headline") or ""),
                "url": url,
                "source": str(candidate.get("source") or ""),
                "description": str(candidate.get("description") or "").strip(),
                "company_description": str(company_descriptions.get(company) or ""),
                "verdict_pipeline": "drop",
            }
        )
    return unique_by_url(items)


def unique_by_url(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def sample(items: list[dict], size: int, seed: str) -> list[dict]:
    if len(items) <= size:
        return list(items)
    return random.Random(seed).sample(items, size)


def judge_items(judge, llm, items: list[dict], date: str, model: str | None) -> list[dict]:
    rows = []
    for item in items:
        verdict, reason = judge.judge_row(llm, item, model)
        rows.append({**item, "date": date, "verdict_judge": verdict, "judge_reason": reason, "origin": "daily"})
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def run(repo: Path, date: str, kept_size: int, dropped_size: int, llm, judge, compare, model: str | None) -> int:
    if not llm.have_key():
        print("daily_judge_sample: DEEPSEEK_API_KEY not set; nothing judged (fail-open)")
        return 0
    candidates = read_candidates(repo)
    kept = kept_items(compare, repo, date, candidates)
    dropped = dropped_items(compare, candidates, {item["url"] for item in kept})
    chosen = sample(kept, kept_size, f"{date}:kept") + sample(dropped, dropped_size, f"{date}:dropped")
    if not chosen:
        print("daily_judge_sample: no kept or dropped items today; nothing judged")
        return 0
    rows = judge_items(judge, llm, chosen, date, model)
    out = repo / "data" / "harness" / "judged" / f"{date}.jsonl"
    write_rows(out, rows)
    disagreements = sum(1 for row in rows if row["verdict_judge"] and row["verdict_judge"] != row["verdict_pipeline"])
    print(f"daily_judge_sample: judged {len(rows)} items, {disagreements} disagreements -> {out.relative_to(repo)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    parser.add_argument("--kept", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--dropped", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--model", default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    llm = load_script("ab_llm")
    judge = load_script("ab_judge")
    compare = load_script("ab_compare")
    return run(args.repo_root.resolve(), args.date, args.kept, args.dropped, llm, judge, compare, args.model)


if __name__ == "__main__":
    sys.exit(main())
