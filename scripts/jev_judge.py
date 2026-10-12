#!/usr/bin/env python3
"""Blind relevance judge backed by TypeSafe's Jev classifier.

Jev is a fast "System One" classifier priced per input token, cheap enough to
rule on every Layer 1 verdict and Layer 2 kept item each day. The item text,
the company description and the ingest relevance criteria go in as ``state``;
a ``choice`` question returns keep/drop with probabilities and a ``noul``
question scores whether the item is third-party coverage or a material event.

Fail-open: a missing key, HTTP error, timeout or unparseable reply returns a
result with ``verdict: None`` and the failure in ``reason``. Pure stdlib.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_TIMEOUT_SECS = 30.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SECS = 1.0
MAX_BACKOFF_SECS = 20.0
RETRYABLE_STATUSES = {429, 529, 500, 502, 503, 504}
MAX_STATE_CHARS = 60000

VERDICT_QUESTION = "verdict"
MATERIAL_QUESTION = "material"

VERDICT_INSTRUCTIONS = (
    "You are the editorial filter for a private-market intelligence digest that tracks specific "
    "early-stage, mostly Singapore-linked companies. Decide whether the candidate item earns a place "
    "in the digest. The company description is authoritative for identity."
)
VERDICT_CRITERIA = {
    "keep": (
        "The item is primarily about the tracked company as described and teaches the reader something "
        "new about it: funding, product launch, partnership, customer, hiring, regulatory event, founder "
        "move, acquisition, shutdown, award or program selection. Third-party coverage or a material "
        "event, even from small trade press."
    ),
    "drop": (
        "The item is about a different entity that merely shares the name (public ticker, band, foreign "
        "company, generic word); or the company is only a passing mention in a list or roundup; or it is "
        "the company's own evergreen marketing, a page-navigation label, a generic job requisition with "
        "no information, a version-revision notice, or stale news with nothing new."
    ),
}
MATERIAL_INSTRUCTIONS = (
    "Is this item third-party coverage of, or itself announcing, a material new event about the tracked "
    "company (rather than evergreen marketing, navigation chrome or an unrelated entity)?"
)


def have_key() -> bool:
    return bool(os.environ.get(KEY_ENV))


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def max_attempts() -> int:
    return max(1, int(_env_number("JEV_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)))


def backoff_base_secs() -> float:
    return max(0.0, _env_number("JEV_BACKOFF_SECS", DEFAULT_BACKOFF_SECS))


def build_state(row: dict) -> str:
    lines = [
        f"Tracked company: {row.get('company', '')}",
        f"Company description: {row.get('company_description', '')}",
        f"Source: {row.get('source', '')}",
    ]
    if row.get("date"):
        lines.append(f"Date: {row['date']}")
    lines.append(f"Headline: {row.get('headline', '')}")
    lines.append(f"Item description: {row.get('description', '')}")
    return "\n".join(lines)[:MAX_STATE_CHARS]


def build_payload(row: dict) -> dict:
    return {
        "state": build_state(row),
        "model": MODEL,
        "questions": {
            VERDICT_QUESTION: {
                "type": "choice",
                "instructions": VERDICT_INSTRUCTIONS,
                "criteria": VERDICT_CRITERIA,
            },
            MATERIAL_QUESTION: {"type": "noul", "instructions": MATERIAL_INSTRUCTIONS},
        },
    }


def empty_result(reason: str) -> dict:
    return {"verdict": None, "confidence": None, "reason": reason, "probabilities": {}, "usage": {}}


def parse_response(body: dict) -> dict:
    answers = body.get("answers") if isinstance(body, dict) else None
    answer = answers.get(VERDICT_QUESTION) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("choice") not in VERDICT_CRITERIA:
        return empty_result("unparseable reply")
    material = answers.get(MATERIAL_QUESTION)
    material_score = material.get("noul") if isinstance(material, dict) else None
    confidence = answer.get("confidence")
    reason = f"jev material={material_score}" if material_score is not None else "jev"
    return {
        "verdict": answer["choice"],
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
        "reason": reason,
        "probabilities": answer.get("probabilities") or {},
        "usage": body.get("usage") or {},
    }


def build_request(row: dict) -> urllib.request.Request:
    return urllib.request.Request(
        API_URL,
        data=json.dumps(build_payload(row)).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ.get(KEY_ENV, '')}", "Content-Type": "application/json"},
        method="POST",
    )


def retry_delay(attempt: int) -> float:
    delay = min(MAX_BACKOFF_SECS, backoff_base_secs() * 2 ** (attempt - 1))
    return delay * (1 + random.uniform(0, 0.25))


def is_retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_STATUSES
    return isinstance(error, (urllib.error.URLError, TimeoutError))


def describe(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    return type(error).__name__


def post_with_retries(row: dict, timeout: float) -> tuple[dict | None, str]:
    attempts = max_attempts()
    failure = "no attempt"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(build_request(row), timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")), ""
        except (urllib.error.URLError, OSError, ValueError) as error:
            failure = describe(error)
            if not is_retryable(error) or attempt == attempts:
                break
            time.sleep(retry_delay(attempt))
    return None, failure


def judge_item(row: dict, *, timeout: float = DEFAULT_TIMEOUT_SECS) -> dict:
    if not have_key():
        return empty_result(f"{KEY_ENV} not set")
    body, failure = post_with_retries(row, timeout)
    if body is None:
        return empty_result(f"jev request failed: {failure}")
    return parse_response(body)
