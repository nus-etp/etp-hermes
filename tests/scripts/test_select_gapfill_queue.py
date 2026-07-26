"""Unit tests for scripts/select_gapfill_queue.py."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path


def _setup(tmp_repo: Path, names: list[str], state: dict[str, str] | None = None) -> None:
    (tmp_repo / "data" / "companies.json").write_text(
        json.dumps([{"name": n, "description": "x"} for n in names])
    )
    if state is not None:
        (tmp_repo / "signals" / "agent-queue-state.json").write_text(
            json.dumps({"last_queried": state})
        )


def test_never_queried_companies_surface_first(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    _setup(tmp_repo, ["Alpha", "Bravo", "Charlie"], state={"Alpha": "2026-05-01"})
    out = mod.select_queue(
        ["Alpha", "Bravo", "Charlie"],
        {"Alpha": "2026-05-01"},
        covered=set(),
        size=10,
    )
    assert out == ["Bravo", "Charlie", "Alpha"]


def test_ties_break_alphabetically_case_insensitive(scripts_module_loader) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    out = mod.select_queue(
        ["bravo", "Alpha", "charlie"],
        state={"bravo": "2026-05-01", "Alpha": "2026-05-01", "charlie": "2026-05-01"},
        covered=set(),
        size=10,
    )
    assert out == ["Alpha", "bravo", "charlie"]


def test_covered_companies_excluded(scripts_module_loader) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    out = mod.select_queue(
        ["Alpha", "Bravo", "Charlie"],
        state={},
        covered={"Bravo"},
        size=10,
    )
    assert "Bravo" not in out
    assert set(out) == {"Alpha", "Charlie"}


def test_covered_window_scans_last_7_days(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    today = dt.date(2026, 5, 20)
    (tmp_repo / "signals" / "updates" / f"{today - dt.timedelta(days=6)}.md").write_text(
        "# Company Updates\n## Acme\n- **launch** — Acme\n  https://x\n"
    )
    (tmp_repo / "signals" / "updates" / f"{today - dt.timedelta(days=8)}.md").write_text(
        "# Company Updates\n## Beta\n- **launch** — Beta\n  https://y\n"
    )
    covered = mod.covered_in_window(tmp_repo / "signals" / "updates", today, 7)
    assert covered == {"Acme"}


def test_run_at_dividers_not_treated_as_company(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    today = dt.date(2026, 5, 20)
    (tmp_repo / "signals" / "updates" / f"{today}.md").write_text(
        "# Company Updates\n## Run at 13:00 UTC\n## Acme\n- **x** — Acme\n  https://x\n"
    )
    covered = mod.covered_in_window(tmp_repo / "signals" / "updates", today, 1)
    assert covered == {"Acme"}


def test_size_parameter_caps_queue(scripts_module_loader) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    names = [f"C{i:02d}" for i in range(20)]
    out = mod.select_queue(names, state={}, covered=set(), size=5)
    assert len(out) == 5
    assert out == sorted(names)[:5]


def _write_brief(tmp_repo: Path, slug: str, body: str) -> None:
    d = tmp_repo / "signals" / "briefs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "LIVING_BRIEF.md").write_text(body)


def test_open_questions_extracted_from_brief(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    _write_brief(
        tmp_repo,
        "neu-battery-materials",
        "# NEU Battery Materials — LIVING BRIEF\n"
        "## Recent signals\n"
        "- **2026-05-01** — something — [x](https://x)\n"
        "## Open questions\n"
        "- Who led the seed round?\n"
        "- Any pilot customers announced?\n",
    )
    out = mod.load_open_questions(tmp_repo / "signals" / "briefs", "NEU Battery Materials")
    assert out == ["Who led the seed round?", "Any pilot customers announced?"]


def test_open_questions_capped_at_four(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    bullets = "\n".join(f"- Question {i}?" for i in range(6))
    _write_brief(tmp_repo, "acme", f"# Acme — LIVING BRIEF\n## Open questions\n{bullets}\n")
    out = mod.load_open_questions(tmp_repo / "signals" / "briefs", "Acme")
    assert out == [f"Question {i}?" for i in range(4)]


def test_open_questions_missing_brief_is_empty(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    assert mod.load_open_questions(tmp_repo / "signals" / "briefs", "Ghost Co") == []


def test_open_questions_stops_at_next_h2(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    _write_brief(
        tmp_repo,
        "acme",
        "# Acme — LIVING BRIEF\n"
        "## Open questions\n"
        "- Real question?\n"
        "## Recent signals\n"
        "- **2026-05-01** — not a question — [x](https://x)\n",
    )
    out = mod.load_open_questions(tmp_repo / "signals" / "briefs", "Acme")
    assert out == ["Real question?"]


def test_open_questions_malformed_section_is_empty(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    briefs = tmp_repo / "signals" / "briefs"
    # `_none open_` placeholder / prose / indented sub-bullets: no top-level bullets.
    _write_brief(tmp_repo, "acme", "# Acme — LIVING BRIEF\n## Open questions\n_none open_\n")
    assert mod.load_open_questions(briefs, "Acme") == []
    _write_brief(tmp_repo, "beta", "# Beta — LIVING BRIEF\n## Open questions\nsome prose\n  - indented\n")
    assert mod.load_open_questions(briefs, "Beta") == []
    # Section absent entirely.
    _write_brief(tmp_repo, "gamma", "# Gamma — LIVING BRIEF\n## Recent signals\n- **2026-05-01** — x\n")
    assert mod.load_open_questions(briefs, "Gamma") == []


def test_main_writes_queue_entries_with_open_questions(
    scripts_module_loader, tmp_repo: Path
) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    _setup(tmp_repo, ["Alpha", "Bravo"])
    _write_brief(
        tmp_repo,
        "alpha",
        "# Alpha — LIVING BRIEF\n## Open questions\n- Funding stage?\n",
    )
    argv = sys.argv
    sys.argv = [
        "select_gapfill_queue.py",
        "--repo-root",
        str(tmp_repo),
        "--date",
        "2026-05-20",
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = argv

    # txt shape untouched: one name per line (layer guard + slicer depend on it).
    assert (tmp_repo / "signals" / "agent-queue.txt").read_text() == "Alpha\nBravo\n"
    entries = json.loads((tmp_repo / "data" / "agent-open-questions.json").read_text())
    assert entries == [
        {"name": "Alpha", "open_questions": ["Funding stage?"]},
        {"name": "Bravo", "open_questions": []},
    ]


def test_load_state_handles_missing_and_malformed(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    assert mod.load_state(tmp_repo / "signals" / "absent.json") == {}
    malformed = tmp_repo / "signals" / "bad.json"
    malformed.write_text("not json")
    assert mod.load_state(malformed) == {}
    wrong_shape = tmp_repo / "signals" / "wrong.json"
    wrong_shape.write_text(json.dumps([1, 2, 3]))
    assert mod.load_state(wrong_shape) == {}
