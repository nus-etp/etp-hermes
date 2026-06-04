"""Unit tests for the fail-open Langfuse eval shim (tests/evals/_obs.py).

Runs on every PR (no DEEPSEEK_API_KEY, no real langfuse needed). Verifies the
two contracts that matter: (1) without creds the shim is a total no-op, and
(2) with a client present, spans/generations/scores route to it correctly.
"""

from __future__ import annotations

import pytest

from tests.evals import _obs


@pytest.fixture(autouse=True)
def _isolate_client_cache():
    # Each test controls the client; never leak a fake across tests.
    _obs.reset_cache()
    yield
    _obs.reset_cache()


def test_fail_open_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("HERMES_LANGFUSE_SECRET_KEY", raising=False)
    _obs.reset_cache()

    with _obs.eval_span("some-test") as handle:
        assert handle.enabled is False
        # Must not raise.
        handle.score("precision", 0.9, comment="ignored")
    # Must not raise even with no active span.
    _obs.record_generation(name="x", model="m", input=[], output="o")


def test_fail_open_when_build_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_obs, "_build_client", lambda: None)
    _obs.reset_cache()
    with _obs.eval_span("t") as handle:
        assert handle.enabled is False
        handle.score("recall", 1.0)


# --------------------------------------------------------------------------- #
# Fakes mirroring the slice of the langfuse v4 API the shim touches.
# --------------------------------------------------------------------------- #
class _FakeSpanCM:
    def __init__(self, client: "_FakeClient") -> None:
        self._c = client

    def __enter__(self):
        self._c.events.append(("span_enter",))
        return self

    def __exit__(self, *exc):
        self._c.events.append(("span_exit",))
        return False


class _FakeGen:
    def __init__(self, client: "_FakeClient", kwargs: dict) -> None:
        self._c = client
        self.kwargs = kwargs

    def end(self) -> None:
        self._c.events.append(("gen_end",))


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.generations: list[dict] = []
        self.scores: list[dict] = []

    def start_as_current_observation(self, **kwargs):
        self.events.append(("start_span", kwargs))
        return _FakeSpanCM(self)

    def get_current_trace_id(self) -> str:
        return "trace-abc"

    def start_observation(self, **kwargs):
        self.generations.append(kwargs)
        return _FakeGen(self, kwargs)

    def create_score(self, **kwargs) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.events.append(("flush",))


def test_enabled_routes_spans_generations_and_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(_obs, "_build_client", lambda: fake)
    _obs.reset_cache()

    with _obs.eval_span("relevance") as handle:
        assert handle.enabled is True
        _obs.record_generation(
            name="deepseek.chat",
            model="deepseek-chat",
            input=[{"role": "user", "content": "hi"}],
            output="yo",
            model_parameters={"temperature": 0.0, "max_tokens": 64},
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            latency_s=0.42,
        )
        handle.score("precision", 0.9, comment="ok")

    # Span opened, entered, exited, and flushed.
    kinds = [e[0] for e in fake.events]
    assert kinds == ["start_span", "span_enter", "gen_end", "span_exit", "flush"]

    # Generation captured model, output, token usage, and latency metadata.
    assert len(fake.generations) == 1
    gen = fake.generations[0]
    assert gen["as_type"] == "generation"
    assert gen["model"] == "deepseek-chat"
    assert gen["output"] == "yo"
    assert gen["usage_details"] == {"input": 5, "output": 2, "total": 7}
    assert gen["metadata"] == {"latency_s": 0.42}

    # Score attached to the span's trace id.
    assert len(fake.scores) == 1
    score = fake.scores[0]
    assert score["name"] == "precision"
    assert score["value"] == 0.9
    assert score["trace_id"] == "trace-abc"
    assert score["comment"] == "ok"


def test_client_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError("langfuse exploded")

    monkeypatch.setattr(_obs, "_build_client", lambda: _Boom())
    _obs.reset_cache()
    # A client that throws on span creation must degrade to a null handle.
    with _obs.eval_span("t") as handle:
        assert handle.enabled is False
        handle.score("x", 1.0)
