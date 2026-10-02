#!/usr/bin/env python3
"""Grade a proposal's replay against the champion's — the harness ACCEPT box.

Reads ``simulate.py`` outputs (``--champion`` / ``--proposal``, one file per
runtime each, paired by their ``runtime`` field) and applies the
pre-registered rule of docs/self-optimising-harness.md §4 per runtime:

1. **Scenarios** — every active card of the layer must pass every grader it
   lists. ``verdict_match`` and ``blind_judge`` compare the proposal's verdict
   with the card's ``expected.verdict`` (the expectation is already
   judge-derived, so no model is called); ``company_match`` compares the
   predicted company case-insensitively (an expected ``null`` passes on a drop
   or a null company). Graders that do not apply to the layer pass with a note.
   Pass counts are weighted by ``scenarios.weight`` (human cards count 3x).
2. **Fresh window** — items the two arms decided differently are labeled by
   the blind judge (``ab_judge.judge_row`` on ``--judge-runtime``); labels are
   cached in ``data/harness/judge-cache.jsonl`` keyed by (url, judge runtime) so
   reruns are free. ``ab_stats.right_arm`` / ``binom_two_sided`` give
   champion_right, proposal_right and the McNemar p-value; the keep-volume
   ratio is checked with ``ab_stats.compute_guardrail``'s band; cost_delta is
   the relative change in recorded ``usage.total_tokens`` (0.0 when absent).

ACCEPT iff, on every runtime: all active scenarios pass, no significant
window regression (proposal_right < champion_right with p < 0.05), volume
guardrail ok, cost_delta <= 0.15. Writes the markdown replay table and the
verdict JSON in the contract shape. Pure stdlib.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ab_llm
import scenarios

ab_judge = importlib.import_module("ab_judge")
ab_stats = importlib.import_module("ab_stats")

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = REPO_ROOT / "data" / "harness"
DEFAULT_CACHE = HARNESS_DIR / "judge-cache.jsonl"
CHAMPION_ARM = "v1"
PROPOSAL_ARM = "v2"
GUARDRAIL_LO = 0.5
GUARDRAIL_HI = 2.0
MAX_COST_DELTA = 0.15
LAYER_GRADERS = {"ingest": ("verdict_match", "company_match", "blind_judge")}


@dataclass
class ScenarioGrade:
    passed_weight: int = 0
    total_weight: int = 0
    failed_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class WindowGrade:
    champion_right: int = 0
    proposal_right: int = 0
    p_value: float | None = None
    discordant: int = 0
    unlabeled: int = 0
    champion_keeps: int = 0
    proposal_keeps: int = 0
    volume_ratio: float | None = None
    volume_status: str = "ok"


def load_sims(paths: list[Path]) -> dict[str, dict]:
    sims: dict[str, dict] = {}
    for path in paths:
        sim = json.loads(path.read_text(encoding="utf-8"))
        sims[sim["runtime"]] = sim
    return sims


def normalized(name: object) -> str | None:
    return name.strip().casefold() if isinstance(name, str) and name.strip() else None


def grade_verdict(card: dict, result: dict) -> bool:
    return result.get("verdict") == (card.get("expected") or {}).get("verdict")


def grade_company(card: dict, result: dict) -> bool:
    expected = normalized((card.get("expected") or {}).get("company"))
    predicted = normalized(result.get("company"))
    if expected is None:
        return predicted is None or result.get("verdict") == "drop"
    return predicted == expected


GRADER_FUNCTIONS = {
    "verdict_match": grade_verdict,
    "company_match": grade_company,
    "blind_judge": grade_verdict,
}


def card_passes(card: dict, result: dict | None, notes: list[str]) -> bool:
    if result is None:
        notes.append(f"{card['id']}: no proposal result")
        return False
    applicable = LAYER_GRADERS.get(card["layer"], ())
    for grader in card.get("graders", []):
        if grader not in applicable:
            notes.append(f"{card['id']}: grader {grader} not applicable to {card['layer']}, passed")
            continue
        if not GRADER_FUNCTIONS[grader](card, result):
            return False
    return True


def grade_scenarios(cards: list[dict], proposal: dict) -> ScenarioGrade:
    grade = ScenarioGrade()
    results = proposal.get("scenario_results") or {}
    for card in cards:
        card_weight = scenarios.weight(card)
        grade.total_weight += card_weight
        if card_passes(card, results.get(card["id"]), grade.notes):
            grade.passed_weight += card_weight
        else:
            grade.failed_ids.append(card["id"])
    return grade


def load_cache(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    cache = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("verdict") in {"keep", "drop"} and row.get("url") and row.get("runtime"):
            cache[(row["url"], row["runtime"])] = row
    return cache


def append_cache(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass
class Judge:
    runtime: str
    model: str | None
    cache_path: Path
    sleep: float
    cache: dict[tuple[str, str], dict] = field(default_factory=dict)

    def client(self) -> SimpleNamespace:
        runtime = self.runtime

        def chat(messages, **kwargs):
            return ab_llm.chat(messages, runtime=runtime, **kwargs)

        return SimpleNamespace(chat=chat, extract_json=ab_llm.extract_json)

    def label(self, item: dict) -> str | None:
        key = (item["url"], self.runtime)
        if key in self.cache:
            return self.cache[key]["verdict"]
        if not ab_llm.have_key(self.runtime):
            return None
        verdict, reason = ab_judge.judge_row(self.client(), item, self.model)
        time.sleep(self.sleep)
        if verdict is None:
            return None
        row = {"url": item["url"], "runtime": self.runtime, "verdict": verdict, "reason": reason, "model": self.model}
        self.cache[key] = row
        append_cache(self.cache_path, row)
        return verdict


def paired_window(champion: dict, proposal: dict) -> list[tuple[dict, dict]]:
    proposal_by_url = {item["url"]: item for item in proposal.get("window_results") or [] if item.get("url")}
    return [
        (item, proposal_by_url[item["url"]])
        for item in champion.get("window_results") or []
        if item.get("url") in proposal_by_url
    ]


def volume_status(champion_keeps: int, proposal_keeps: int) -> tuple[float | None, str]:
    metrics = [{"date": "window", "v1_items": champion_keeps, "v2_items": proposal_keeps}]
    guardrail = ab_stats.compute_guardrail(metrics, GUARDRAIL_LO, GUARDRAIL_HI, 0)
    if guardrail["status"] == "insufficient":
        return None, "ok" if proposal_keeps == 0 else "warn_high"
    return guardrail["ratio"], guardrail["status"]


def grade_window(champion: dict, proposal: dict, judge: Judge) -> WindowGrade:
    grade = WindowGrade()
    for champion_item, proposal_item in paired_window(champion, proposal):
        champion_verdict = champion_item.get("verdict")
        proposal_verdict = proposal_item.get("verdict")
        grade.champion_keeps += champion_verdict == "keep"
        grade.proposal_keeps += proposal_verdict == "keep"
        if champion_verdict is None or proposal_verdict is None or champion_verdict == proposal_verdict:
            continue
        grade.discordant += 1
        label = judge.label(champion_item)
        kept_by = CHAMPION_ARM if champion_verdict == "keep" else PROPOSAL_ARM
        right = ab_stats.right_arm({"kept_by": kept_by, "label": label})
        if right is None:
            grade.unlabeled += 1
        elif right == CHAMPION_ARM:
            grade.champion_right += 1
        else:
            grade.proposal_right += 1
    p_value = ab_stats.binom_two_sided(grade.champion_right, grade.proposal_right)
    grade.p_value = None if p_value is None else round(p_value, 5)
    grade.volume_ratio, grade.volume_status = volume_status(grade.champion_keeps, grade.proposal_keeps)
    return grade


def total_tokens(sim: dict) -> int:
    usage = sim.get("usage") or {}
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and tokens > 0 else 0


def cost_delta(champion: dict, proposal: dict) -> float:
    champion_tokens = total_tokens(champion)
    proposal_tokens = total_tokens(proposal)
    if champion_tokens == 0 or proposal_tokens == 0:
        return 0.0
    return round((proposal_tokens - champion_tokens) / champion_tokens, 4)


def significant_regression(window: WindowGrade) -> bool:
    return (
        window.proposal_right < window.champion_right
        and window.p_value is not None
        and window.p_value < ab_stats.ALPHA
    )


def runtime_reasons(runtime: str, row: dict, window: WindowGrade, implemented: bool) -> list[str]:
    reasons = []
    if not implemented:
        reasons.append(f"{runtime}: layer simulation not implemented")
    if row["failed_ids"]:
        reasons.append(f"{runtime}: scenarios failed: {', '.join(row['failed_ids'])}")
    if significant_regression(window):
        reasons.append(
            f"{runtime}: significant window regression "
            f"(champion_right={window.champion_right}, proposal_right={window.proposal_right}, p={window.p_value})"
        )
    if window.volume_status != "ok":
        reasons.append(f"{runtime}: volume guardrail {window.volume_status} (ratio={window.volume_ratio})")
    if row["cost_delta"] > MAX_COST_DELTA:
        reasons.append(f"{runtime}: cost delta {row['cost_delta']} > {MAX_COST_DELTA}")
    return reasons


def per_runtime_row(scenario_grade: ScenarioGrade, window: WindowGrade, delta: float) -> dict:
    return {
        "scenarios_passed": scenario_grade.passed_weight,
        "scenarios_total": scenario_grade.total_weight,
        "failed_ids": scenario_grade.failed_ids,
        "champion_right": window.champion_right,
        "proposal_right": window.proposal_right,
        "p_value": window.p_value,
        "volume_ratio": window.volume_ratio,
        "cost_delta": delta,
    }


def grade_runtime(champion: dict, proposal: dict, scenario_root: Path, judge: Judge) -> tuple[dict, list[str], list[str]]:
    layer = proposal.get("layer") or champion.get("layer")
    cards = scenarios.load_all(scenario_root, layer=layer, status="active")
    scenario_grade = grade_scenarios(cards, proposal)
    window = grade_window(champion, proposal, judge)
    row = per_runtime_row(scenario_grade, window, cost_delta(champion, proposal))
    implemented = proposal.get("implemented", True) and champion.get("implemented", True)
    reasons = runtime_reasons(proposal["runtime"], row, window, implemented)
    notes = list(scenario_grade.notes)
    if window.unlabeled:
        notes.append(f"{proposal['runtime']}: {window.unlabeled} window disagreements left unlabeled by the judge")
    return row, reasons, notes


def grade(champions: dict[str, dict], proposals: dict[str, dict], scenario_root: Path, judge: Judge) -> tuple[dict, list[str]]:
    reasons: list[str] = []
    notes: list[str] = []
    per_runtime: dict[str, dict] = {}
    runtimes = sorted(set(champions) | set(proposals))
    if not runtimes:
        reasons.append("no simulation files to grade")
    for runtime in runtimes:
        if runtime not in champions or runtime not in proposals:
            missing = "champion" if runtime not in champions else "proposal"
            reasons.append(f"{runtime}: missing {missing} simulation")
            continue
        row, runtime_failures, runtime_notes = grade_runtime(champions[runtime], proposals[runtime], scenario_root, judge)
        per_runtime[runtime] = row
        reasons.extend(runtime_failures)
        notes.extend(runtime_notes)
    verdict = {"accept": not reasons, "reasons": reasons, "per_runtime": per_runtime}
    return verdict, notes


def format_cell(value: object) -> str:
    return "n/a" if value is None else str(value)


def render_table(verdict: dict, notes: list[str]) -> str:
    lines = [
        "## Replay table",
        "",
        "| runtime | scenarios passed | failing scenarios | champion right | proposal right | p | volume ratio | cost delta |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for runtime, row in sorted(verdict["per_runtime"].items()):
        failing = ", ".join(row["failed_ids"]) or "none"
        lines.append(
            f"| {runtime} | {row['scenarios_passed']}/{row['scenarios_total']} | {failing} | "
            f"{row['champion_right']} | {row['proposal_right']} | {format_cell(row['p_value'])} | "
            f"{format_cell(row['volume_ratio'])} | {row['cost_delta']} |"
        )
    lines += ["", f"**Decision: {'ACCEPT' if verdict['accept'] else 'REJECT'}**", ""]
    lines += [f"- {reason}" for reason in verdict["reasons"]]
    if notes:
        lines += ["", "Notes:", ""] + [f"- {note}" for note in notes]
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--champion", nargs="+", action="extend", type=Path, required=True)
    parser.add_argument("--proposal", nargs="+", action="extend", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, default=scenarios.SCENARIOS_DIR)
    parser.add_argument("--out-table", type=Path, default=HARNESS_DIR / "replay-table.md")
    parser.add_argument("--out-verdict", type=Path, default=HARNESS_DIR / "verdict.json")
    parser.add_argument("--judge-runtime", default=ab_llm.DEFAULT_RUNTIME, choices=sorted(ab_llm.RUNTIMES))
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--judge-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds between judge calls.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    judge = Judge(args.judge_runtime, args.judge_model, args.judge_cache, args.sleep, load_cache(args.judge_cache))
    verdict, notes = grade(load_sims(args.champion), load_sims(args.proposal), args.scenarios, judge)
    write_text(args.out_verdict, json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(args.out_table, render_table(verdict, notes))
    print(f"{'ACCEPT' if verdict['accept'] else 'REJECT'}: {len(verdict['reasons'])} reasons -> {args.out_verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
