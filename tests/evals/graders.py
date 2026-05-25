"""DeepSeek chat client + LLM-as-judge helper."""

from __future__ import annotations

import json
import os
from typing import Sequence
from urllib import request


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


def chat(
    messages: Sequence[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 90.0,
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
    req = request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


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
    )
    token = reply.strip().upper().split()[0] if reply.strip() else ""
    if token not in {"YES", "NO"}:
        raise AssertionError(f"judge returned non-yes/no token: {reply!r}")
    return token == "YES"
