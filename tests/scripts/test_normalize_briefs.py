"""Unit tests for scripts/normalize_briefs.py."""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

TODAY = dt.date(2026, 7, 26)  # cutoff at --days 30 → 2026-06-26


def _brief(recent: str, older: str = "_none_\n") -> str:
    """Synthetic brief modeled on the real template (see signals/briefs/)."""
    return (
        "# Acme — LIVING BRIEF\n"
        "_Last updated: 2026-07-25 14:00 UTC_\n"
        "![Infographic](infographic.png)\n"
        "\n"
        "## Thesis\n"
        "Acme thesis paragraph.\n"
        "\n"
        "## Profile\n"
        "- Sector: SaaS\n"
        "- Region: Singapore\n"
        "\n"
        "## Funding history\n"
        "- **2021-05** — Seed, $1M — Investor X — [src](https://x.example/seed)\n"
        "- **2019** — Angel, $100K — Investor Y — [src](https://x.example/angel)\n"
        "\n"
        f"## Recent signals\n{recent}"
        "\n"
        f"## Older signals\n{older}"
        "\n"
        "## Open questions\n"
        "- What next?\n"
    )


def _write_brief(tmp_path: Path, body: str, slug: str = "acme") -> Path:
    p = tmp_path / "briefs" / slug / "LIVING_BRIEF.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _run_main(mod, tmp_path: Path, *extra: str) -> int:
    argv = sys.argv
    sys.argv = [
        "normalize_briefs.py",
        "--briefs-dir",
        str(tmp_path / "briefs"),
        "--today",
        TODAY.isoformat(),
        *extra,
    ]
    try:
        return mod.main()
    finally:
        sys.argv = argv


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("normalize_briefs")


def test_sorts_recent_descending(mod) -> None:
    recent = (
        "- **2026-07-10** — mid — [s](https://a/1)\n"
        "- **2026-07-20** — newest — [s](https://a/2)\n"
        "- **2026-07-01** — oldest — [s](https://a/3)\n"
    )
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 0
    bullets = [l for l in out.splitlines() if l.startswith("- **2026-07")]
    assert bullets == [
        "- **2026-07-20** — newest — [s](https://a/2)",
        "- **2026-07-10** — mid — [s](https://a/1)",
        "- **2026-07-01** — oldest — [s](https://a/3)",
    ]


def test_rotation_across_cutoff_and_older_resort(mod) -> None:
    recent = (
        "- **2026-07-20** — stays — [s](https://a/1)\n"
        "- **2026-06-01** — rotates — [s](https://a/2)\n"
    )
    older = "- **2026-05-15** — already old — [s](https://a/3)\n"
    out, moved = mod.normalize_text(_brief(recent, older), TODAY, 30)
    assert moved == 1

    recent_body = out.split("## Recent signals\n")[1].split("## Older signals\n")[0]
    older_body = out.split("## Older signals\n")[1].split("## Open questions\n")[0]
    assert "stays" in recent_body and "rotates" not in recent_body
    # Rotated block lands in Older, re-sorted descending.
    older_bullets = [l for l in older_body.splitlines() if l.startswith("- ")]
    assert older_bullets == [
        "- **2026-06-01** — rotates — [s](https://a/2)",
        "- **2026-05-15** — already old — [s](https://a/3)",
    ]


def test_cutoff_boundary_block_stays(mod) -> None:
    # Exactly `days` old (== cutoff) is not "older than" the cutoff.
    recent = "- **2026-06-26** — boundary — [s](https://a/1)\n"
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 0
    assert "boundary" in out.split("## Older signals")[0]


def test_sub_bullet_blocks_stay_attached(mod) -> None:
    block = (
        "- **2026-06-01** — rotates with sub-bullets — [s](https://a/1)\n"
        "  - Summary: the summary line.\n"
        "  - People: Jane Doe (CEO)\n"
        "  - Numbers: $1M revenue\n"
        '  - Quote: "quoted." — Jane Doe\n'
        "  - Counterparties: Investor X\n"
    )
    recent = block + "- **2026-07-20** — stays — [s](https://a/2)\n"
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 1
    # Whole block moved to Older, contiguous and byte-identical.
    older_body = out.split("## Older signals\n")[1].split("\n## Open questions")[0]
    assert block.rstrip("\n") in older_body
    assert "Summary: the summary line." not in out.split("## Older signals")[0]


def test_undated_blocks_sink_below_dated_in_order(mod) -> None:
    recent = (
        "- **date unknown** — undated first — [s](https://a/1)\n"
        "- **2026-07-10** — dated — [s](https://a/2)\n"
        "- **date unknown** — undated second — [s](https://a/3)\n"
    )
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 0  # undated blocks never rotate
    recent_body = out.split("## Recent signals\n")[1].split("## Older signals")[0]
    bullets = [l for l in recent_body.splitlines() if l.startswith("- **")]
    assert bullets == [
        "- **2026-07-10** — dated — [s](https://a/2)",
        "- **date unknown** — undated first — [s](https://a/1)",
        "- **date unknown** — undated second — [s](https://a/3)",
    ]


def test_placeholder_added_when_recent_empties(mod) -> None:
    recent = "- **2026-05-01** — very old — [s](https://a/1)\n"
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 1
    recent_body = out.split("## Recent signals\n")[1].split("## Older signals\n")[0]
    assert recent_body == "_none_\n\n"


def test_placeholder_removed_when_older_gains(mod) -> None:
    recent = (
        "- **2026-07-20** — stays — [s](https://a/1)\n"
        "- **2026-06-01** — rotates — [s](https://a/2)\n"
    )
    out, _ = mod.normalize_text(_brief(recent, "_none_\n"), TODAY, 30)
    older_body = out.split("## Older signals\n")[1].split("## Open questions\n")[0]
    assert "_none_" not in older_body
    assert "rotates" in older_body


def test_indented_placeholder_recognized_and_normalized_flush(mod) -> None:
    # Some committed briefs carry `  _none_` (indented); it must parse as the
    # placeholder and be rewritten flush.
    recent = "- **2026-07-20** — item — [s](https://a/1)\n"
    out, _ = mod.normalize_text(_brief(recent, "  _none_\n"), TODAY, 30)
    assert "\n## Older signals\n_none_\n" in out
    assert "  _none_" not in out


def test_funding_history_untouched(mod) -> None:
    # Funding history bullets are out of order and use partial dates; the
    # prefix must be preserved byte-for-byte.
    body = _brief("- **2026-07-20** — item — [s](https://a/1)\n")
    out, _ = mod.normalize_text(body, TODAY, 30)
    assert out.split("## Recent signals")[0] == body.split("## Recent signals")[0]


def test_idempotent(mod) -> None:
    recent = (
        "- **2026-06-01** — rotates — [s](https://a/1)\n"
        "  - Summary: sub.\n"
        "\n"
        "- **2026-07-20** — stays — [s](https://a/2)\n"
    )
    once, _ = mod.normalize_text(_brief(recent), TODAY, 30)
    twice, moved = mod.normalize_text(once, TODAY, 30)
    assert twice == once
    assert moved == 0


def test_unparseable_file_skipped_untouched(mod, tmp_path: Path, capsys) -> None:
    body = _brief("stray top-level prose, not a bullet\n")
    p = _write_brief(tmp_path, body)
    assert _run_main(mod, tmp_path) == 0
    assert p.read_text() == body
    captured = capsys.readouterr()
    assert "SKIP" in captured.err
    assert "0 changed, 0 unchanged, 1 skipped" in captured.out


def test_missing_sections_skipped(mod, tmp_path: Path, capsys) -> None:
    p = _write_brief(tmp_path, "# Acme — LIVING BRIEF\n\n## Thesis\nNo signal sections.\n")
    assert _run_main(mod, tmp_path) == 0
    assert "No signal sections." in p.read_text()
    assert "SKIP" in capsys.readouterr().err


def test_noop_file_not_rewritten(mod, tmp_path: Path, capsys) -> None:
    body = _brief("- **2026-07-20** — item — [s](https://a/1)\n")
    normalized, _ = mod.normalize_text(body, TODAY, 30)
    p = _write_brief(tmp_path, normalized)
    stamp = 1_000_000_000  # distinctive old mtime
    os.utime(p, (stamp, stamp))
    assert _run_main(mod, tmp_path) == 0
    assert p.stat().st_mtime == stamp  # not rewritten
    assert "0 changed, 1 unchanged, 0 skipped" in capsys.readouterr().out


def test_check_mode_reports_but_does_not_write(mod, tmp_path: Path, capsys) -> None:
    body = _brief(
        "- **2026-07-10** — mid — [s](https://a/1)\n"
        "- **2026-07-20** — newest — [s](https://a/2)\n"
    )
    p = _write_brief(tmp_path, body)
    assert _run_main(mod, tmp_path, "--check") == 0
    assert p.read_text() == body
    out = capsys.readouterr().out
    assert "would change" in out
    assert "(dry run)" in out


def test_main_writes_and_reports(mod, tmp_path: Path, capsys) -> None:
    body = _brief(
        "- **2026-07-20** — stays — [s](https://a/1)\n"
        "- **2026-06-01** — rotates — [s](https://a/2)\n"
    )
    p = _write_brief(tmp_path, body)
    assert _run_main(mod, tmp_path) == 0
    out = capsys.readouterr().out
    assert "rotated 1 to Older" in out
    assert "1 changed, 0 unchanged, 0 skipped" in out
    new = p.read_text()
    assert "rotates" in new.split("## Older signals")[1]
    # And the written file is a fixed point.
    assert mod.normalize_text(new, TODAY, 30)[0] == new


def test_hiring_subsection_preserved_and_not_rotated(mod) -> None:
    # The synthesis template ends Recent signals with a `### Hiring`
    # subsection; it must stay at the end of Recent, verbatim, even when its
    # bullets are older than the cutoff.
    recent = (
        "- **2026-06-01** — rotates — [s](https://a/1)\n"
        "- **2026-07-20** — stays — [s](https://a/2)\n"
        "\n"
        "### Hiring\n"
        "- **2026-06-01** — rolled-up hiring read — [s](https://a/3)\n"
    )
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 1  # only the top-level 06-01 bullet rotates
    recent_body = out.split("## Recent signals\n")[1].split("## Older signals\n")[0]
    assert (
        "### Hiring\n- **2026-06-01** — rolled-up hiring read — [s](https://a/3)"
        in recent_body
    )
    assert recent_body.index("stays") < recent_body.index("### Hiring")
    assert "rotates" not in recent_body
    # Idempotent with the subsection present.
    assert mod.normalize_text(out, TODAY, 30)[0] == out


def test_hiring_subsection_alone_suppresses_placeholder(mod) -> None:
    recent = "### Hiring\n- **2026-07-20** — hiring read — [s](https://a/1)\n"
    out, moved = mod.normalize_text(_brief(recent), TODAY, 30)
    assert moved == 0
    recent_body = out.split("## Recent signals\n")[1].split("## Older signals\n")[0]
    assert "_none_" not in recent_body
    assert "### Hiring" in recent_body


def test_days_flag_changes_cutoff(mod) -> None:
    recent = "- **2026-07-10** — sixteen days old — [s](https://a/1)\n"
    _, moved_default = mod.normalize_text(_brief(recent), TODAY, 30)
    _, moved_tight = mod.normalize_text(_brief(recent), TODAY, 7)
    assert moved_default == 0
    assert moved_tight == 1
