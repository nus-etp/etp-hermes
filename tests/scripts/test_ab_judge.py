"""Unit tests for scripts/ab_judge.py — the blind relevance labeler."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("ab_judge")


def _fake_llm(reply: str):
    """An ab_llm stand-in: chat() returns a fixed reply; extract_json is real."""
    real = scripts_real_extract()

    def chat(messages, *, model=None, max_tokens=120):  # noqa: ARG001
        return reply

    return types.SimpleNamespace(chat=chat, extract_json=real, have_key=lambda: True)


def scripts_real_extract():
    import importlib.util

    path = Path(__file__).resolve().parent.parent.parent / "scripts" / "ab_llm.py"
    spec = importlib.util.spec_from_file_location("ab_llm_real", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.extract_json


def test_build_user_message_omits_arm(mod) -> None:
    row = {"company": "Acme", "headline": "H", "kept_by": "v1", "source": "TC"}
    msg = mod.build_user_message(row)
    assert "Acme" in msg and "kept_by" not in msg  # blind: never leak which arm kept it


def test_judge_row_parses_keep(mod) -> None:
    llm = _fake_llm('{"verdict": "keep", "reason": "real funding"}')
    verdict, reason = mod.judge_row(llm, {"company": "Acme"}, None)
    assert verdict == "keep" and reason == "real funding"


def test_judge_row_handles_fenced_json(mod) -> None:
    llm = _fake_llm('```json\n{"verdict":"drop","reason":"same-name ticker"}\n```')
    verdict, _ = mod.judge_row(llm, {"company": "Acme"}, None)
    assert verdict == "drop"


def test_judge_row_failopen_on_garbage(mod) -> None:
    llm = _fake_llm("I think maybe keep it?")
    assert mod.judge_row(llm, {"company": "Acme"}, None) == (None, None)


def test_judge_row_failopen_on_bad_verdict(mod) -> None:
    llm = _fake_llm('{"verdict": "maybe"}')
    assert mod.judge_row(llm, {"company": "Acme"}, None) == (None, None)


def _seed(path: Path, n_pending: int) -> None:
    rows = [
        {"date": "2026-06-10", "url": f"u/{i}", "company": "Acme", "headline": "H",
         "source": "TC", "description": "", "company_description": "", "kept_by": "v1",
         "origin": "daily", "label": None, "label_model": None, "label_reason": None}
        for i in range(n_pending)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_main_failopen_without_key(mod, tmp_path: Path, monkeypatch) -> None:
    ab = tmp_path / "signals" / "ab"
    ab.mkdir(parents=True)
    _seed(ab / "disagreements.jsonl", 2)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    sys.modules.pop("ab_llm", None)
    monkeypatch.setattr(mod.sys, "argv", ["ab_judge", "--repo-root", str(tmp_path)])
    assert mod.main() == 0
    # labels untouched (still null)
    rows = [json.loads(l) for l in (ab / "disagreements.jsonl").read_text().splitlines()]
    assert all(r["label"] is None for r in rows)


def test_main_labels_pending(mod, tmp_path: Path, monkeypatch) -> None:
    ab = tmp_path / "signals" / "ab"
    ab.mkdir(parents=True)
    _seed(ab / "disagreements.jsonl", 3)
    # Inject a fake ab_llm so the script's loader picks it up.
    sys.modules["ab_llm"] = _fake_llm('{"verdict": "keep", "reason": "ok"}')
    monkeypatch.setattr(mod.sys, "argv", ["ab_judge", "--repo-root", str(tmp_path)])
    try:
        assert mod.main() == 0
    finally:
        sys.modules.pop("ab_llm", None)
    rows = [json.loads(l) for l in (ab / "disagreements.jsonl").read_text().splitlines()]
    assert all(r["label"] == "keep" for r in rows)
    assert all(r["label_reason"] == "ok" for r in rows)
