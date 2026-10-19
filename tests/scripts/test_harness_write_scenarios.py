"""Unit tests for scripts/harness/write_scenarios.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import scenarios, write_scenarios


@pytest.fixture(autouse=True)
def no_run_id(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def ab_row(url: str, label: str | None, company: str = "Acme", date: str = "2026-06-01") -> dict:
    return {
        "company": company,
        "company_description": "Singapore robotics startup.",
        "date": date,
        "description": "blurb",
        "headline": f"Headline {url}",
        "kept_by": "v1",
        "label": label,
        "label_model": "deepseek-chat",
        "label_reason": "reason",
        "origin": "daily",
        "source": "Tech in Asia",
        "url": url,
    }


def test_from_ab_writes_labeled_rows_only(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "d.jsonl",
        [
            ab_row("https://www.acme.sg/a", "drop"),
            ab_row("https://acme.sg/b", "keep"),
            ab_row("https://acme.sg/c", None),
        ],
    )
    assert write_scenarios.from_ab(source, root, "2026-10-03") == 2
    cards = scenarios.load_all(root)
    assert [c["id"] for c in cards] == ["ingest-2026-06-01-0001", "ingest-2026-06-01-0002"]
    dropped, kept = cards
    assert dropped["origin"]["kind"] == "ab_seed"
    assert dropped["expected"] == {"verdict": "drop", "company": None, "reason": "reason"}
    assert dropped["cluster"] == "ingest/false_keep/acme/acme.sg"
    assert kept["expected"]["company"] == "Acme"
    assert kept["cluster"] == "ingest/false_drop/acme/acme.sg"
    assert kept["input"]["companies"] == {"Acme": {"description": "Singapore robotics startup."}}
    assert all(scenarios.validate(c) == [] for c in cards)


def test_from_ab_is_idempotent_by_url(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "d.jsonl",
        [ab_row("https://acme.sg/a", "drop"), ab_row("https://acme.sg/a", "drop", date="2026-06-02")],
    )
    assert write_scenarios.from_ab(source, root, "2026-10-03") == 1
    assert write_scenarios.from_ab(source, root, "2026-10-03") == 0
    assert len(scenarios.load_all(root)) == 1


def judged_row(url: str, pipeline: str, judge: str | None) -> dict:
    return {
        "date": "2026-10-03",
        "url": url,
        "company": "Acme",
        "headline": "h",
        "description": "",
        "source": "s",
        "verdict_pipeline": pipeline,
        "verdict_judge": judge,
        "judge_reason": "judge says so",
        "origin": "daily",
    }


def test_from_judged_writes_only_disagreements(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "2026-10-03.jsonl",
        [
            judged_row("https://a.com/1", "keep", "drop"),
            judged_row("https://a.com/2", "drop", "keep"),
            judged_row("https://a.com/3", "keep", "keep"),
            judged_row("https://a.com/4", "drop", None),
        ],
    )
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 2
    cards = scenarios.load_all(root)
    assert [c["expected"]["verdict"] for c in cards] == ["drop", "keep"]
    assert cards[0]["origin"]["kind"] == "judge_disagreement"
    assert cards[0]["origin"]["run_id"] == "42"
    assert cards[0]["created_by"] == "write_scenarios@run 42"
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 0


def jev_row(url: str, confidence: float | None, layer: str | None = None) -> dict:
    row = judged_row(url, "keep", "drop")
    row.update({"judge": "jev", "judge_confidence": confidence})
    if layer:
        row["layer"] = layer
    return row


def test_from_judged_applies_confidence_floor_to_jev_only(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    deepseek = judged_row("https://a.com/ds", "keep", "drop")
    deepseek["judge"] = "deepseek"
    source = write_jsonl(
        tmp_path / "2026-10-03.jsonl",
        [
            jev_row("https://a.com/low", 0.5),
            jev_row("https://a.com/none", None),
            jev_row("https://a.com/high", 0.9),
            deepseek,
        ],
    )
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 2
    urls = {scenarios.card_url(c) for c in scenarios.load_all(root)}
    assert urls == {"https://a.com/high", "https://a.com/ds"}
    assert write_scenarios.from_judged(source, tmp_path / "other", "2026-10-03", min_confidence=0.4) == 3


def test_from_judged_records_judge_in_origin(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(tmp_path / "d.jsonl", [jev_row("https://a.com/1", 0.9)])
    write_scenarios.from_judged(source, root, "2026-10-03")
    origin = scenarios.load_all(root)[0]["origin"]
    assert origin["judge"] == "jev"
    assert origin["judge_confidence"] == 0.9


def test_from_judged_routes_agent_rows_to_agent_supplement(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "d.jsonl",
        [jev_row("https://a.com/ag", 0.9, "agent_supplement"), jev_row("https://a.com/in", 0.9, "ingest")],
    )
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 2
    agent_cards = scenarios.load_all(root, layer="agent_supplement")
    assert len(agent_cards) == 1
    card = agent_cards[0]
    assert card["id"].startswith("agent_supplement-2026-10-03-")
    assert card["cluster"].startswith("agent_supplement/false_keep/")
    assert card["expected"]["verdict"] == "drop"
    assert len(scenarios.load_all(root, layer="ingest")) == 1
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 0


def published_row(url: str, published: str, pipeline: str = "drop", judge: str = "keep") -> dict:
    row = judged_row(url, pipeline, judge)
    row["published"] = published
    return row


def test_from_judged_skips_undated_or_stale_false_drops(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "d.jsonl",
        [
            published_row("https://a.com/undated", ""),
            published_row("https://a.com/garbage", "sometime soon"),
            published_row("https://a.com/old", "9 June 2025"),
            published_row("https://a.com/fresh-human", "30 September 2026"),
            published_row("https://a.com/fresh-rfc", "Fri, 02 Oct 2026 08:00:00 GMT"),
            published_row("https://a.com/fresh-iso", "2026-10-01T10:00:00Z"),
            published_row("https://a.com/false-keep", "", pipeline="keep", judge="drop"),
        ],
    )
    assert write_scenarios.from_judged(source, root, "2026-10-03") == 4
    urls = {scenarios.card_url(c) for c in scenarios.load_all(root)}
    assert urls == {
        "https://a.com/fresh-human",
        "https://a.com/fresh-rfc",
        "https://a.com/fresh-iso",
        "https://a.com/false-keep",
    }


def test_from_judged_max_age_is_configurable(tmp_path: Path) -> None:
    source = write_jsonl(tmp_path / "d.jsonl", [published_row("https://a.com/old", "9 June 2025")])
    assert write_scenarios.from_judged(source, tmp_path / "s", "2026-10-03", max_age_days=500) == 1


def test_parse_published_formats() -> None:
    assert str(write_scenarios.parse_published("07 October 2026")) == "2026-10-07"
    assert str(write_scenarios.parse_published("Oct 7, 2026")) == "2026-10-07"
    assert str(write_scenarios.parse_published("2026-10-07")) == "2026-10-07"
    assert write_scenarios.parse_published(None) is None


def issue(number: int, label: str, body: str, title: str = "Wrong item") -> dict:
    return {"number": number, "title": title, "body": body, "labels": [{"name": label}], "url": f"https://gh/{number}"}


def test_from_issues_maps_labels_to_cards(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    issues = [
        issue(1, "harness:wrong-keep", "Company: Acme\nHeadline: Stablecoin story\nhttps://news.com/x."),
        issue(2, "harness:missed", "**Company:** Beta Labs\nSee https://beta.io/raise"),
        issue(3, "harness:brief-error", "company: Gamma\nThe brief misstates funding https://gamma.ai/p"),
        issue(4, "harness:missed", "no structured fields here"),
        issue(5, "bug", "Company: Acme\nhttps://news.com/y"),
    ]
    source = tmp_path / "issues.json"
    source.write_text(json.dumps(issues), encoding="utf-8")
    assert write_scenarios.from_issues(source, root, "2026-10-03") == 3
    ingest = scenarios.load_all(root, layer="ingest")
    synthesis = scenarios.load_all(root, layer="synthesis")
    assert [c["expected"]["verdict"] for c in ingest] == ["drop", "keep"]
    assert ingest[0]["input"]["candidate"]["url"] == "https://news.com/x"
    assert ingest[0]["input"]["candidate"]["title"] == "Stablecoin story"
    assert ingest[1]["input"]["candidate"]["title"] == "Wrong item"
    assert ingest[1]["expected"]["company"] == "Beta Labs"
    assert all(c["origin"]["kind"] == "human" for c in ingest + synthesis)
    assert synthesis[0]["cluster"] == "synthesis/brief_error/gamma/gamma.ai"
    assert write_scenarios.from_issues(source, root, "2026-10-03") == 0


def test_main_fails_open_on_missing_and_bad_sources(tmp_path: Path, capsys) -> None:
    root = tmp_path / "scenarios"
    bad = tmp_path / "issues.json"
    bad.write_text("{not json", encoding="utf-8")
    exit_code = write_scenarios.main(
        ["--root", str(root), "--from-ab", str(tmp_path / "missing.jsonl"), "--issues", str(bad)]
    )
    assert exit_code == 0
    assert "wrote 0 cards" in capsys.readouterr().out


def label_row(url: str, label: str, company: str = "Acme", note: str = "operator note", date: str = "2026-10-05") -> dict:
    return {"url": url, "company": company, "label": label, "note": note, "date": date}


def test_from_labels_maps_labels_to_human_cards(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "human-labels.jsonl",
        [
            label_row("https://news.com/x", "wrong-keep"),
            label_row("https://beta.io/raise", "missed", company="Beta Labs"),
            label_row("https://gamma.ai/p", "brief-error", company="Gamma"),
            label_row("https://delta.io/p", "bogus"),
            label_row("", "missed"),
        ],
    )
    assert write_scenarios.from_labels(source, root, "2026-10-06") == 3
    ingest = scenarios.load_all(root, layer="ingest")
    synthesis = scenarios.load_all(root, layer="synthesis")
    assert [c["expected"]["verdict"] for c in ingest] == ["drop", "keep"]
    assert ingest[0]["expected"]["reason"] == "operator note"
    assert ingest[1]["expected"]["company"] == "Beta Labs"
    assert all(c["origin"]["kind"] == "human" and c["origin"]["date"] == "2026-10-05" for c in ingest + synthesis)
    assert synthesis[0]["cluster"] == "synthesis/brief_error/gamma/gamma.ai"
    assert all(scenarios.validate(c) == [] for c in ingest + synthesis)


def test_from_labels_is_idempotent_by_url(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(
        tmp_path / "human-labels.jsonl",
        [label_row("https://news.com/x", "wrong-keep"), label_row("https://news.com/x", "wrong-keep")],
    )
    assert write_scenarios.from_labels(source, root, "2026-10-06") == 1
    assert write_scenarios.from_labels(source, root, "2026-10-06") == 0


def test_main_accepts_labels_flag(tmp_path: Path, capsys) -> None:
    root = tmp_path / "scenarios"
    source = write_jsonl(tmp_path / "human-labels.jsonl", [label_row("https://news.com/x", "missed")])
    assert write_scenarios.main(["--root", str(root), "--labels", str(source)]) == 0
    assert "human=1" in capsys.readouterr().out
