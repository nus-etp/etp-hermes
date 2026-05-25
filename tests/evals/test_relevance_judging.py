"""LLM eval: Layer 1 firehose-to-watchlist relevance judging."""

from __future__ import annotations

import json
import re

import pytest

from tests.evals.graders import chat


WATCHLIST = [
    {
        "name": "Carousell",
        "description": "Singapore-headquartered consumer marketplace. Layer 1 keeps signals about Carousell the company; drops generic recommerce trend pieces that don't name it.",
    },
    {
        "name": "Horizon Quantum Computing",
        "description": "Singapore quantum-computing software startup. Keep funding, hiring, product, exec, or scientific milestones; drop generic quantum-computing market commentary that doesn't reference Horizon specifically.",
    },
    {
        "name": "Patsnap",
        "description": "Singapore-headquartered IP intelligence and innovation analytics SaaS. Keep funding, hiring, product, exec moves; drop generic patent-news pieces.",
    },
]


FIXTURE = [
    ("h1", "Carousell raises $100M Series E led by STIC Investments", "techcrunch.com", "Carousell"),
    ("h2", "Singapore recommerce market grows 35% YoY, report says", "vulcanpost.com", None),
    ("h3", "Horizon Quantum Computing posts Q1 2026 results", "businesstimes.com.sg", "Horizon Quantum Computing"),
    ("h4", "What is quantum supremacy? A primer for executives", "channelnewsasia.com", None),
    ("h5", "Patsnap hiring across AI Agent Platform team", "lever.co/patsnap", "Patsnap"),
    ("h6", "Top 10 SaaS companies in APAC 2026", "techinasia.com", None),
    ("h7", "Carousell appoints new CFO ahead of IPO push", "press.carousell.com", "Carousell"),
    ("h8", "Five Southeast Asia unicorns to watch in 2026", "agfundernews.com", None),
    ("h9", "Horizon Quantum signs research MOU with NUS CQT", "nus.edu.sg", "Horizon Quantum Computing"),
    ("h10", "Patent filing volumes hit decade low in 2025", "ft.com", None),
]

SYSTEM = (
    "You are a market-intelligence analyst. For each headline you receive, decide which "
    "(if any) watchlist company it is *specifically* about. Return a JSON object mapping "
    "each input id to either the canonical company name (exact match from the watchlist) "
    "or null if it is generic news that does not name any watchlist company. Do not invent "
    "companies. No commentary. Output JSON only, no markdown fences."
)


def _build_user(watchlist: list[dict], fixture: list[tuple]) -> str:
    lines = ["Watchlist:"]
    for c in watchlist:
        lines.append(f"- {c['name']}: {c['description']}")
    lines.append("")
    lines.append("Headlines:")
    for i, headline, source, _ in fixture:
        lines.append(f"{i}: {headline}  [source: {source}]")
    lines.append("")
    lines.append('Respond with: {"h1": "<company name or null>", ...}')
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, object]:
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise AssertionError(f"no JSON object in model output: {text!r}")
    return json.loads(m.group(0))


@pytest.mark.llm
def test_relevance_precision_and_recall(deepseek_api_key: str) -> None:
    user = _build_user(WATCHLIST, FIXTURE)
    out = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        max_tokens=512,
    )
    answers = _parse_json(out)

    tp = fp = fn = 0
    for hid, _, _, expected in FIXTURE:
        actual = answers.get(hid)
        if isinstance(actual, str) and actual.strip().lower() == "null":
            actual = None
        if expected is None and actual is None:
            continue
        if expected is not None and actual == expected:
            tp += 1
        elif expected is not None and actual is None:
            fn += 1
        elif expected is None and actual is not None:
            fp += 1
        else:
            fn += 1
            fp += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    assert precision >= 0.75, f"precision {precision:.2f} below 0.75; answers={answers}"
    assert recall >= 0.75, f"recall {recall:.2f} below 0.75; answers={answers}"
