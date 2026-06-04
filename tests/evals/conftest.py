"""Gate the LLM-behaviour eval suite behind DEEPSEEK_API_KEY."""

from __future__ import annotations

import os

import pytest

from tests.evals import _obs

if not os.environ.get("DEEPSEEK_API_KEY"):
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(scope="session")
def deepseek_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY not set; LLM evals skipped")
    assert key
    return key


@pytest.fixture()
def eval_obs(request: pytest.FixtureRequest):
    """Open a Langfuse root span for this eval and expose a scoring handle.

    Fail-open: yields a no-op handle when Langfuse is unavailable. Generations
    from chat()/judge() nest under the span; tests call ``.score(name, value)``
    to attach metrics (precision, recall, pass/fail) for trend history.
    """
    with _obs.eval_span(request.node.name) as handle:
        request.node._eval_handle = handle  # read by the makereport hook below
        yield handle


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Emit a uniform pass/fail (+duration) score per eval, even on failure."""
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":
        return
    handle = getattr(item, "_eval_handle", None)
    if handle is None or not getattr(handle, "enabled", False):
        return
    handle.score("passed", 1.0 if rep.passed else 0.0)
    if getattr(rep, "duration", None) is not None:
        handle.score("duration_s", float(rep.duration))
