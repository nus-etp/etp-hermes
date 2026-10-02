#!/usr/bin/env python3
"""Replay a layer prompt over the scenario corpus and a fresh candidate window.

The simulator of the self-optimising harness (docs/self-optimising-harness.md
§2 C, docs/harness-interfaces.md PR2). For ``--layer ingest`` it feeds each
active scenario card's frozen candidate, then every candidate in the trailing
``--window-days`` of ``data/replay/<date>.json`` snapshots, to the prompt at
``--prompt-path`` in single-candidate override mode — the same user message
``ab_backfill.arm_keep`` builds — on the provider selected by ``--runtime``.
The override asks for ``{"keep": bool, "company": str | null}``; each reply is
recorded as verdict ``keep`` / ``drop`` / ``null`` (call or parse failure).

The live watchlist (``--companies``, default ``./data/companies.json`` so a
reverted worktree replays its own config) is applied on top of the frozen
inputs: the matched company's live ``description`` replaces the card/snapshot
one, and its live ``exclude_terms`` veto the candidate deterministically
(``entity_terms.is_excluded``) — recorded as ``drop`` with ``via:
"exclude_terms"`` and no model call. A rung-1 config proposal therefore
changes the replay exactly as it would change production.

Run once per (arm, runtime); ``grade.py`` pairs the outputs. ``pre_extracted``
window candidates auto-keep in production and are skipped; the window is
deduplicated by normalized URL (newest snapshot wins) and capped at
``--limit``. Layers ``agent_supplement`` / ``synthesis`` are accepted but not
implemented yet: they write an empty result and exit 0.

Fail-open per item, exit 0; exit 2 when the runtime has no API key. Pure
stdlib (uses ``scripts/ab_llm.py``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ab_compare
import ab_llm
import scenarios

entity_terms = importlib.import_module("entity_terms")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOW_DIR = REPO_ROOT / "data" / "replay"
IMPLEMENTED_LAYERS = ("ingest",)
SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
RAW_REPLY_CHARS = 500

REPLAY_OVERRIDE = (
    "\n\n---\n\nHARNESS REPLAY MODE. Ignore every instruction above about fetching "
    "URLs, deduplication, seen-urls files, writing output files, and stdout "
    "format. Apply ONLY this prompt's relevance policy to the SINGLE candidate in "
    "the user message and respond with ONLY a JSON object: "
    '{"keep": true | false, "company": "<watchlist company name>" | null}. '
    "Use null for company when the item should be dropped. No prose."
)


def company_description(companies: dict, name: str) -> str:
    entry = companies.get(name)
    if isinstance(entry, dict):
        entry = entry.get("description")
    return (entry or "").strip() if isinstance(entry, str) else ""


def candidate_company(candidate: dict) -> str:
    company = candidate.get("company")
    if company:
        return company
    matched = candidate.get("matched") or []
    return matched[0] if matched else ""


def candidate_url(candidate: dict) -> str:
    link = candidate.get("link") or candidate.get("url") or ""
    return ab_compare.normalize_url(link) if link else ""


def build_user_message(candidate: dict, companies: dict) -> str:
    company = candidate_company(candidate)
    fields = {
        "company": company,
        "company_description": company_description(companies, company),
        "headline": candidate.get("headline") or candidate.get("title") or "",
        "description": candidate.get("description", ""),
        "source": candidate.get("source", ""),
        "source_kind": candidate.get("source_kind", ""),
    }
    return json.dumps(fields, ensure_ascii=False)


def parse_reply(reply: str | None) -> tuple[str | None, str | None]:
    parsed = ab_llm.extract_json(reply) if reply else None
    if not parsed or "keep" not in parsed:
        return None, None
    verdict = "keep" if bool(parsed["keep"]) else "drop"
    company = parsed.get("company")
    company = company.strip() if isinstance(company, str) and company.strip() else None
    return verdict, company


def resolve_companies_path(value: Path | None) -> Path:
    if value is not None:
        return value
    local = Path.cwd() / "data" / "companies.json"
    return local if local.exists() else REPO_ROOT / "data" / "companies.json"


def load_live_companies(path: Path) -> dict[str, dict]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"no readable watchlist at {path}; replaying frozen descriptions only", file=sys.stderr)
        return {}
    if not isinstance(entries, list):
        return {}
    return {entry["name"]: entry for entry in entries if isinstance(entry, dict) and entry.get("name")}


def overlay_live_description(companies: dict, live: dict[str, dict], name: str) -> dict:
    description = (live.get(name) or {}).get("description")
    if not isinstance(description, str) or not description.strip():
        return companies
    return {**companies, name: description}


def vetoed_by_exclude_terms(candidate: dict, live: dict[str, dict]) -> bool:
    entry = live.get(candidate_company(candidate))
    if not entry:
        return False
    matcher = entity_terms.build_company_matchers([entry])[entry["name"]]
    headline = candidate.get("headline") or candidate.get("title") or ""
    return entity_terms.is_excluded(matcher, headline, candidate.get("description") or "")


def call_model(system_prompt: str, candidate: dict, companies: dict, args: argparse.Namespace) -> dict:
    reply = ab_llm.chat(
        [
            {"role": "system", "content": system_prompt + REPLAY_OVERRIDE},
            {"role": "user", "content": build_user_message(candidate, companies)},
        ],
        model=args.model,
        max_tokens=80,
        runtime=args.runtime,
    )
    time.sleep(args.sleep)
    verdict, company = parse_reply(reply)
    return {"verdict": verdict, "company": company, "raw": (reply or "")[:RAW_REPLY_CHARS] or None, "via": "llm"}


def replay_candidate(system_prompt: str, candidate: dict, companies: dict, args: argparse.Namespace) -> dict:
    if vetoed_by_exclude_terms(candidate, args.live_companies):
        return {"verdict": "drop", "company": None, "raw": None, "via": "exclude_terms"}
    return call_model(system_prompt, candidate, companies, args)


def snapshot_date(path: Path) -> dt.date | None:
    match = SNAPSHOT_RE.match(path.stem)
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError:
        return None


def window_snapshots(window_dir: Path, today: dt.date, window_days: int) -> list[tuple[dt.date, Path]]:
    if not window_dir.is_dir():
        return []
    selected = []
    for path in window_dir.glob("*.json"):
        date = snapshot_date(path)
        if date is None:
            continue
        age_days = (today - date).days
        if 0 <= age_days < window_days:
            selected.append((date, path))
    return sorted(selected, reverse=True)


def load_snapshot(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"skip unreadable replay snapshot {path}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def window_items(window_dir: Path, today: dt.date, window_days: int, limit: int) -> list[tuple[str, dict, dict]]:
    items: list[tuple[str, dict, dict]] = []
    seen_urls: set[str] = set()
    for date, path in window_snapshots(window_dir, today, window_days):
        snapshot = load_snapshot(path)
        companies = snapshot.get("companies") or {}
        for candidate in snapshot.get("candidates") or []:
            url = candidate_url(candidate)
            if not url or candidate.get("pre_extracted") or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append((date.isoformat(), candidate, companies))
            if 0 < limit <= len(items):
                return items
    return items


def simulate_scenarios(cards: list[dict], system_prompt: str, args: argparse.Namespace) -> dict:
    results = {}
    for card in cards:
        card_input = card.get("input") or {}
        candidate = card_input.get("candidate") or {}
        companies = overlay_live_description(
            card_input.get("companies") or {}, args.live_companies, candidate_company(candidate)
        )
        results[card["id"]] = replay_candidate(system_prompt, candidate, companies, args)
    return results


def simulate_window(items: list[tuple[str, dict, dict]], system_prompt: str, args: argparse.Namespace) -> list[dict]:
    results = []
    for date, candidate, snapshot_companies in items:
        company = candidate_company(candidate)
        companies = overlay_live_description(snapshot_companies, args.live_companies, company)
        outcome = replay_candidate(system_prompt, candidate, companies, args)
        results.append(
            {
                "date": date,
                "url": candidate_url(candidate),
                "company": company,
                "headline": candidate.get("headline") or candidate.get("title") or "",
                "description": (candidate.get("description") or "").strip(),
                "source": candidate.get("source", ""),
                "company_description": company_description(companies, company),
                "verdict": outcome["verdict"],
                "predicted_company": outcome["company"],
                "via": outcome["via"],
            }
        )
    return results


def empty_result(args: argparse.Namespace, implemented: bool) -> dict:
    return {
        "arm": args.arm,
        "runtime": args.runtime,
        "layer": args.layer,
        "prompt_path": str(args.prompt_path),
        "companies_path": str(args.companies),
        "implemented": implemented,
        "scenario_results": {},
        "window_results": [],
    }


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_arm(out_path: Path, runtime: str) -> str:
    stem = out_path.stem
    suffix = f"-{runtime}"
    return stem[: -len(suffix)] if stem.endswith(suffix) else stem


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layer", required=True, choices=scenarios.LAYERS)
    parser.add_argument("--prompt-path", required=True, type=Path)
    parser.add_argument("--runtime", required=True, choices=sorted(ab_llm.RUNTIMES))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arm", default=None, help="Arm label (default: --out stem minus -<runtime>).")
    parser.add_argument("--scenarios", type=Path, default=scenarios.SCENARIOS_DIR)
    parser.add_argument("--window", type=Path, default=DEFAULT_WINDOW_DIR)
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--today", default=None, help="YYYY-MM-DD anchor for the window (default: UTC today).")
    parser.add_argument("--limit", type=int, default=0, help="Max window candidates (0 = all).")
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds between model calls.")
    parser.add_argument("--model", default=None, help="Override the runtime's model.")
    parser.add_argument(
        "--companies", type=Path, default=None,
        help="Live watchlist (default: ./data/companies.json, else the repo's).",
    )
    args = parser.parse_args(argv)
    args.arm = args.arm or default_arm(args.out, args.runtime)
    args.companies = resolve_companies_path(args.companies)
    args.live_companies = {}
    return args


def resolve_today(value: str | None) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now(dt.timezone.utc).date()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.layer not in IMPLEMENTED_LAYERS:
        print(f"layer {args.layer}: not implemented in this PR; writing an empty result")
        write_result(args.out, empty_result(args, implemented=False))
        return 0
    if not ab_llm.have_key(args.runtime):
        print(f"no API key for runtime {args.runtime}; cannot simulate", file=sys.stderr)
        return 2

    system_prompt = args.prompt_path.read_text(encoding="utf-8")
    args.live_companies = load_live_companies(args.companies)
    cards = scenarios.load_all(args.scenarios, layer=args.layer, status="active")
    items = window_items(args.window, resolve_today(args.today), args.window_days, args.limit)
    usage_before = ab_llm.usage_total(args.runtime)

    result = empty_result(args, implemented=True)
    result["scenario_results"] = simulate_scenarios(cards, system_prompt, args)
    result["window_results"] = simulate_window(items, system_prompt, args)
    tokens = ab_llm.usage_total(args.runtime) - usage_before
    if tokens > 0:
        result["usage"] = {"total_tokens": tokens}
    write_result(args.out, result)

    failed = sum(1 for r in result["scenario_results"].values() if r["verdict"] is None)
    failed += sum(1 for r in result["window_results"] if r["verdict"] is None)
    print(
        f"{args.arm}/{args.runtime}: {len(cards)} scenarios, {len(items)} window candidates, "
        f"{failed} null verdicts -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
