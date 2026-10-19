#!/usr/bin/env python3
"""Daily blind-judge pass over Layer 1 verdicts and Layer 2 kept items.

Runs after Layer 1. Takes the items the pipeline kept (today's
``signals/updates/<date>.md``) and the candidates it dropped
(``data/candidates.json`` entries whose link is absent from the updates), plus
the items Layer 2 kept (``signals/agent/<date>.md``), and asks a blind
relevance judge to rule on each — never telling it which way the pipeline
went. Writes one row per item to ``data/harness/judged/<date>.jsonl`` with the
``layer`` it came from, the ``judge`` used and its ``judge_confidence``; rows
where ``verdict_judge`` differs from ``verdict_pipeline`` become scenario
cards via write_scenarios.py.

``--judge auto`` uses the Jev classifier (``TYPESAFE_API_KEY``, cheap enough to
judge everything) when the key is set, else the DeepSeek judge
(``DEEPSEEK_API_KEY``, sampled to ``--kept``/``--dropped`` items per day).
Sampling is seeded by the date, so a re-run picks the same items. Fail-open:
no key writes nothing and exits 0; a per-item judge failure records
``verdict_judge: null``. Pure stdlib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import random
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_SAMPLE = 40
AGENT_HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
AGENT_ITEM_RE = re.compile(r"^- \*\*(?P<headline>.*)\*\*\s+—\s+(?P<rest>.*)$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Judger(NamedTuple):
    name: str
    judge: Callable[[dict], tuple[str | None, str | None, float | None]]
    default_sample: int | None


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
                "layer": "ingest",
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
                "published": str(candidate.get("pubDate") or ""),
                "verdict_pipeline": "drop",
                "layer": "ingest",
            }
        )
    return unique_by_url(items)


def split_item_meta(rest: str) -> tuple[str, str]:
    parts = [part.strip() for part in rest.split(" · ")]
    date = parts[-1] if len(parts) > 1 and ISO_DATE_RE.match(parts[-1]) else ""
    return parts[0], date


def parse_agent_digest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    items = []
    company = None
    for index, line in enumerate(lines):
        heading = AGENT_HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            company = heading.group(2).strip() if level == 3 else (None if level < 3 else company)
            continue
        item = AGENT_ITEM_RE.match(line)
        url_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if item and company and url_line.startswith("http"):
            source, item_date = split_item_meta(item.group("rest"))
            items.append(
                {
                    "company": company,
                    "headline": item.group("headline").strip(),
                    "url": url_line,
                    "source": source,
                    "item_date": item_date,
                }
            )
    return items


def watchlist_descriptions(repo: Path) -> dict[str, str]:
    for name in ("agent-companies.json", "companies.json"):
        path = repo / "data" / name
        if not path.exists():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entries, list):
            return {str(e.get("name")): str(e.get("description") or "") for e in entries if isinstance(e, dict)}
    return {}


def agent_items(compare, repo: Path, date: str, candidates: dict) -> list[dict]:
    parsed = parse_agent_digest(repo / "signals" / "agent" / f"{date}.md")
    if not parsed:
        return []
    descriptions = {**watchlist_descriptions(repo), **(candidates.get("companies") or {})}
    items = []
    for item in parsed:
        items.append(
            {
                "company": item["company"],
                "headline": item["headline"],
                "url": compare.normalize_url(item["url"]),
                "source": item["source"],
                "description": f"Published {item['item_date']}" if item["item_date"] else "",
                "company_description": str(descriptions.get(item["company"]) or ""),
                "verdict_pipeline": "keep",
                "layer": "agent_supplement",
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


def sample(items: list[dict], size: int | None, seed: str) -> list[dict]:
    if size is None or len(items) <= size:
        return list(items)
    return random.Random(seed).sample(items, size)


def judge_items(judger: Judger, items: list[dict], date: str) -> list[dict]:
    rows = []
    for item in items:
        verdict, reason, confidence = judger.judge(item)
        rows.append(
            {
                **item,
                "date": date,
                "verdict_judge": verdict,
                "judge_reason": reason,
                "judge": judger.name,
                "judge_confidence": confidence,
                "origin": "daily",
            }
        )
    return rows


def deepseek_judger(llm, judge, model: str | None) -> Judger:
    def judge_one(item: dict) -> tuple[str | None, str | None, float | None]:
        verdict, reason = judge.judge_row(llm, item, model)
        return verdict, reason, None

    return Judger("deepseek", judge_one, DEFAULT_SAMPLE)


def jev_judger(jev) -> Judger:
    def judge_one(item: dict) -> tuple[str | None, str | None, float | None]:
        result = jev.judge_item(item)
        return result["verdict"], result["reason"], result["confidence"]

    return Judger("jev", judge_one, None)


def select_judger(choice: str, llm, judge, jev, model: str | None) -> Judger | None:
    if choice in {"auto", "jev"} and jev.have_key():
        return jev_judger(jev)
    if choice in {"auto", "deepseek"} and llm.have_key():
        return deepseek_judger(llm, judge, model)
    return None


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def run(repo: Path, date: str, kept_size: int | None, dropped_size: int | None, judger: Judger | None, compare) -> int:
    if judger is None:
        print("daily_judge_sample: no judge key set; nothing judged (fail-open)")
        return 0
    kept_size = judger.default_sample if kept_size is None else kept_size
    dropped_size = judger.default_sample if dropped_size is None else dropped_size
    candidates = read_candidates(repo)
    kept = kept_items(compare, repo, date, candidates)
    dropped = dropped_items(compare, candidates, {item["url"] for item in kept})
    agent = agent_items(compare, repo, date, candidates)
    chosen = sample(kept, kept_size, f"{date}:kept") + sample(dropped, dropped_size, f"{date}:dropped") + agent
    if not chosen:
        print("daily_judge_sample: no kept or dropped items today; nothing judged")
        return 0
    rows = judge_items(judger, chosen, date)
    out = repo / "data" / "harness" / "judged" / f"{date}.jsonl"
    write_rows(out, rows)
    disagreements = sum(1 for row in rows if row["verdict_judge"] and row["verdict_judge"] != row["verdict_pipeline"])
    print(
        f"daily_judge_sample: {judger.name} judged {len(rows)} items "
        f"({len(agent)} from Layer 2), {disagreements} disagreements -> {out.relative_to(repo)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    parser.add_argument("--judge", choices=("auto", "jev", "deepseek"), default="auto")
    parser.add_argument("--kept", type=int, default=None, help="Cap on kept items (default: all for jev, 40 for deepseek).")
    parser.add_argument("--dropped", type=int, default=None, help="Cap on dropped items (default: all for jev, 40 for deepseek).")
    parser.add_argument("--model", default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    llm = load_script("ab_llm")
    judge = load_script("ab_judge")
    compare = load_script("ab_compare")
    jev = load_script("jev_judge")
    judger = select_judger(args.judge, llm, judge, jev, args.model)
    return run(args.repo_root.resolve(), args.date, args.kept, args.dropped, judger, compare)


if __name__ == "__main__":
    sys.exit(main())
