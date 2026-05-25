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
