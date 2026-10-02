#!/usr/bin/env python3
"""Validate the committed scenario corpus.

Walks ``evals/scenarios/`` and reports every problem at once: unparseable
JSON, cards that fail ``scenarios.validate``, a filename that is not
``<id>.json``, a card filed under the wrong layer directory, JSON files
outside a known layer directory, duplicate ids, and two active cards in the
same layer replaying the same input URL. Exit 1 with a readable list on any
problem, else exit 0. Runs in the evals.yml fast job. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios


def read_card(path: Path) -> tuple[dict | None, str | None]:
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, f"unreadable JSON ({error})"
    if not isinstance(card, dict):
        return None, "card is not a JSON object"
    return card, None


def placement_problems(card: dict, path: Path, root: Path) -> list[str]:
    problems: list[str] = []
    if path.stem != card.get("id"):
        problems.append(f"filename does not match id {card.get('id')!r}")
    if path.parent.name != card.get("layer"):
        problems.append(f"filed under {path.parent.name}/ but layer is {card.get('layer')!r}")
    return problems


def stray_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.json"))
        if path.parent.parent != root or path.parent.name not in scenarios.LAYERS
    ]


def check(root: Path) -> tuple[int, list[str]]:
    problems: list[str] = []
    ids: dict[str, list[Path]] = defaultdict(list)
    active_urls: dict[tuple[str, str], list[str]] = defaultdict(list)
    count = 0
    for path in stray_files(root):
        problems.append(f"{path.relative_to(root)}: not under a known layer directory")
    for layer in scenarios.LAYERS:
        for path in sorted((root / layer).glob("*.json")):
            count += 1
            label = str(path.relative_to(root))
            card, error = read_card(path)
            if card is None:
                problems.append(f"{label}: {error}")
                continue
            problems.extend(f"{label}: {problem}" for problem in scenarios.validate(card))
            problems.extend(f"{label}: {problem}" for problem in placement_problems(card, path, root))
            ids[str(card.get("id"))].append(path)
            url = scenarios.card_url(card)
            if url and card.get("status") == "active":
                active_urls[(layer, url)].append(str(card.get("id")))
    for card_id, paths in ids.items():
        if len(paths) > 1:
            problems.append(f"duplicate id {card_id}: " + ", ".join(str(p.relative_to(root)) for p in paths))
    for (layer, url), card_ids in active_urls.items():
        if len(card_ids) > 1:
            problems.append(f"duplicate active {layer} url {url}: " + ", ".join(card_ids))
    return count, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=scenarios.SCENARIOS_DIR)
    args = parser.parse_args(argv)

    count, problems = check(args.root)
    if problems:
        print(f"check_scenarios: {len(problems)} problem(s) in {count} card(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"check_scenarios: {count} card(s) ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
