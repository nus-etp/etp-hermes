#!/usr/bin/env python3
"""Scenario cards — the shared contract of the self-optimising harness.

A scenario card freezes one production failure into a replayable test. All
harness scripts (write_scenarios, simulate, grade, cluster, prune) read and
write cards only through this module. See docs/self-optimising-harness.md §3.

Layout: evals/scenarios/<layer>/<id>.json, one card per file, append-only
(superseded cards get status "retired", never deleted). Pure stdlib.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "evals" / "scenarios"

LAYERS = ("ingest", "agent_supplement", "synthesis")
STATUSES = ("active", "retired", "quarantined")
ORIGIN_KINDS = (
    "judge_disagreement",
    "exclude_leak",
    "dedup_miss",
    "unanswered_question",
    "budget_shortfall",
    "template_drift",
    "human",
    "ab_seed",
)
GRADERS = (
    "verdict_match",
    "company_match",
    "blind_judge",
    "template_invariants",
    "questions_retired",
    "no_fabrication",
    "op_budget",
)
HUMAN_WEIGHT = 3

ID_RE = re.compile(r"^(ingest|agent_supplement|synthesis)-\d{4}-\d{2}-\d{2}-\d{4}$")
REQUIRED_KEYS = (
    "id",
    "layer",
    "version",
    "status",
    "origin",
    "persona",
    "goal",
    "input",
    "expected",
    "cluster",
    "graders",
    "created_by",
)


class ScenarioError(ValueError):
    pass


def validate(card: dict) -> list[str]:
    """Return a list of problems; empty means the card is well-formed."""
    problems: list[str] = []
    for key in REQUIRED_KEYS:
        if key not in card:
            problems.append(f"missing key: {key}")
    if problems:
        return problems
    if not ID_RE.match(str(card["id"])):
        problems.append(f"bad id: {card['id']}")
    if card["layer"] not in LAYERS:
        problems.append(f"bad layer: {card['layer']}")
    if not str(card["id"]).startswith(f"{card['layer']}-"):
        problems.append("id prefix does not match layer")
    if card["status"] not in STATUSES:
        problems.append(f"bad status: {card['status']}")
    if not isinstance(card["version"], int) or card["version"] < 1:
        problems.append("version must be a positive int")
    origin = card["origin"]
    if not isinstance(origin, dict) or origin.get("kind") not in ORIGIN_KINDS:
        problems.append("origin.kind invalid")
    if not isinstance(origin, dict) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(origin.get("date", ""))):
        problems.append("origin.date must be YYYY-MM-DD")
    if not isinstance(card["input"], dict) or not isinstance(card["expected"], dict):
        problems.append("input and expected must be objects")
    if not isinstance(card["graders"], list) or not card["graders"]:
        problems.append("graders must be a non-empty list")
    else:
        for g in card["graders"]:
            if g not in GRADERS:
                problems.append(f"unknown grader: {g}")
    cluster = str(card["cluster"])
    if cluster.count("/") < 2 or not cluster.startswith(f"{card['layer']}/"):
        problems.append("cluster must be <layer>/<failure_kind>/<root_term>[/<host>]")
    return problems


def card_path(card: dict, root: Path = SCENARIOS_DIR) -> Path:
    return root / card["layer"] / f"{card['id']}.json"


def load_all(root: Path = SCENARIOS_DIR, *, layer: str | None = None, status: str | None = "active") -> list[dict]:
    """Load cards sorted by id. status=None loads every status."""
    cards: list[dict] = []
    layers = [layer] if layer else list(LAYERS)
    for lay in layers:
        for path in sorted((root / lay).glob("*.json")):
            card = json.loads(path.read_text(encoding="utf-8"))
            if status is not None and card.get("status") != status:
                continue
            cards.append(card)
    return cards


def save(card: dict, root: Path = SCENARIOS_DIR) -> Path:
    problems = validate(card)
    if problems:
        raise ScenarioError(f"{card.get('id')}: " + "; ".join(problems))
    path = card_path(card, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def next_id(layer: str, date: str, root: Path = SCENARIOS_DIR) -> str:
    prefix = f"{layer}-{date}-"
    existing = [p.stem for p in (root / layer).glob(f"{prefix}*.json")] if (root / layer).exists() else []
    seq = 0
    for stem in existing:
        try:
            seq = max(seq, int(stem[len(prefix):]))
        except ValueError:
            continue
    return f"{prefix}{seq + 1:04d}"


def weight(card: dict) -> int:
    return HUMAN_WEIGHT if card.get("origin", {}).get("kind") == "human" else 1


def cluster_key(layer: str, failure_kind: str, root_term: str, host: str | None = None) -> str:
    slug = lambda s: re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-") or "unknown"
    parts = [layer, slug(failure_kind), slug(root_term)]
    if host:
        parts.append(slug(host))
    return "/".join(parts)


def card_url(card: dict) -> str | None:
    """The input URL a card replays: the ingest candidate url, else input.url."""
    card_input = card.get("input")
    if not isinstance(card_input, dict):
        return None
    candidate = card_input.get("candidate")
    if isinstance(candidate, dict) and candidate.get("url"):
        return str(candidate["url"])
    url = card_input.get("url")
    return str(url) if url else None


def find_by_url(url: str, root: Path = SCENARIOS_DIR, *, layer: str | None = None) -> dict | None:
    """First card of any status whose input URL equals ``url``, else None."""
    for card in load_all(root, layer=layer, status=None):
        if card_url(card) == url:
            return card
    return None


def find_by_issue(number: int | None, root: Path = SCENARIOS_DIR) -> dict | None:
    """First card of any status created from GitHub issue ``number``, else None."""
    if number is None:
        return None
    for card in load_all(root, status=None):
        origin = card.get("origin")
        if isinstance(origin, dict) and origin.get("issue") == number:
            return card
    return None
