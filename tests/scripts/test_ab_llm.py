"""Unit tests for scripts/ab_llm.py — the shared fail-open chat client."""

from __future__ import annotations

import json

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


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._payload = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _capture_requests(mod, monkeypatch, body: dict) -> list:
    captured: list = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResponse(body)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return captured


REPLY = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 7}}

RUNTIME_ENV = (
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "AB_JUDGE_MODEL",
    "NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL",
    "ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL",
)


@pytest.fixture()
def clean_env(monkeypatch):
    for name in RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_have_key_per_runtime(mod, clean_env) -> None:
    clean_env.setenv("NVIDIA_API_KEY", "n")
    assert mod.have_key("nvidia") is True
    assert mod.have_key("zai") is False
    assert mod.have_key() is False
    assert mod.have_key("unknown") is False


def test_default_runtime_is_deepseek_and_backward_compatible(mod, clean_env) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "d")
    captured = _capture_requests(mod, clean_env, REPLY)
    assert mod.chat([{"role": "user", "content": "hi"}]) == "ok"
    request = captured[0]
    assert request.full_url == mod.DEFAULT_URL
    assert json.loads(request.data)["model"] == mod.DEFAULT_MODEL
    assert request.get_header("Authorization") == "Bearer d"


@pytest.mark.parametrize(
    ("runtime", "key_env", "url", "model"),
    [
        ("nvidia", "NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions",
         "deepseek-ai/deepseek-v4.1-flash"),
        ("zai", "ZAI_API_KEY", "https://api.z.ai/api/paas/v4/chat/completions", "glm-4.7-flash"),
    ],
)
def test_runtime_defaults(mod, clean_env, runtime, key_env, url, model) -> None:
    clean_env.setenv(key_env, "k")
    captured = _capture_requests(mod, clean_env, REPLY)
    assert mod.chat([{"role": "user", "content": "hi"}], runtime=runtime) == "ok"
    assert captured[0].full_url == url
    assert json.loads(captured[0].data)["model"] == model
    assert captured[0].get_header("Authorization") == "Bearer k"


def test_runtime_env_overrides(mod, clean_env) -> None:
    clean_env.setenv("ZAI_API_KEY", "z")
    clean_env.setenv("ZAI_BASE_URL", "https://example.test/v1/chat")
    clean_env.setenv("ZAI_MODEL", "glm-custom")
    captured = _capture_requests(mod, clean_env, REPLY)
    mod.chat([{"role": "user", "content": "hi"}], runtime="zai")
    assert captured[0].full_url == "https://example.test/v1/chat"
    assert json.loads(captured[0].data)["model"] == "glm-custom"
    mod.chat([{"role": "user", "content": "hi"}], runtime="zai", model="explicit")
    assert json.loads(captured[1].data)["model"] == "explicit"


def test_chat_failopen_without_runtime_key(mod, clean_env) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "d")
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="nvidia") is None
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="unknown") is None


def test_usage_accumulates_per_runtime(mod, clean_env) -> None:
    clean_env.setenv("NVIDIA_API_KEY", "n")
    _capture_requests(mod, clean_env, REPLY)
    before_nvidia = mod.usage_total("nvidia")
    before_zai = mod.usage_total("zai")
    mod.chat([{"role": "user", "content": "hi"}], runtime="nvidia")
    mod.chat([{"role": "user", "content": "hi"}], runtime="nvidia")
    assert mod.usage_total("nvidia") == before_nvidia + 14
    assert mod.usage_total("zai") == before_zai


def test_http_error_emits_status_and_body_diagnostic(mod, clean_env, capsys) -> None:
    import io

    clean_env.setenv("NVIDIA_API_KEY", "n")

    def boom(req, timeout=None):  # noqa: ARG001
        raise mod.urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"detail": "model not found"}')
        )

    clean_env.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="nvidia") is None
    err = capsys.readouterr().err
    assert "nvidia" in err and "HTTP 404" in err and "model not found" in err


def test_other_exception_emits_class_and_message(mod, clean_env, capsys) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "d")

    def boom(req, timeout=None):  # noqa: ARG001
        raise OSError("network down")

    clean_env.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.chat([{"role": "user", "content": "hi"}]) is None
    assert "OSError: network down" in capsys.readouterr().err


def test_empty_content_falls_back_to_reasoning_content(mod, clean_env, capsys) -> None:
    clean_env.setenv("ZAI_API_KEY", "z")
    message = {"content": "", "reasoning_content": '{"keep": true}'}
    _capture_requests(mod, clean_env, {"choices": [{"finish_reason": "length", "message": message}]})
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") == '{"keep": true}'
    err = capsys.readouterr().err
    assert "empty content" in err and "finish_reason=length" in err and "reasoning_content=present" in err


def test_empty_content_without_reasoning_returns_none(mod, clean_env, capsys) -> None:
    clean_env.setenv("ZAI_API_KEY", "z")
    body = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
    _capture_requests(mod, clean_env, body)
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") is None
    assert "reasoning_content=absent" in capsys.readouterr().err


def test_runtime_extra_payload_merged_into_request_body(mod, clean_env) -> None:
    clean_env.setenv("ZAI_API_KEY", "z")
    clean_env.setenv("DEEPSEEK_API_KEY", "d")
    captured = _capture_requests(mod, clean_env, REPLY)
    mod.chat([{"role": "user", "content": "hi"}], runtime="zai")
    mod.chat([{"role": "user", "content": "hi"}])
    zai_body = json.loads(captured[0].data)
    deepseek_body = json.loads(captured[1].data)
    assert zai_body["thinking"] == {"type": "disabled"}
    assert zai_body["model"] == "glm-4.7-flash"
    assert "thinking" not in deepseek_body


def _http_error(mod, code: int, headers: dict | None = None):
    import io

    return mod.urllib.error.HTTPError(
        "https://example.test", code, "err", headers or {}, io.BytesIO(b'{"code": "1302"}')
    )


def _scripted_urlopen(mod, monkeypatch, outcomes: list) -> list:
    calls: list = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        calls.append(req)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return calls


@pytest.fixture()
def sleeps(mod, clean_env):
    recorded: list = []
    clean_env.setattr(mod.time, "sleep", recorded.append)
    clean_env.setenv("ZAI_API_KEY", "z")
    clean_env.setenv("AB_LLM_BACKOFF_SECS", "5")
    return recorded


def test_retries_429_then_succeeds(mod, clean_env, sleeps) -> None:
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 429), _http_error(mod, 503), REPLY])
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") == "ok"
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert 5 <= sleeps[0] <= 5.6
    assert 15 <= sleeps[1] <= 16.6


def test_retries_timeout_and_url_error(mod, clean_env, sleeps) -> None:
    outcomes = [TimeoutError("read timed out"), mod.urllib.error.URLError("dns"), REPLY]
    calls = _scripted_urlopen(mod, clean_env, outcomes)
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") == "ok"
    assert len(calls) == 3


def test_does_not_retry_410(mod, clean_env, sleeps) -> None:
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 410), REPLY])
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") is None
    assert len(calls) == 1
    assert sleeps == []


def test_does_not_retry_other_4xx(mod, clean_env, sleeps) -> None:
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 401), REPLY])
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") is None
    assert len(calls) == 1


def test_gives_up_after_max_attempts(mod, clean_env, sleeps, capsys) -> None:
    clean_env.setenv("AB_LLM_MAX_ATTEMPTS", "3")
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 429)] * 5)
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") is None
    assert len(calls) == 3
    assert len(sleeps) == 2
    err = capsys.readouterr().err
    assert "HTTP 429" in err and "giving up after 3 attempts" in err


def test_single_attempt_via_env(mod, clean_env, sleeps) -> None:
    clean_env.setenv("AB_LLM_MAX_ATTEMPTS", "1")
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 429), REPLY])
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") is None
    assert len(calls) == 1
    assert sleeps == []


def test_retry_after_header_is_honoured(mod, clean_env, sleeps) -> None:
    calls = _scripted_urlopen(mod, clean_env, [_http_error(mod, 429, {"Retry-After": "7"}), REPLY])
    assert mod.chat([{"role": "user", "content": "hi"}], runtime="zai") == "ok"
    assert len(calls) == 2
    assert sleeps == [7.0]


def test_min_interval_spaces_consecutive_calls(mod, clean_env, sleeps) -> None:
    clean_env.setenv("AB_LLM_MIN_INTERVAL_SECS", "1")
    mod._last_call_started.clear()
    _scripted_urlopen(mod, clean_env, [REPLY, REPLY])
    mod.chat([{"role": "user", "content": "hi"}], runtime="zai")
    assert sleeps == []
    mod.chat([{"role": "user", "content": "hi"}], runtime="zai")
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 1
