"""Unit tests for scripts/harness/cluster.py."""

from __future__ import annotations

import json
from pathlib import Path

from harness import cluster, scenarios


def make_card(
    seq: int,
    cluster_key: str,
    *,
    date: str = "2026-10-01",
    kind: str = "judge_disagreement",
    status: str = "active",
    company: str = "Arch",
) -> dict:
    return {
        "id": f"ingest-{date}-{seq:04d}",
        "layer": "ingest",
        "version": 1,
        "status": status,
        "origin": {"kind": kind, "date": date},
        "persona": "Layer 1 relevance pass",
        "goal": f"goal {seq}",
        "input": {
            "candidate": {
                "title": f"title {seq}",
                "url": f"https://example.com/{seq}",
                "source": "feed",
                "matched": [company],
            },
            "companies": {company: {"description": "desc"}},
        },
        "expected": {"verdict": "drop", "company": None, "reason": "r"},
        "cluster": cluster_key,
        "graders": ["verdict_match"],
        "created_by": "test",
    }


def write_cards(root: Path, cards: list[dict]) -> None:
    for card in cards:
        scenarios.save(card, root)


def test_picks_largest_weighted_cluster():
    cards = [make_card(i, "ingest/false_keep/weak_term/arch") for i in range(1, 4)]
    cards += [make_card(i, "ingest/false_drop/x/y") for i in range(4, 8)]
    result = cluster.select_cluster(cards, 3)
    assert result["eligible"] is True
    assert result["cluster"] == "ingest/false_drop/x/y"
    assert result["size"] == 4
    assert result["weighted_size"] == 4
    assert result["layer"] == "ingest"
    assert result["failure_kind"] == "false_drop"
    assert result["root_term"] == "x"
    assert result["host"] == "y"
    assert result["slug"] == "ingest-false_drop-x-y"
    assert result["scenario_ids"] == [f"ingest-2026-10-01-{i:04d}" for i in range(4, 8)]


def test_human_card_weight_and_eligibility():
    cards = [make_card(1, "ingest/false_keep/a", kind="human")]
    cards += [make_card(i, "ingest/false_keep/b") for i in range(2, 4)]
    result = cluster.select_cluster(cards, 3)
    assert result["cluster"] == "ingest/false_keep/a"
    assert result["weighted_size"] == 3
    assert result["size"] == 1


def test_single_human_card_is_eligible_above_min_size():
    result = cluster.select_cluster([make_card(1, "ingest/false_keep/a", kind="human")], 10)
    assert result["eligible"] is True
    assert result["cluster"] == "ingest/false_keep/a"


def test_tie_breaks_on_oldest_date_then_key():
    cards = [make_card(i, "ingest/false_keep/new", date="2026-10-02") for i in range(1, 4)]
    cards += [make_card(i, "ingest/false_keep/old", date="2026-09-01") for i in range(1, 4)]
    assert cluster.select_cluster(cards, 3)["cluster"] == "ingest/false_keep/old"
    same_date = [make_card(i, "ingest/false_keep/b") for i in range(1, 4)]
    same_date += [make_card(i, "ingest/false_keep/a") for i in range(4, 7)]
    assert cluster.select_cluster(same_date, 3)["cluster"] == "ingest/false_keep/a"


def test_nothing_eligible():
    cards = [make_card(i, "ingest/false_keep/a") for i in range(1, 3)]
    result = cluster.select_cluster(cards, 3)
    assert result["eligible"] is False
    assert result["cluster"] is None
    assert result["scenario_ids"] == []
    assert result["clusters_considered"] == 1


def test_companies_and_card_summaries():
    cards = [make_card(i, "ingest/false_keep/t", company=f"Co{i}") for i in range(1, 4)]
    result = cluster.select_cluster(cards, 3)
    assert result["companies"] == ["Co1", "Co2", "Co3"]
    summary = result["cards"][0]
    assert summary["goal"] == "goal 1"
    assert summary["expected"]["verdict"] == "drop"
    assert summary["input"]["title"] == "title 1"
    assert summary["input"]["url"] == "https://example.com/1"
    assert summary["input"]["companies"] == ["Co1"]


def test_non_candidate_input_is_summarized_raw():
    summary = cluster.summarize_input({"brief": "x" * 1000})
    assert len(summary["raw"]) == cluster.SUMMARY_MAX_CHARS


def test_main_writes_file_and_ignores_retired(tmp_path: Path, capsys):
    root = tmp_path / "scenarios"
    cards = [make_card(i, "ingest/false_keep/a") for i in range(1, 4)]
    cards += [make_card(i, "ingest/false_keep/b", status="retired") for i in range(4, 10)]
    write_cards(root, cards)
    out = tmp_path / "harness" / "cluster.json"
    assert cluster.main(["--scenarios", str(root), "--out", str(out)]) == 0
    written = json.loads(out.read_text())
    assert written["cluster"] == "ingest/false_keep/a"
    assert written["eligible"] is True
    assert "cluster: ingest/false_keep/a" in capsys.readouterr().out


def test_main_not_eligible_exits_zero(tmp_path: Path):
    root = tmp_path / "scenarios"
    write_cards(root, [make_card(1, "ingest/false_keep/a")])
    out = tmp_path / "cluster.json"
    assert cluster.main(["--scenarios", str(root), "--out", str(out), "--min-size", "5"]) == 0
    assert json.loads(out.read_text())["eligible"] is False


def test_main_filters_to_allowed_layers(tmp_path: Path):
    root = tmp_path / "scenarios"
    for seq in range(3):
        scenarios.save(make_card(seq + 1, "ingest/false_keep/arch"), root)
    synth = make_card(9, "synthesis/template_drift/hiring")
    synth["id"] = "synthesis-2026-10-01-0009"
    synth["layer"] = "synthesis"
    synth["graders"] = ["template_invariants"]
    for seq in range(4):
        card = dict(synth, id=f"synthesis-2026-10-01-{seq + 1:04d}")
        card["input"] = {"candidate": {"url": f"https://example.com/s{seq}"}}
        scenarios.save(card, root)
    out = tmp_path / "cluster.json"
    assert cluster.main(["--scenarios", str(root), "--out", str(out), "--layers", "ingest"]) == 0
    assert json.loads(out.read_text())["layer"] == "ingest"
    assert cluster.main(["--scenarios", str(root), "--out", str(out), "--layers", "ingest,synthesis"]) == 0
    assert json.loads(out.read_text())["layer"] == "synthesis"
