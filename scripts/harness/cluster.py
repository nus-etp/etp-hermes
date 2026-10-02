#!/usr/bin/env python3
"""Pick the top failure cluster for the weekly harness optimise loop.

Deterministic pre-step of `harness-optimise.yml` (docs/self-optimising-harness.md
§5). Groups every active scenario card by its `cluster` key, sizes each group
by card count and by grading weight (`scenarios.weight`: human-origin cards
count 3x), and marks a group eligible when its weighted size reaches
`--min-size` or it holds any human-origin card. The top eligible group wins:
weighted size descending, then oldest `origin.date`, then key ascending.

Writes `data/harness/cluster.json` — the contract fields (`cluster`, `size`,
`weighted_size`, `scenario_ids`, `layer`, `failure_kind`, `root_term`,
`eligible`) plus `host`, `slug`, `companies` and a `cards` summary list so the
cluster and propose prompts read one file. Exits 0 with `eligible: false`
when nothing qualifies. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios

DEFAULT_MIN_SIZE = 3
DEFAULT_OUT = scenarios.REPO_ROOT / "data" / "harness" / "cluster.json"
SUMMARY_MAX_CHARS = 400


def group_by_cluster(cards: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for card in cards:
        groups.setdefault(str(card["cluster"]), []).append(card)
    return groups


def origin_date(card: dict) -> str:
    return str(card.get("origin", {}).get("date", "9999-99-99"))


def is_human(card: dict) -> bool:
    return card.get("origin", {}).get("kind") == "human"


def describe_group(key: str, cards: list[dict], min_size: int) -> dict:
    weighted_size = sum(scenarios.weight(card) for card in cards)
    return {
        "cluster": key,
        "size": len(cards),
        "weighted_size": weighted_size,
        "has_human": any(is_human(card) for card in cards),
        "oldest_date": min(origin_date(card) for card in cards),
        "eligible": weighted_size >= min_size or any(is_human(card) for card in cards),
    }


def rank_key(group: dict) -> tuple:
    return (-group["weighted_size"], group["oldest_date"], group["cluster"])


def split_key(key: str) -> dict:
    parts = key.split("/")
    return {
        "layer": parts[0] if parts else None,
        "failure_kind": parts[1] if len(parts) > 1 else None,
        "root_term": parts[2] if len(parts) > 2 else None,
        "host": parts[3] if len(parts) > 3 else None,
    }


def cluster_slug(key: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", key.lower()).strip("-")


def card_companies(card: dict) -> set[str]:
    names: set[str] = set()
    expected_company = card.get("expected", {}).get("company")
    if isinstance(expected_company, str) and expected_company:
        names.add(expected_company)
    card_input = card.get("input", {})
    companies = card_input.get("companies")
    if isinstance(companies, dict):
        names.update(str(name) for name in companies)
    candidate = card_input.get("candidate")
    if isinstance(candidate, dict):
        if isinstance(candidate.get("company"), str) and candidate["company"]:
            names.add(candidate["company"])
        matched = candidate.get("matched")
        if isinstance(matched, list):
            names.update(str(name) for name in matched if name)
    company = card_input.get("company")
    if isinstance(company, str) and company:
        names.add(company)
    return names


def summarize_input(card_input: dict) -> dict:
    candidate = card_input.get("candidate")
    if isinstance(candidate, dict):
        companies = card_input.get("companies")
        return {
            "title": candidate.get("title") or candidate.get("headline"),
            "description": str(candidate.get("description") or "")[:SUMMARY_MAX_CHARS],
            "url": candidate.get("url") or candidate.get("link"),
            "source": candidate.get("source"),
            "matched": candidate.get("matched"),
            "companies": sorted(companies) if isinstance(companies, dict) else [],
        }
    return {"raw": json.dumps(card_input, ensure_ascii=False, sort_keys=True)[:SUMMARY_MAX_CHARS]}


def summarize_card(card: dict) -> dict:
    return {
        "id": card["id"],
        "origin_kind": card.get("origin", {}).get("kind"),
        "origin_date": origin_date(card),
        "goal": card.get("goal"),
        "expected": card.get("expected"),
        "input": summarize_input(card.get("input", {})),
    }


def empty_result(groups_considered: int) -> dict:
    return {
        "cluster": None,
        "slug": None,
        "size": 0,
        "weighted_size": 0,
        "scenario_ids": [],
        "layer": None,
        "failure_kind": None,
        "root_term": None,
        "host": None,
        "eligible": False,
        "companies": [],
        "cards": [],
        "root_cause": None,
        "clusters_considered": groups_considered,
        "clusters_eligible": 0,
    }


def select_cluster(cards: list[dict], min_size: int = DEFAULT_MIN_SIZE) -> dict:
    groups = group_by_cluster(cards)
    described = [describe_group(key, members, min_size) for key, members in groups.items()]
    eligible = sorted((group for group in described if group["eligible"]), key=rank_key)
    if not eligible:
        return empty_result(len(described))
    top = eligible[0]
    members = sorted(groups[top["cluster"]], key=lambda card: card["id"])
    companies = set().union(*(card_companies(card) for card in members))
    return {
        "cluster": top["cluster"],
        "slug": cluster_slug(top["cluster"]),
        "size": top["size"],
        "weighted_size": top["weighted_size"],
        "scenario_ids": [card["id"] for card in members],
        **split_key(top["cluster"]),
        "eligible": True,
        "companies": sorted(companies),
        "cards": [summarize_card(card) for card in members],
        "root_cause": None,
        "clusters_considered": len(described),
        "clusters_eligible": len(eligible),
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scenarios", type=Path, default=scenarios.SCENARIOS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cards = scenarios.load_all(args.scenarios, status="active")
    result = select_cluster(cards, args.min_size)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result["eligible"]:
        print(
            f"cluster: {result['cluster']} size={result['size']} weighted={result['weighted_size']} "
            f"({result['clusters_eligible']}/{result['clusters_considered']} clusters eligible)"
        )
    else:
        print(f"no eligible cluster ({result['clusters_considered']} clusters, min weighted size {args.min_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
