"""LLM eval: first-time synthesis output adheres to the brief template."""

from __future__ import annotations

import re

import pytest

from tests.evals.graders import chat


SYSTEM = (
    "You are a market-intelligence analyst writing a first-time LIVING_BRIEF.md for "
    "one company. Follow the template exactly:\n\n"
    "```\n"
    "# <Company name> — LIVING BRIEF\n"
    "_Last updated: <YYYY-MM-DD HH:MM UTC>_\n"
    "![Infographic](infographic.png)\n\n"
    "## Thesis\n"
    "<2-3 sentences>\n\n"
    "## Profile\n"
    "- Sector: ...\n"
    "- Region: ...\n\n"
    "## Recent signals\n"
    "- **<YYYY-MM-DD>** — <one-line synthesis> — [<source>](<url>)\n\n"
    "## Older signals\n"
    "_none_\n\n"
    "## Open questions\n"
    "- <question>\n"
    "```\n\n"
    "Rules: H1 must end with ` — LIVING BRIEF`. `_Last updated:_` line must use "
    "exactly the format shown. The `![Infographic](infographic.png)` line is "
    "mandatory and immediately follows `_Last updated:_`. Section headings appear "
    "in the exact order: Thesis, Profile, Recent signals, Older signals, Open "
    "questions. No extra sections, no decorative headers, no emojis. Output the "
    "brief markdown only, no commentary."
)


COMPANY = {
    "name": "Acme Robotics",
    "description": (
        "Singapore-headquartered industrial robotics startup building autonomous "
        "warehouse robots for Southeast Asia logistics customers."
    ),
    "signals": [
        ("2026-05-20", "Acme Robotics closes $25M Series B led by Sequoia SEA", "techcrunch.com", "https://techcrunch.com/2026/05/20/acme-series-b/"),
        ("2026-05-19", "Acme appoints former GoTo VP as Chief Revenue Officer", "press.acme.example", "https://press.acme.example/cro-hire"),
    ],
}


def _build_user() -> str:
    sigs = "\n".join(
        f"- date={d}, headline={h!r}, source={s}, url={u}"
        for d, h, s, u in COMPANY["signals"]
    )
    return (
        f"Company: {COMPANY['name']}\n"
        f"Description: {COMPANY['description']}\n\n"
        f"Today (UTC): 2026-05-20 14:00 UTC\n\n"
        f"New signals:\n{sigs}\n"
    )


REQUIRED_SECTIONS = (
    "## Thesis",
    "## Profile",
    "## Recent signals",
    "## Older signals",
    "## Open questions",
)
H1_RE = re.compile(r"^# (.+) — LIVING BRIEF$")
LAST_UPDATED_RE = re.compile(r"^_Last updated: \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC_$")


@pytest.mark.llm
def test_first_time_brief_matches_template(deepseek_api_key: str) -> None:
    out = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _build_user()}],
        max_tokens=1024,
    )
    lines = out.strip().splitlines()
    assert H1_RE.match(lines[0]), f"line 1 not a valid LIVING BRIEF H1: {lines[0]!r}\n{out}"
    assert LAST_UPDATED_RE.match(lines[1]), f"line 2 not a valid _Last updated:_ line: {lines[1]!r}\n{out}"
    assert lines[2] == "![Infographic](infographic.png)", f"line 3 must be the infographic marker: {lines[2]!r}\n{out}"

    cursor = 0
    for heading in REQUIRED_SECTIONS:
        idx = out.find("\n" + heading + "\n", cursor)
        assert idx > 0, f"missing required heading {heading!r} (cursor={cursor}):\n{out}"
        cursor = idx
