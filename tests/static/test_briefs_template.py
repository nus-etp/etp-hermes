"""Brief template adherence (prompts/synthesis.md "Brief template")."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._slug import slug

H1_RE = re.compile(r"^# (.+) — LIVING BRIEF$")
LAST_UPDATED_RE = re.compile(r"^_Last updated: \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC_$")
INFOGRAPHIC_LINE = "![Infographic](infographic.png)"
REQUIRED_SECTIONS_IN_ORDER = (
    "## Thesis",
    "## Profile",
    "## Recent signals",
    "## Older signals",
    "## Open questions",
)
SIGNAL_BULLET_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2}|date unknown)\*\* — ")
# New-template lines (prompts/synthesis.md). Asserted per-brief only when present —
# legacy briefs predate these requirements; the prompt's template block always asserts them.
LAST_MATERIAL_EVENT_RE = re.compile(r"^_Last material event: (\d{4}-\d{2}-\d{2} — .+|none on record)_$")
NO_FUNDING_LINE = "_No disclosed funding._"


def _all_brief_paths(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "signals" / "briefs").glob("*/LIVING_BRIEF.md"))


def pytest_generate_tests(metafunc):
    if "brief_path" in metafunc.fixturenames:
        repo_root = Path(__file__).resolve().parents[2]
        paths = _all_brief_paths(repo_root)
        metafunc.parametrize("brief_path", paths, ids=[p.parent.name for p in paths])


def test_brief_header(brief_path: Path) -> None:
    lines = brief_path.read_text().splitlines()
    assert len(lines) >= 3, f"{brief_path} too short to have a valid header"
    assert H1_RE.match(lines[0]), f"{brief_path}: line 1 must match `# <name> — LIVING BRIEF`, got {lines[0]!r}"
    assert LAST_UPDATED_RE.match(lines[1]), (
        f"{brief_path}: line 2 must match `_Last updated: YYYY-MM-DD HH:MM UTC_`, got {lines[1]!r}"
    )
    assert lines[2] == INFOGRAPHIC_LINE, (
        f"{brief_path}: line 3 must be exactly `{INFOGRAPHIC_LINE}`. Got {lines[2]!r}"
    )


def test_brief_slug_matches_name(brief_path: Path) -> None:
    lines = brief_path.read_text().splitlines()
    m = H1_RE.match(lines[0])
    assert m, f"{brief_path}: H1 not parseable"
    expected = slug(m.group(1).strip())
    assert expected == brief_path.parent.name, (
        f"{brief_path}: parent dir {brief_path.parent.name!r} does not match slug={expected!r}"
    )


def test_brief_required_sections_in_order(brief_path: Path) -> None:
    text = brief_path.read_text()
    last = -1
    for heading in REQUIRED_SECTIONS_IN_ORDER:
        idx = text.find("\n" + heading + "\n")
        if idx == -1 and not text.endswith(heading):
            pytest.fail(f"{brief_path}: missing required heading {heading!r}")
        assert idx > last, (
            f"{brief_path}: heading {heading!r} appears out of order "
            f"(prev offset {last}, this offset {idx})"
        )
        last = idx


def test_recent_signals_bullets_are_dated(brief_path: Path) -> None:
    text = brief_path.read_text()
    start = text.find("\n## Recent signals\n")
    if start == -1:
        return
    nxt = text.find("\n## ", start + 1)
    body = text[start : nxt if nxt != -1 else len(text)]

    bad = [line for line in body.splitlines() if line.startswith("- ") and not SIGNAL_BULLET_RE.match(line)]
    assert not bad, f"{brief_path}: malformed Recent-signals bullets:\n  - " + "\n  - ".join(bad)


def test_last_material_event_line_well_formed(brief_path: Path) -> None:
    """If a brief carries a `_Last material event:` line it must be well-formed and live in Thesis.

    Not required on legacy briefs (they predate the rule); the prompt-template test below
    asserts the template itself mandates it for new/updated briefs.
    """
    text = brief_path.read_text()
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith("_Last material event")]
    assert len(matches) <= 1, f"{brief_path}: multiple `_Last material event:` lines"
    for i in matches:
        assert LAST_MATERIAL_EVENT_RE.match(lines[i]), (
            f"{brief_path}: malformed `_Last material event:` line: {lines[i]!r} "
            "(must be `_Last material event: YYYY-MM-DD — <description>_` or "
            "`_Last material event: none on record_`)"
        )
        thesis = text.find("\n## Thesis\n")
        offset = sum(len(l) + 1 for l in lines[:i])
        nxt = text.find("\n## ", thesis + 1)
        assert thesis != -1 and thesis < offset < (nxt if nxt != -1 else len(text)), (
            f"{brief_path}: `_Last material event:` line must be inside the Thesis section"
        )


def test_funding_history_placement_and_fallback(brief_path: Path) -> None:
    """If `## Funding history` is present, it sits between Profile and Recent signals
    and its body is non-empty (round bullets or the explicit `_No disclosed funding._` line).

    Presence itself is not asserted on legacy briefs; the prompt-template test asserts
    the template requires the section going forward.
    """
    text = brief_path.read_text()
    fh = text.find("\n## Funding history\n")
    if fh == -1:
        return
    profile = text.find("\n## Profile\n")
    recent = text.find("\n## Recent signals\n")
    assert profile != -1 and recent != -1 and profile < fh < recent, (
        f"{brief_path}: `## Funding history` must appear between `## Profile` and `## Recent signals`"
    )
    nxt = text.find("\n## ", fh + 1)
    body = text[fh + len("\n## Funding history\n") : nxt if nxt != -1 else len(text)]
    has_rounds = any(line.startswith("- ") for line in body.splitlines())
    assert has_rounds or NO_FUNDING_LINE in body, (
        f"{brief_path}: `## Funding history` body must contain round bullets or the "
        f"explicit `{NO_FUNDING_LINE}` line"
    )


def test_prompt_template_block_requires_new_sections() -> None:
    """The synthesis prompt's template block must mandate the new required elements:
    the `_Last material event:` line under Thesis, the (always-present) `## Funding history`
    section with its `_No disclosed funding._` fallback, and the `### Hiring` subsection
    inside Recent signals."""
    repo_root = Path(__file__).resolve().parents[2]
    prompt = (repo_root / "prompts" / "synthesis.md").read_text()

    m = re.search(r"```markdown\n(.*?)```", prompt, flags=re.DOTALL)
    assert m, "prompts/synthesis.md: template block (```markdown fence) not found"
    template = m.group(1)

    thesis = template.find("## Thesis")
    lme = template.find("_Last material event:")
    profile = template.find("## Profile")
    assert thesis != -1 and profile != -1, "template must contain Thesis and Profile headings"
    assert thesis < lme < profile, (
        "template must place the `_Last material event:` line inside the Thesis section"
    )

    fh = template.find("## Funding history")
    recent = template.find("## Recent signals")
    assert profile < fh < recent, (
        "template must place `## Funding history` between Profile and Recent signals"
    )

    hiring = template.find("### Hiring")
    older = template.find("## Older signals")
    assert recent < hiring < older, (
        "template must place the `### Hiring` subsection inside Recent signals"
    )

    assert NO_FUNDING_LINE in prompt, (
        f"prompt must define the `{NO_FUNDING_LINE}` fallback for empty funding history"
    )
