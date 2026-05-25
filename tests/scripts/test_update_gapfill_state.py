"""Unit tests for scripts/update_gapfill_state.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _run(mod, tmp_repo: Path, date: str) -> int:
    argv = sys.argv
    sys.argv = ["update_gapfill_state.py", "--repo-root", str(tmp_repo), "--date", date]
    try:
        return mod.main()
    finally:
        sys.argv = argv


def _setup(tmp_repo: Path, names: list[str], queue: list[str], prior_state: dict | None = None) -> None:
    (tmp_repo / "data" / "companies.json").write_text(
        json.dumps([{"name": n, "description": "x"} for n in names])
    )
    (tmp_repo / "signals").mkdir(exist_ok=True)
    (tmp_repo / "signals" / "agent-queue.txt").write_text("\n".join(queue) + "\n")
    if prior_state is not None:
        (tmp_repo / "signals" / "agent-queue-state.json").write_text(json.dumps(prior_state))


def test_stamps_queued_companies(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("update_gapfill_state")
    _setup(tmp_repo, names=["Alpha", "Bravo"], queue=["Alpha", "Bravo"])
    assert _run(mod, tmp_repo, "2026-05-20") == 0
    state = json.loads((tmp_repo / "signals" / "agent-queue-state.json").read_text())
    assert state == {"last_queried": {"Alpha": "2026-05-20", "Bravo": "2026-05-20"}}


def test_prunes_stale_entries(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("update_gapfill_state")
    _setup(
        tmp_repo,
        names=["Alpha", "Bravo"],
        queue=["Alpha"],
        prior_state={"last_queried": {"Alpha": "2026-05-01", "Charlie": "2026-04-01"}},
    )
    _run(mod, tmp_repo, "2026-05-20")
    state = json.loads((tmp_repo / "signals" / "agent-queue-state.json").read_text())
    assert state["last_queried"] == {"Alpha": "2026-05-20"}


def test_unknown_names_in_queue_are_skipped(scripts_module_loader, tmp_repo: Path, capsys) -> None:
    mod = scripts_module_loader("update_gapfill_state")
    _setup(tmp_repo, names=["Alpha"], queue=["Alpha", "Ghost"])
    _run(mod, tmp_repo, "2026-05-20")
    state = json.loads((tmp_repo / "signals" / "agent-queue-state.json").read_text())
    assert state["last_queried"] == {"Alpha": "2026-05-20"}
    out = capsys.readouterr().out
    assert "skipped 1 unknown" in out
    assert "Ghost" in out


def test_missing_queue_file_noop(scripts_module_loader, tmp_repo: Path, capsys) -> None:
    mod = scripts_module_loader("update_gapfill_state")
    (tmp_repo / "data" / "companies.json").write_text(json.dumps([{"name": "Alpha", "description": "x"}]))
    assert _run(mod, tmp_repo, "2026-05-20") == 0
    assert "nothing to record" in capsys.readouterr().out
    assert not (tmp_repo / "signals" / "agent-queue-state.json").exists()


def test_roundtrip_with_select_gapfill_queue(scripts_module_loader, tmp_repo: Path) -> None:
    sel = scripts_module_loader("select_gapfill_queue")
    upd = scripts_module_loader("update_gapfill_state")

    names = ["Alpha", "Bravo", "Charlie", "Delta"]
    (tmp_repo / "data" / "companies.json").write_text(
        json.dumps([{"name": n, "description": "x"} for n in names])
    )

    first = sel.select_queue(names, state={}, covered=set(), size=2)
    assert first == ["Alpha", "Bravo"]

    (tmp_repo / "signals").mkdir(exist_ok=True)
    (tmp_repo / "signals" / "agent-queue.txt").write_text("\n".join(first) + "\n")
    _run(upd, tmp_repo, "2026-05-20")
    state = sel.load_state(tmp_repo / "signals" / "agent-queue-state.json")
    second = sel.select_queue(names, state=state, covered=set(), size=2)
    assert second == ["Charlie", "Delta"]
