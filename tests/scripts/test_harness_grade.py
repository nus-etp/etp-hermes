"""Unit tests for scripts/harness/grade.py — the harness ACCEPT box."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import grade, scenarios

RUNTIMES = ("nvidia", "zai")


def make_card(root: Path, expected: str, *, company: str | None = "Acme", origin_kind: str = "ab_seed",
              graders: list[str] | None = None) -> str:
    card = {
        "id": scenarios.next_id("ingest", "2026-10-01", root),
        "layer": "ingest",
        "version": 1,
        "status": "active",
        "origin": {"kind": origin_kind, "date": "2026-10-01"},
        "persona": "Layer 1 relevance pass",
        "goal": "grade test",
        "input": {"candidate": {"headline": "h", "company": "Acme"}, "companies": {"Acme": "SG robotics"}},
        "expected": {"verdict": expected, "company": company if expected == "keep" else None},
        "cluster": "ingest/false_keep/weak_term/acme",
        "graders": graders or ["verdict_match", "company_match", "blind_judge"],
        "created_by": "test",
    }
    scenarios.save(card, root)
    return card["id"]


def window_item(index: int, verdict: str | None) -> dict:
    return {"date": "2026-10-02", "url": f"https://news.test/{index}", "company": "Acme",
            "headline": f"item {index}", "description": "", "source": "TC",
            "company_description": "SG robotics", "verdict": verdict}


def sim(arm: str, runtime: str, scenario_results: dict, window: list[dict], usage: int | None = None) -> dict:
    result = {"arm": arm, "runtime": runtime, "layer": "ingest", "implemented": True,
              "scenario_results": scenario_results, "window_results": window}
    if usage is not None:
        result["usage"] = {"total_tokens": usage}
    return result


def correct_results(root: Path) -> dict:
    results = {}
    for card in scenarios.load_all(root, layer="ingest"):
        expected = card["expected"]
        results[card["id"]] = {"verdict": expected["verdict"], "company": expected["company"], "raw": "{}"}
    return results


class FakeJudge:
    def __init__(self, verdict: str = "keep") -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def __call__(self, messages, *, model=None, max_tokens=512, runtime="deepseek", **_):
        self.calls.append(runtime)
        return json.dumps({"verdict": self.verdict, "reason": "test"})


@pytest.fixture()
def judge(monkeypatch) -> FakeJudge:
    fake = FakeJudge()
    monkeypatch.setattr(grade.ab_llm, "chat", fake)
    monkeypatch.setattr(grade.ab_llm, "have_key", lambda runtime="deepseek": True)
    return fake


@pytest.fixture()
def workspace(tmp_path: Path) -> dict:
    root = tmp_path / "scenarios"
    make_card(root, "keep")
    make_card(root, "drop")
    return {"root": root, "dir": tmp_path}


def write_sims(workspace: dict, sims: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {"champion": [], "proposal": []}
    for arm, by_runtime in sims.items():
        for runtime, payload in by_runtime.items():
            path = workspace["dir"] / "sim" / f"{arm}-{runtime}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
            paths[arm].append(str(path))
    return paths


def run_grade(workspace: dict, sims: dict[str, dict[str, dict]], *extra: str) -> tuple[dict, str]:
    paths = write_sims(workspace, sims)
    verdict_path = workspace["dir"] / "out" / "verdict.json"
    table_path = workspace["dir"] / "out" / "replay-table.md"
    exit_code = grade.main([
        "--champion", *paths["champion"], "--proposal", *paths["proposal"],
        "--scenarios", str(workspace["root"]), "--out-table", str(table_path),
        "--out-verdict", str(verdict_path), "--judge-cache", str(workspace["dir"] / "judge-cache.jsonl"),
        "--sleep", "0", *extra,
    ])
    assert exit_code == 0
    return json.loads(verdict_path.read_text()), table_path.read_text()


def baseline_window(count: int = 10) -> list[dict]:
    return [window_item(i, "keep" if i % 2 else "drop") for i in range(count)]


def both_runtimes(arm: str, scenario_results: dict, window: list[dict], usage: int | None = None) -> dict:
    return {rt: sim(arm, rt, scenario_results, window, usage) for rt in RUNTIMES}


PER_RUNTIME_KEYS = {"scenarios_passed", "scenarios_total", "failed_ids", "champion_right",
                    "proposal_right", "p_value", "volume_ratio", "cost_delta"}


def test_accept_path(workspace, judge) -> None:
    results = correct_results(workspace["root"])
    verdict, table = run_grade(workspace, {
        "champion": both_runtimes("champion", results, baseline_window()),
        "proposal": both_runtimes("proposal", results, baseline_window()),
    })
    assert verdict["accept"] is True and verdict["reasons"] == []
    assert set(verdict) == {"accept", "reasons", "per_runtime"}
    assert set(verdict["per_runtime"]) == set(RUNTIMES)
    for row in verdict["per_runtime"].values():
        assert set(row) == PER_RUNTIME_KEYS
        assert row["scenarios_passed"] == row["scenarios_total"] == 2
        assert row["volume_ratio"] == 1.0 and row["cost_delta"] == 0.0
    assert "**Decision: ACCEPT**" in table
    assert table.count("| nvidia |") == 1 and table.count("| zai |") == 1
    assert judge.calls == []


def test_scenario_failure_on_one_runtime_rejects(workspace, judge) -> None:
    good = correct_results(workspace["root"])
    keep_id = next(cid for cid, r in good.items() if r["verdict"] == "keep")
    bad = {**good, keep_id: {"verdict": "drop", "company": None, "raw": "{}"}}
    proposals = both_runtimes("proposal", good, baseline_window())
    proposals["zai"] = sim("proposal", "zai", bad, baseline_window())
    verdict, table = run_grade(workspace, {
        "champion": both_runtimes("champion", good, baseline_window()),
        "proposal": proposals,
    })
    assert verdict["accept"] is False
    assert verdict["per_runtime"]["nvidia"]["failed_ids"] == []
    assert verdict["per_runtime"]["zai"]["failed_ids"] == [keep_id]
    assert verdict["per_runtime"]["zai"]["scenarios_passed"] == 1
    assert any(keep_id in reason and reason.startswith("zai") for reason in verdict["reasons"])
    assert keep_id in table and "**Decision: REJECT**" in table


def test_company_mismatch_fails_card(workspace, judge) -> None:
    good = correct_results(workspace["root"])
    keep_id = next(cid for cid, r in good.items() if r["verdict"] == "keep")
    wrong_company = {**good, keep_id: {"verdict": "keep", "company": "Other Co", "raw": "{}"}}
    verdict, _ = run_grade(workspace, {
        "champion": both_runtimes("champion", good, baseline_window()),
        "proposal": both_runtimes("proposal", wrong_company, baseline_window()),
    })
    assert verdict["per_runtime"]["nvidia"]["failed_ids"] == [keep_id]


def test_human_cards_weighted_and_inapplicable_graders_pass(tmp_path, judge) -> None:
    root = tmp_path / "scenarios"
    human_id = make_card(root, "drop", origin_kind="human", graders=["verdict_match", "op_budget"])
    workspace = {"root": root, "dir": tmp_path}
    results = correct_results(root)
    verdict, table = run_grade(workspace, {
        "champion": both_runtimes("champion", results, baseline_window()),
        "proposal": both_runtimes("proposal", results, baseline_window()),
    })
    assert verdict["accept"] is True
    assert verdict["per_runtime"]["nvidia"]["scenarios_total"] == 3
    assert f"{human_id}: grader op_budget not applicable" in table


def regression_sims(workspace: dict) -> dict:
    results = correct_results(workspace["root"])
    champion_window = [window_item(i, "keep") for i in range(10)] + [window_item(i, "drop") for i in range(10, 20)]
    proposal_window = [window_item(i, "drop") for i in range(10)] + [window_item(i, "keep") for i in range(10, 20)]
    return {
        "champion": both_runtimes("champion", results, champion_window),
        "proposal": both_runtimes("proposal", results, proposal_window),
    }


def test_significant_window_regression_rejects(workspace, judge, monkeypatch) -> None:
    sims = regression_sims(workspace)
    verdicts = {f"item {i}": ("keep" if i < 10 else "drop") for i in range(20)}

    def judge_by_headline(messages, *, model=None, max_tokens=512, runtime="deepseek", **_):
        judge.calls.append(runtime)
        headline = json.loads(messages[1]["content"])["headline"]
        return json.dumps({"verdict": verdicts[headline], "reason": "r"})

    monkeypatch.setattr(grade.ab_llm, "chat", judge_by_headline)
    verdict, _ = run_grade(workspace, sims)
    assert verdict["accept"] is False
    for row in verdict["per_runtime"].values():
        assert row["champion_right"] == 20 and row["proposal_right"] == 0
        assert row["p_value"] < 0.05
    assert any("significant window regression" in reason for reason in verdict["reasons"])
    assert set(judge.calls) == {"deepseek"}
    assert len(judge.calls) == 20


def test_judge_cache_reused_across_runtimes_and_reruns(workspace, judge) -> None:
    sims = regression_sims(workspace)
    run_grade(workspace, sims)
    assert len(judge.calls) == 20
    cache_rows = (workspace["dir"] / "judge-cache.jsonl").read_text().splitlines()
    assert len(cache_rows) == 20
    assert {json.loads(row)["runtime"] for row in cache_rows} == {"deepseek"}
    run_grade(workspace, sims)
    assert len(judge.calls) == 20


def test_judge_runtime_flag_uses_separate_cache_key(workspace, judge) -> None:
    sims = regression_sims(workspace)
    run_grade(workspace, sims)
    run_grade(workspace, sims, "--judge-runtime", "zai")
    assert judge.calls.count("zai") == 20


def test_volume_guardrail_breach_rejects(workspace, judge) -> None:
    results = correct_results(workspace["root"])
    champion_window = [window_item(i, "keep" if i < 2 else "drop") for i in range(10)]
    proposal_window = [window_item(i, "keep") for i in range(10)]
    verdict, _ = run_grade(workspace, {
        "champion": both_runtimes("champion", results, champion_window),
        "proposal": both_runtimes("proposal", results, proposal_window),
    })
    assert verdict["accept"] is False
    assert verdict["per_runtime"]["nvidia"]["volume_ratio"] == 5.0
    assert any("volume guardrail warn_high" in reason for reason in verdict["reasons"])


def test_cost_delta_over_threshold_rejects(workspace, judge) -> None:
    results = correct_results(workspace["root"])
    verdict, _ = run_grade(workspace, {
        "champion": both_runtimes("champion", results, baseline_window(), usage=1000),
        "proposal": both_runtimes("proposal", results, baseline_window(), usage=1200),
    })
    assert verdict["per_runtime"]["nvidia"]["cost_delta"] == 0.2
    assert any("cost delta" in reason for reason in verdict["reasons"])


def test_missing_runtime_pair_rejects(workspace, judge) -> None:
    results = correct_results(workspace["root"])
    verdict, _ = run_grade(workspace, {
        "champion": both_runtimes("champion", results, baseline_window()),
        "proposal": {"nvidia": sim("proposal", "nvidia", results, baseline_window())},
    })
    assert verdict["accept"] is False
    assert "zai: missing proposal simulation" in verdict["reasons"]


def test_unimplemented_layer_rejects(workspace, judge) -> None:
    empty = {"arm": "x", "runtime": "nvidia", "layer": "synthesis", "implemented": False,
             "scenario_results": {}, "window_results": []}
    verdict, _ = run_grade(workspace, {"champion": {"nvidia": empty}, "proposal": {"nvidia": empty}})
    assert verdict["accept"] is False
    assert "nvidia: layer simulation not implemented" in verdict["reasons"]


def test_unlabeled_disagreements_without_judge_key(workspace, monkeypatch) -> None:
    monkeypatch.setattr(grade.ab_llm, "have_key", lambda runtime="deepseek": False)
    verdict, table = run_grade(workspace, regression_sims(workspace))
    row = verdict["per_runtime"]["nvidia"]
    assert row["champion_right"] == 0 and row["proposal_right"] == 0 and row["p_value"] is None
    assert "left unlabeled" in table
