"""Unit tests for scripts/jev_judge.py (HTTP mocked)."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import jev_judge

ROW = {
    "company": "Acme",
    "company_description": "Singapore robotics startup.",
    "headline": "Acme raises Series A",
    "description": "blurb",
    "source": "Tech in Asia",
    "date": "2026-10-03",
}

REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "verdict": {"type": "choice", "choice": "keep", "confidence": 0.91, "probabilities": {"keep": 0.91, "drop": 0.09}},
        "material": {"type": "noul", "noul": 0.25},
    },
    "usage": {"input_tokens": 400, "output_tokens": 60},
}


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.typesafe.ai", code, "err", {}, io.BytesIO(b"{}"))


@pytest.fixture(autouse=True)
def environment(monkeypatch) -> list[float]:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_MAX_ATTEMPTS", "3")
    sleeps: list[float] = []
    monkeypatch.setattr(jev_judge.time, "sleep", sleeps.append)
    return sleeps


def test_build_state_contains_item_and_company_context() -> None:
    state = jev_judge.build_state(ROW)
    for expected in ("Acme", "Singapore robotics startup.", "Acme raises Series A", "blurb", "Tech in Asia", "2026-10-03"):
        assert expected in state


def test_build_payload_has_choice_and_noul_questions() -> None:
    payload = jev_judge.build_payload(ROW)
    assert payload["model"] == "jev-latest"
    verdict = payload["questions"]["verdict"]
    assert verdict["type"] == "choice"
    assert set(verdict["criteria"]) == {"keep", "drop"}
    assert payload["questions"]["material"]["type"] == "noul"


def test_judge_item_parses_reply(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse(REPLY)

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fake_urlopen)
    result = jev_judge.judge_item(ROW, timeout=5)
    assert result["verdict"] == "keep"
    assert result["confidence"] == 0.91
    assert result["probabilities"] == {"keep": 0.91, "drop": 0.09}
    assert result["usage"] == {"input_tokens": 400, "output_tokens": 60}
    assert "material=0.25" in result["reason"]
    assert captured == {"auth": "Bearer test-key", "timeout": 5}


def test_unparseable_reply_is_fail_open(monkeypatch) -> None:
    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", lambda *a, **k: FakeResponse({"answers": {}}))
    result = jev_judge.judge_item(ROW)
    assert result["verdict"] is None
    assert result["reason"] == "unparseable reply"


def test_retries_429_then_succeeds(monkeypatch, environment) -> None:
    replies = [http_error(429), http_error(529), FakeResponse(REPLY)]

    def fake_urlopen(request, timeout=None):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fake_urlopen)
    assert jev_judge.judge_item(ROW)["verdict"] == "keep"
    assert len(environment) == 2


def test_gives_up_after_retries_on_429(monkeypatch, environment) -> None:
    attempts = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        raise http_error(429)

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fake_urlopen)
    result = jev_judge.judge_item(ROW)
    assert result["verdict"] is None
    assert "HTTP 429" in result["reason"]
    assert len(attempts) == 3
    assert len(environment) == 2


def test_non_retryable_error_does_not_retry(monkeypatch) -> None:
    attempts = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        raise http_error(401)

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fake_urlopen)
    assert jev_judge.judge_item(ROW)["verdict"] is None
    assert len(attempts) == 1


def test_timeout_is_retried_then_fail_open(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fake_urlopen)
    result = jev_judge.judge_item(ROW)
    assert result["verdict"] is None
    assert "TimeoutError" in result["reason"]


def test_no_key_makes_no_request(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY")

    def fail(*args, **kwargs):
        raise AssertionError("must not call the API")

    monkeypatch.setattr(jev_judge.urllib.request, "urlopen", fail)
    assert jev_judge.have_key() is False
    result = jev_judge.judge_item(ROW)
    assert result["verdict"] is None
    assert "TYPESAFE_API_KEY" in result["reason"]
