"""Fail-open Langfuse instrumentation for the LLM eval suite (Tiers 1+2).

Mirrors the production ``observability/langfuse`` plugin's contract: the eval
suite emits **traces** (every ``chat()``/``judge()`` call, with prompt,
completion, token usage and latency) and **scores** (precision, recall,
pass/fail) to Langfuse — but *only* when the ``langfuse`` SDK is importable
AND the ``HERMES_LANGFUSE_{PUBLIC_KEY,SECRET_KEY,BASE_URL}`` env vars are
present. A missing SDK, missing creds, failed auth, or any SDK error degrades
to a no-op, so the eval suite never goes red for observability-infra reasons.

Reuses the same ``HERMES_LANGFUSE_*`` env vars the production plugin reads,
seeded in CI from the existing GitHub secrets. Built against langfuse v4
(OTEL-based: ``start_as_current_observation`` / ``start_observation`` /
``create_score``).
"""

from __future__ import annotations

import os
import subprocess
import time
import warnings
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

_UNSET: Any = object()
_client_cache: Any = _UNSET


@lru_cache(maxsize=1)
def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@lru_cache(maxsize=1)
def _session_id() -> str:
    """Stable id for the whole eval run, so its traces group as one session.

    Without a session id every eval trace is session-less and never shows in
    Langfuse's Sessions tab (only the production agent, which does set one,
    appears there). Prefer the CI run id — one session per workflow run — and
    fall back to a process-start epoch locally; both are prefixed with the
    short git SHA for a readable, collision-free handle.
    """
    run = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    return f"eval-{_git_sha()}-{run}"


def _client() -> Any:
    """Return a cached Langfuse client, or None when unavailable. Fail-open."""
    global _client_cache
    if _client_cache is not _UNSET:
        return _client_cache
    _client_cache = _build_client()
    return _client_cache


def _build_client() -> Any:
    pub = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("HERMES_LANGFUSE_SECRET_KEY")
    host = os.environ.get("HERMES_LANGFUSE_BASE_URL")
    if not (pub and sec):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from langfuse import Langfuse

            client = Langfuse(
                public_key=pub,
                secret_key=sec,
                host=host or None,
                environment=os.environ.get("HERMES_LANGFUSE_ENV", "eval"),
                release=_git_sha(),
                tracing_enabled=True,
            )
            if not client.auth_check():
                return None
            return client
    except Exception:
        return None


def reset_cache() -> None:
    """Test hook: clear the memoised client so env/patch changes take effect."""
    global _client_cache
    _client_cache = _UNSET


# --------------------------------------------------------------------------- #
# Handles. The real handle wraps a client + trace id; the null handle is a
# no-op stand-in so call sites never have to branch on availability.
# --------------------------------------------------------------------------- #
class _NullHandle:
    enabled = False

    def score(self, name: str, value: float, *, comment: str | None = None) -> None:
        pass


class _Handle:
    enabled = True

    def __init__(self, client: Any, trace_id: str | None) -> None:
        self._c = client
        self._trace_id = trace_id

    def score(self, name: str, value: float, *, comment: str | None = None) -> None:
        if self._trace_id is None:
            return
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._c.create_score(
                    name=name,
                    value=float(value),
                    trace_id=self._trace_id,
                    comment=comment,
                )
        except Exception:
            pass


@contextmanager
def eval_span(name: str) -> Iterator[Any]:
    """Open a root span for one eval test. Yields a handle (real or null).

    Generations recorded by ``record_generation`` inside the ``with`` block
    nest under this span automatically (OTEL current-context). Flushes on exit.

    Langfuse v4's OTEL SDK does **not** derive the trace name from the root
    span's name, so every eval trace otherwise lands unnamed (blank rows) in the
    Traces list. ``propagate_attributes(trace_name=...)`` is entered *before* the
    span so it stamps ``langfuse.trace.name`` (and tags) onto the span and every
    child generation — fail-open like everything else here.
    """
    c = _client()
    if c is None:
        yield _NullHandle()
        return

    # Enter the contexts manually (not via `with`) so a failure mid-setup still
    # leaves us with a clean null handle to yield — the eval must never break.
    prop_cm = None
    span_cm = None
    trace_id = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # propagate_attributes is optional: the SDK may be absent in
            # non-LLM-job environments (e.g. the fast/PR job, unit tests).
            # Isolate it so an ImportError here doesn't abort span creation.
            try:
                from langfuse import propagate_attributes

                prop_cm = propagate_attributes(
                    trace_name=name, tags=["llm-eval"], session_id=_session_id()
                )
                prop_cm.__enter__()
            except Exception:
                prop_cm = None
            span_cm = c.start_as_current_observation(
                name=name,
                as_type="span",
                metadata={"suite": "llm-eval", "git_sha": _git_sha()},
            )
            span_cm.__enter__()
            trace_id = c.get_current_trace_id()
    except Exception:
        # Unwind the propagate context if the span never opened.
        if prop_cm is not None and span_cm is None:
            try:
                prop_cm.__exit__(None, None, None)
            except Exception:
                pass
            prop_cm = None
        span_cm = None

    handle = _Handle(c, trace_id) if span_cm is not None else _NullHandle()
    try:
        yield handle
    finally:
        # Exit in reverse order: span first, then the propagate context.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if span_cm is not None:
                try:
                    span_cm.__exit__(None, None, None)
                except Exception:
                    pass
            if prop_cm is not None:
                try:
                    prop_cm.__exit__(None, None, None)
                except Exception:
                    pass
            if span_cm is not None:
                try:
                    c.flush()
                except Exception:
                    pass


def record_generation(
    *,
    name: str,
    model: str,
    input: Any,
    output: str,
    model_parameters: dict | None = None,
    usage: dict | None = None,
    latency_s: float | None = None,
) -> None:
    """Record one completed LLM call as a Langfuse generation. Fail-open.

    Nests under the active eval span when one is open; otherwise creates a
    standalone trace. ``usage`` accepts a raw OpenAI-style usage dict
    (``prompt_tokens``/``completion_tokens``/``total_tokens``).
    """
    c = _client()
    if c is None:
        return
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            usage_details = None
            if usage:
                usage_details = {
                    k: int(v)
                    for k, v in {
                        "input": usage.get("prompt_tokens"),
                        "output": usage.get("completion_tokens"),
                        "total": usage.get("total_tokens"),
                    }.items()
                    if isinstance(v, int)
                }
            metadata = (
                {"latency_s": round(latency_s, 3)} if latency_s is not None else None
            )
            gen = c.start_observation(
                name=name,
                as_type="generation",
                model=model,
                input=input,
                output=output,
                model_parameters=model_parameters or None,
                usage_details=usage_details or None,
                metadata=metadata,
            )
            gen.end()
    except Exception:
        pass
