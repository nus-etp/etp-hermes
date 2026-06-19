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
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


def have_key() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: float = 90.0,
) -> str | None:
    """Return the assistant message text, or ``None`` on any failure.

    Fail-open: no API key, an HTTP/network error, or a malformed response all
    return ``None`` so callers can leave the work undone and try again later
    rather than crashing the workflow step.
    """
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_URL)
    model = model or os.environ.get("AB_JUDGE_MODEL", DEFAULT_MODEL)
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
        return body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
        return None


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
