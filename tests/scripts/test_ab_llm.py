"""Unit tests for scripts/ab_llm.py — the shared fail-open chat client."""

from __future__ import annotations

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("ab_llm")


def test_chat_failopen_without_key(mod, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert mod.chat([{"role": "user", "content": "hi"}]) is None
    assert mod.have_key() is False


def test_chat_failopen_on_http_error(mod, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")

    def boom(req, timeout=None):  # noqa: ARG001
        raise OSError("network down")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.chat([{"role": "user", "content": "hi"}]) is None


def test_extract_json_variants(mod) -> None:
    assert mod.extract_json('{"verdict": "keep"}') == {"verdict": "keep"}
    assert mod.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert mod.extract_json("prose then {\"a\": 2} trailing") == {"a": 2}
    assert mod.extract_json("no json here") is None
    assert mod.extract_json("") is None
    # a bare JSON array is not an object -> None
    assert mod.extract_json("[1, 2, 3]") is None
