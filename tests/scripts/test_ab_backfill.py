"""Unit tests for scripts/ab_backfill.py — the arm-replay sample seeder."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("ab_backfill")


def _arm_split_llm():
    """Fake ab_llm: v1 keeps everything, v2 drops everything (all disagree)."""
    real_extract = _real_extract()

    def chat(messages, *, model=None, max_tokens=40):  # noqa: ARG001
        system = messages[0]["content"]
        keep = "false" if "ARM_V2" in system else "true"
        return f'{{"keep": {keep}}}'

    return types.SimpleNamespace(chat=chat, extract_json=real_extract, have_key=lambda: True)


def _real_extract():
    import importlib.util

    path = Path(__file__).resolve().parent.parent.parent / "scripts" / "ab_llm.py"
    spec = importlib.util.spec_from_file_location("ab_llm_real", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.extract_json


@pytest.fixture()
def backfill_repo(tmp_path: Path) -> Path:
    (tmp_path / "prompts" / "v2").mkdir(parents=True)
    (tmp_path / "prompts" / "ingest.md").write_text("ARM_V1 relevance policy")
    (tmp_path / "prompts" / "v2" / "ingest.md").write_text("ARM_V2 relevance policy")
    (tmp_path / "data").mkdir()
    (tmp_path / "signals" / "ab").mkdir(parents=True)
    candidates = {
        "candidates": [
            {"company": "Acme", "headline": "A raises", "description": "", "source": "TC",
             "pubDate": "2026-06-08", "link": "https://t.co/a", "source_kind": "firehose"},
            {"company": "Beta", "headline": "B partners", "description": "", "source": "e27",
             "pubDate": "June 2, 2026", "link": "https://e27.co/b", "source_kind": "rss"},
            # pre_extracted auto-keeps in both arms -> must be skipped, never a disagreement
            {"company": "Gamma", "headline": "G", "description": "", "source": "blog",
             "pubDate": "2026-06-09", "link": "https://g.co/x", "source_kind": "html_scrape",
             "pre_extracted": True},
        ],
        "companies": {"Acme": "SG robotics", "Beta": "SG bio"},
    }
    (tmp_path / "data" / "candidates.json").write_text(json.dumps(candidates))
    return tmp_path


def _run(mod, repo: Path, monkeypatch, extra=None) -> int:
    sys.modules["ab_llm"] = _arm_split_llm()
    sys.modules.pop("ab_compare", None)
    argv = ["ab_backfill", "--repo-root", str(repo)] + (extra or [])
    monkeypatch.setattr(mod.sys, "argv", argv)
    try:
        return mod.main()
    finally:
        sys.modules.pop("ab_llm", None)


def test_candidate_date_parses_or_falls_back(mod) -> None:
    assert mod.candidate_date({"pubDate": "2026-06-08T12:00Z"}, "2000-01-01") == "2026-06-08"
    assert mod.candidate_date({"pubDate": "June 2, 2026"}, "2000-01-01") == "2000-01-01"
    assert mod.candidate_date({}, "2000-01-01") == "2000-01-01"


def test_backfill_records_disagreements_and_skips_pre_extracted(mod, backfill_repo, monkeypatch) -> None:
    assert _run(mod, backfill_repo, monkeypatch, ["--date", "2000-01-01"]) == 0
    rows = [
        json.loads(l)
        for l in (backfill_repo / "signals" / "ab" / "disagreements.jsonl").read_text().splitlines()
    ]
    urls = {r["url"] for r in rows}
    # Acme + Beta disagree; Gamma (pre_extracted) is skipped.
    assert urls == {"https://t.co/a", "https://e27.co/b"}
    assert all(r["kept_by"] == "v1" and r["origin"] == "backfill" for r in rows)
    by_url = {r["url"]: r for r in rows}
    assert by_url["https://t.co/a"]["date"] == "2026-06-08"        # parsed pubDate
    assert by_url["https://e27.co/b"]["date"] == "2000-01-01"      # unparseable -> fallback
    assert by_url["https://t.co/a"]["company_description"] == "SG robotics"


def test_backfill_append_is_idempotent(mod, backfill_repo, monkeypatch) -> None:
    assert _run(mod, backfill_repo, monkeypatch, ["--date", "2000-01-01"]) == 0
    assert _run(mod, backfill_repo, monkeypatch, ["--date", "2000-01-01"]) == 0
    rows = (backfill_repo / "signals" / "ab" / "disagreements.jsonl").read_text().strip().splitlines()
    assert len(rows) == 2  # second run adds nothing


def test_backfill_failopen_without_key(mod, backfill_repo, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    sys.modules.pop("ab_llm", None)  # force real ab_llm (have_key False)
    monkeypatch.setattr(mod.sys, "argv", ["ab_backfill", "--repo-root", str(backfill_repo)])
    assert mod.main() == 0
    assert not (backfill_repo / "signals" / "ab" / "disagreements.jsonl").exists()
