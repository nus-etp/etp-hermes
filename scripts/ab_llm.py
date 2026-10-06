#!/usr/bin/env python3
"""Minimal DeepSeek chat client shared by the A/B significance scripts.

``ab_judge.py`` (blind relevance labeler) and ``ab_backfill.py`` (arm replay)
both need to call the model from a plain deterministic script — not through a
full ``hermes -z`` session. This module is that one call site: an
OpenAI-compatible Chat Completions POST over stdlib ``urllib``, mirroring
``tests/evals/graders.py`` but with **fail-open** semantics suited to a
post-step (a missing key or a transient HTTP error returns ``None`` instead of
raising, so the A/B experiment never blocks the daily sync).

Pure stdlib. The endpoint/model default to DeepSeek's public API and can be
overridden via ``DEEPSEEK_BASE_URL`` / ``AB_JUDGE_MODEL`` for forks on another
OpenAI-compatible provider.

Runtimes: ``chat(..., runtime=...)`` / ``have_key(runtime)`` select the provider
the self-optimising harness replays against (``docs/harness-interfaces.md``):
``deepseek`` (the default, unchanged behaviour), ``nvidia`` and ``zai`` mirror
the primary and first fallback in ``hermes/config.yaml``. Each runtime reads its
base URL / key / model from env with the defaults in ``RUNTIMES``. Token usage
the provider reports is accumulated per runtime and exposed by ``usage_total``.
An unknown runtime behaves like a missing key (fail-open).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, NamedTuple

DEFAULT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_RUNTIME = "deepseek"


class Runtime(NamedTuple):
    url_env: str
    url_default: str
    key_env: str
    model_env: str
    model_default: str
    extra_payload: dict[str, Any] = {}


RUNTIMES: dict[str, Runtime] = {
    "deepseek": Runtime("DEEPSEEK_BASE_URL", DEFAULT_URL, "DEEPSEEK_API_KEY", "AB_JUDGE_MODEL", DEFAULT_MODEL),
    "nvidia": Runtime(
        "NVIDIA_BASE_URL",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "NVIDIA_API_KEY",
        "NVIDIA_MODEL",
        "deepseek-ai/deepseek-v4-flash-0731",
    ),
    "zai": Runtime(
        "ZAI_BASE_URL",
        "https://api.z.ai/api/paas/v4/chat/completions",
        "ZAI_API_KEY",
        "ZAI_MODEL",
        "glm-4.7-flash",
        {"thinking": {"type": "disabled"}},
    ),
}

ERROR_BODY_CHARS = 300

_usage_tokens: dict[str, int] = {}


def runtime_key(runtime: str = DEFAULT_RUNTIME) -> str | None:
    config = RUNTIMES.get(runtime)
    if config is None:
        return None
    return os.environ.get(config.key_env) or None


def runtime_url(runtime: str = DEFAULT_RUNTIME) -> str:
    config = RUNTIMES[runtime]
    return os.environ.get(config.url_env, config.url_default)


def runtime_model(runtime: str = DEFAULT_RUNTIME) -> str:
    config = RUNTIMES[runtime]
    return os.environ.get(config.model_env, config.model_default)


def have_key(runtime: str = DEFAULT_RUNTIME) -> bool:
    return runtime_key(runtime) is not None


def usage_total(runtime: str = DEFAULT_RUNTIME) -> int:
    return _usage_tokens.get(runtime, 0)


def _record_usage(runtime: str, usage: object) -> None:
    if not isinstance(usage, dict):
        return
    tokens = usage.get("total_tokens")
    if isinstance(tokens, int) and tokens > 0:
        _usage_tokens[runtime] = _usage_tokens.get(runtime, 0) + tokens


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: float = 90.0,
    runtime: str = DEFAULT_RUNTIME,
) -> str | None:
    """Return the assistant message text, or ``None`` on any failure.

    Fail-open: no API key, an HTTP/network error, or a malformed response all
    return ``None`` so callers can leave the work undone and try again later
    rather than crashing the workflow step.
    """
    key = runtime_key(runtime)
    if not key:
        return None
    url = runtime_url(runtime)
    model = model or runtime_model(runtime)
    payload = {
        **RUNTIMES[runtime].extra_payload,
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        choice = body["choices"][0]
        message = choice["message"]
    except urllib.error.HTTPError as error:
        _warn(runtime, f"HTTP {error.code}: {_error_body(error)}")
        return None
    except (urllib.error.URLError, OSError, KeyError, IndexError, TypeError, ValueError) as error:
        _warn(runtime, f"{type(error).__name__}: {error}")
        return None
    _record_usage(runtime, body.get("usage"))
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    if content:
        return content
    _warn(
        runtime,
        f"empty content (finish_reason={choice.get('finish_reason')}, "
        f"reasoning_content={'present' if reasoning else 'absent'})",
    )
    return reasoning if isinstance(reasoning, str) and reasoning.strip() else None


def _warn(runtime: str, detail: str) -> None:
    print(f"ab_llm[{runtime}]: {detail}", file=sys.stderr)


def _error_body(error: urllib.error.HTTPError) -> str:
    try:
        text = error.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return str(error.reason)
    return " ".join(text.split())[:ERROR_BODY_CHARS]


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply.

    Tolerates ```json fenced blocks and leading/trailing prose. Returns the
    parsed dict, or ``None`` if no object parses.
    """
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None
