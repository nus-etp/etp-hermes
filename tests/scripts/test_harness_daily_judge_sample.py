"""Unit tests for scripts/harness/daily_judge_sample.py (LLM mocked)."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from harness import daily_judge_sample

UPDATES = """# Daily Updates — 2026-10-03

## Acme

- **Acme raises Series A** — Tech in Asia · 2026-10-03
  https://www.techinasia.com/acme-series-a?utm_source=x
"""


@pytest.fixture()
def modules():
    return (
        daily_judge_sample.load_script("ab_judge"),
        daily_judge_sample.load_script("ab_compare"),
        daily_judge_sample.load_script("ab_llm"),
    )


def fake_llm(real_llm, verdict: str | None, *, has_key: bool = True):
    calls: list[list[dict]] = []

    def chat(messages, *, model=None, max_tokens=120):
        calls.append(messages)
        return None if verdict is None else json.dumps({"verdict": verdict, "reason": "because"})

    llm = types.SimpleNamespace(have_key=lambda: has_key, chat=chat, extract_json=real_llm.extract_json)
    return llm, calls


def seed_repo(root: Path, n_dropped: int = 3) -> None:
    (root / "data").mkdir()
    (root / "signals" / "updates").mkdir(parents=True)
    (root / "signals" / "updates" / "2026-10-03.md").write_text(UPDATES, encoding="utf-8")
    candidates = [
        {
            "company": "Acme",
            "headline": "Acme raises Series A",
            "description": "Acme blurb",
            "source": "Tech in Asia",
            "link": "https://www.techinasia.com/acme-series-a",
        }
    ]
    candidates += [
        {"company": "Acme", "headline": f"Noise {i}", "description": "", "source": "Feed", "link": f"https://n.com/{i}"}
        for i in range(n_dropped)
    ]
    payload = {"candidates": candidates, "companies": {"Acme": "Singapore robotics startup."}}
    (root / "data" / "candidates.json").write_text(json.dumps(payload), encoding="utf-8")


def read_rows(root: Path) -> list[dict]:
    path = root / "data" / "harness" / "judged" / "2026-10-03.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run(root: Path, llm, modules, kept: int = 40, dropped: int = 40) -> int:
    judge, compare, _ = modules
    return daily_judge_sample.run(root, "2026-10-03", kept, dropped, llm, judge, compare, None)


def test_writes_kept_and_dropped_rows(tmp_path: Path, modules) -> None:
    seed_repo(tmp_path)
    llm, calls = fake_llm(modules[2], "drop")
    assert run(tmp_path, llm, modules) == 0
    rows = read_rows(tmp_path)
    assert [r["verdict_pipeline"] for r in rows] == ["keep", "drop", "drop", "drop"]
    kept = rows[0]
    assert kept["url"] == "https://www.techinasia.com/acme-series-a"
    assert kept["description"] == "Acme blurb"
    assert kept["company_description"] == "Singapore robotics startup."
    assert kept["verdict_judge"] == "drop"
    assert kept["origin"] == "daily"
    assert kept["date"] == "2026-10-03"
    assert len(calls) == 4
    assert "verdict_pipeline" not in calls[0][1]["content"]


def test_sample_caps_and_is_deterministic(tmp_path: Path, modules) -> None:
    seed_repo(tmp_path, n_dropped=10)
    llm, _ = fake_llm(modules[2], "keep")
    run(tmp_path, llm, modules, kept=1, dropped=3)
    first = [r["url"] for r in read_rows(tmp_path)]
    run(tmp_path, llm, modules, kept=1, dropped=3)
    assert [r["url"] for r in read_rows(tmp_path)] == first
    assert len(first) == 4


def test_judge_failure_records_null(tmp_path: Path, modules) -> None:
    seed_repo(tmp_path, n_dropped=0)
    llm, _ = fake_llm(modules[2], None)
    run(tmp_path, llm, modules)
    assert [r["verdict_judge"] for r in read_rows(tmp_path)] == [None]


def test_fails_open_without_key(tmp_path: Path, modules) -> None:
    seed_repo(tmp_path)
    llm, calls = fake_llm(modules[2], "keep", has_key=False)
    assert run(tmp_path, llm, modules) == 0
    assert calls == []
    assert not (tmp_path / "data" / "harness").exists()


def test_main_without_key_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    seed_repo(tmp_path)
    assert daily_judge_sample.main(["--repo-root", str(tmp_path), "--date", "2026-10-03"]) == 0
    assert not (tmp_path / "data" / "harness").exists()


def test_nothing_to_judge_writes_nothing(tmp_path: Path, modules) -> None:
    llm, calls = fake_llm(modules[2], "keep")
    assert run(tmp_path, llm, modules) == 0
    assert calls == []
    assert not (tmp_path / "data" / "harness").exists()
