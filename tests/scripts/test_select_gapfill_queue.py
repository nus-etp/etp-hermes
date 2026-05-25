"""Unit tests for scripts/select_gapfill_queue.py."""

from __future__ import annotations

import datetime as dt
import json
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


def test_load_state_handles_missing_and_malformed(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("select_gapfill_queue")
    assert mod.load_state(tmp_repo / "signals" / "absent.json") == {}
    malformed = tmp_repo / "signals" / "bad.json"
    malformed.write_text("not json")
    assert mod.load_state(malformed) == {}
    wrong_shape = tmp_repo / "signals" / "wrong.json"
    wrong_shape.write_text(json.dumps([1, 2, 3]))
    assert mod.load_state(wrong_shape) == {}
