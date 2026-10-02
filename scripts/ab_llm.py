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
import urllib.error
import urllib.request
from typing import NamedTuple

DEFAULT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_RUNTIME = "deepseek"


class Runtime(NamedTuple):
    url_env: str
    url_default: str
    key_env: str
    model_env: str
    model_default: str


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
    ),
}

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
        content = body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, TypeError, ValueError):
        return None
    _record_usage(runtime, body.get("usage"))
    return content


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
