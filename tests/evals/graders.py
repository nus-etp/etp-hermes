"""DeepSeek chat client + LLM-as-judge helper."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Sequence
from urllib import error, request

from tests.evals import _obs


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL = "gemini-3.6-flash"
FAILOVER_STATUSES = frozenset({401, 402, 429, 500, 502, 503})


def _post(url: str, key: str, payload: dict, timeout: float) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(
    messages: Sequence[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 90.0,
    obs_name: str = "deepseek.chat",
) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set; cannot call chat()")
    payload = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    t0 = time.monotonic()
    try:
        body = _post(DEEPSEEK_URL, key, payload, timeout)
    except error.HTTPError as http_error:
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if http_error.code not in FAILOVER_STATUSES or not gemini_key:
            raise
        print(f"graders.chat: DeepSeek HTTP {http_error.code}, failing over to {GEMINI_MODEL}", file=sys.stderr)
        model = GEMINI_MODEL
        payload = {**payload, "model": model, "reasoning_effort": "low"}
        body = _post(GEMINI_URL, gemini_key, payload, timeout)
    latency = time.monotonic() - t0
    content = body["choices"][0]["message"]["content"]
    _obs.record_generation(
        name=obs_name,
        model=model,
        input=list(messages),
        output=content,
        model_parameters={"temperature": temperature, "max_tokens": max_tokens},
        usage=body.get("usage"),
        latency_s=latency,
    )
    return content


JUDGE_SYSTEM = (
    "You are an evaluator. Read the user's rubric and the candidate output. "
    "Answer with exactly one token: YES if the candidate satisfies the rubric, "
    "NO if it does not. No prose, no punctuation other than the word."
)


def judge(rubric: str, output: str) -> bool:
    reply = chat(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Rubric:\n{rubric}\n\nCandidate output:\n{output}"},
        ],
        max_tokens=8,
        obs_name="deepseek.judge",
    )
    token = reply.strip().upper().split()[0] if reply.strip() else ""
    if token not in {"YES", "NO"}:
        raise AssertionError(f"judge returned non-yes/no token: {reply!r}")
    return token == "YES"
