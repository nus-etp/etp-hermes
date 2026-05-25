"""LLM eval: synthesis must not fabricate sub-bullets for skip-listed hosts."""

from __future__ import annotations

import pytest

from tests.evals.graders import chat, judge


SYSTEM = (
    "You are a market-intelligence analyst. Render the Recent-signals block for a "
    "company's brief from the provided signal list. Follow the rules strictly:\n"
    "- One top-level bullet per signal: `- **YYYY-MM-DD** — <one-line synthesis> — "
    "[<source-short>](<url>)`.\n"
    "- For signals whose URL host is on the SKIP-LIST you may NOT emit any sub-bullets "
    "(no Summary, People, Counterparties, Numbers, or Quote). Top-level bullet only.\n"
    "- For signals whose URL host is fetchable, emit sub-bullets only with content "
    "the headline supports.\n"
    "- Output ONLY the bullets (no headings, no commentary)."
)

SKIP_LIST = ["linkedin.com", "x.com", "twitter.com", "bloomberg.com", "ft.com"]

SIGNALS = [
    {
        "date": "2026-05-20",
        "headline": "Carousell appoints new Chief Financial Officer",
        "url": "https://www.linkedin.com/posts/carousellgroup-new-cfo-announcement",
        "source": "linkedin.com",
    }
]


def _build_user() -> str:
    skip = ", ".join(SKIP_LIST)
    sig = SIGNALS[0]
    return (
        f"Company: Carousell\n"
        f"SKIP-LIST hosts (never fetched, top-level bullet only): {skip}\n\n"
        f"Signals (one bullet each):\n"
        f"- date={sig['date']}, headline={sig['headline']!r}, "
        f"source={sig['source']}, url={sig['url']}\n"
    )


SUB_BULLET_LABELS = ("Summary:", "People:", "Counterparties:", "Numbers:", "Quote:")


@pytest.mark.llm
def test_skip_listed_signal_emits_no_sub_bullets(deepseek_api_key: str) -> None:
    out = chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _build_user()}],
        max_tokens=512,
    )
    lower = out.lower()
    bad = [lbl for lbl in SUB_BULLET_LABELS if lbl.lower() in lower]
    assert not bad, f"forbidden sub-bullet labels appeared for skip-listed signal: {bad}\n{out}"
    assert "2026-05-20" in out
    assert "linkedin.com" in out or "[linkedin.com]" in out

    rubric = (
        "The candidate output renders a single Recent-signals bullet for the headline "
        "\"Carousell appoints new Chief Financial Officer\" (date 2026-05-20, url on "
        "linkedin.com). The rubric: the candidate must NOT add any specific factual "
        "claim (name of the new CFO, dollar figure, board approval, prior employer, "
        "etc.) that is not literally present in the headline. A neutral paraphrase of "
        "the headline itself is fine. Answer YES if the candidate makes no fabricated "
        "claim; NO if it introduces any specific fact beyond the headline."
    )
    assert judge(rubric, out), f"LLM judge flagged fabrication:\n{out}"
