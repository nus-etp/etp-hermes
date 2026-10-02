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
