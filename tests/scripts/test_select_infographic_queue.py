"""Unit tests for scripts/select_infographic_queue.py.

The pre-step decides which briefs get a fresh Layer 4 infographic. The point of
the change it implements: a raw working-tree-vs-HEAD diff over-fires (normalize
reorders/rotates blocks, synthesis bumps `_Last updated:_` and appends
corroboration), so regeneration must be gated on a *genuinely new signal*, not
any file change. These tests pin that boundary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _brief(recent: str, older: str = "_none_\n", *, updated: str = "2026-08-01 14:00 UTC") -> str:
    """Synthetic brief modeled on the real template (see signals/briefs/)."""
    return (
        "# Acme — LIVING BRIEF\n"
        f"_Last updated: {updated}_\n"
        "![Infographic](infographic.png)\n"
        "\n"
        "## Thesis\n"
        "Acme thesis. _Last material event: 2026-07-29 — thing._\n"
        "\n"
        "## Funding history\n"
        "- **2021-05** — Seed, $1M — Investor X — [src](https://x.example/seed)\n"
        "\n"
        f"## Recent signals\n{recent}"
        "\n"
        f"## Older signals\n{older}"
        "\n"
        "## Open questions\n"
        "- What next?\n"
    )


SIGNAL_A = "- **2026-07-20** — Acme raised a seed round — [yahoo.com](https://y/1)\n"
SIGNAL_B = "- **2026-07-25** — Acme launched a product — [prn.com](https://p/2)\n"


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("select_infographic_queue")


# ---- signal_key -----------------------------------------------------------


def test_signal_key_strips_also_reported_by(mod) -> None:
    base = "- **2026-07-20** — Acme raised a seed round — [yahoo.com](https://y/1)"
    with_corr = base + " (Also reported by: [prn.com](https://p/2), [foo.com](https://f/3))"
    assert mod.signal_key([base]) == mod.signal_key([with_corr])


def test_signal_key_ignores_sub_bullets(mod) -> None:
    a = ["- **2026-07-20** — headline — [s](https://s/1)", "  - Summary: one"]
    b = ["- **2026-07-20** — headline — [s](https://s/1)", "  - Summary: two"]
    assert mod.signal_key(a) == mod.signal_key(b)


# ---- signals_material_change ----------------------------------------------


def test_rotation_recent_to_older_not_material(mod) -> None:
    """normalize moved a signal from Recent to Older — same signal, no image change."""
    head = _brief(recent=SIGNAL_A, older="_none_\n")
    work = _brief(recent="_none_\n", older=SIGNAL_A)
    assert mod.signals_material_change(head, work) is False


def test_resort_within_recent_not_material(mod) -> None:
    head = _brief(recent=SIGNAL_A + SIGNAL_B)
    work = _brief(recent=SIGNAL_B + SIGNAL_A)  # reordered
    assert mod.signals_material_change(head, work) is False


def test_new_signal_in_recent_is_material(mod) -> None:
    head = _brief(recent=SIGNAL_A)
    work = _brief(recent=SIGNAL_B + SIGNAL_A)
    assert mod.signals_material_change(head, work) is True


def test_new_signal_in_older_is_material(mod) -> None:
    """A genuinely new event synthesized straight into Older still regenerates."""
    head = _brief(recent="_none_\n", older=SIGNAL_A)
    work = _brief(recent="_none_\n", older=SIGNAL_B + SIGNAL_A)
    assert mod.signals_material_change(head, work) is True


def test_also_reported_by_appended_not_material(mod) -> None:
    """vibefam case: extra source on an existing Older signal, plus a timestamp bump."""
    head = _brief(recent="_none_\n", older=SIGNAL_A, updated="2026-08-01 14:00 UTC")
    corr = SIGNAL_A.rstrip("\n") + " (Also reported by: [prn.com](https://p/9))\n"
    work = _brief(recent="_none_\n", older=corr, updated="2026-08-29 17:00 UTC")
    assert mod.signals_material_change(head, work) is False


def test_timestamp_only_bump_not_material(mod) -> None:
    head = _brief(recent=SIGNAL_A, updated="2026-08-01 14:00 UTC")
    work = _brief(recent=SIGNAL_A, updated="2026-08-29 17:00 UTC")
    assert mod.signals_material_change(head, work) is False


def test_new_brief_with_signal_is_material(mod) -> None:
    """fizzdragon case: first-run creation, no HEAD version."""
    work = _brief(recent="_none_\n", older=SIGNAL_A)
    assert mod.signals_material_change(None, work) is True


def test_new_brief_without_signal_not_material(mod) -> None:
    work = _brief(recent="_none_\n", older="_none_\n")
    assert mod.signals_material_change(None, work) is False


def test_hiring_tail_change_is_material(mod) -> None:
    head = _brief(recent=SIGNAL_A + "\n### Hiring\nHiring 2 roles.\n")
    work = _brief(recent=SIGNAL_A + "\n### Hiring\nHiring 5 roles now.\n")
    assert mod.signals_material_change(head, work) is True


def test_unparseable_fails_open_to_material(mod) -> None:
    # No signal headings at all → can't parse → regenerate rather than serve stale.
    assert mod.signals_material_change("# Broken brief\nno sections\n", "# still broken\n") is True


# ---- select() + main() integration ----------------------------------------


def _write(tmp_path: Path, slug: str, body: str, *, with_png: bool = True) -> None:
    d = tmp_path / "briefs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "LIVING_BRIEF.md").write_text(body, encoding="utf-8")
    if with_png:
        (d / "infographic.png").write_bytes(b"\x89PNG")


def test_select_partitions_changed_and_missing(mod, tmp_path, monkeypatch) -> None:
    # changed: has a new signal vs HEAD.
    _write(tmp_path, "changed-co", _brief(recent=SIGNAL_A + SIGNAL_B))
    # unchanged-but-missing-image: no new signal, but no infographic.png → backfill.
    _write(tmp_path, "missing-co", _brief(recent=SIGNAL_A), with_png=False)
    # unchanged with image: skipped entirely.
    _write(tmp_path, "quiet-co", _brief(recent=SIGNAL_A))

    heads = {
        "changed-co": _brief(recent=SIGNAL_A),          # HEAD lacks SIGNAL_B → material
        "missing-co": _brief(recent=SIGNAL_A),          # identical → not material
        "quiet-co": _brief(recent=SIGNAL_A),            # identical → not material
    }
    monkeypatch.setattr(mod, "git_head_brief", lambda rel: heads[Path(rel).parent.name])
    monkeypatch.setattr(mod, "git_last_commit_ts", lambda rel: 100)

    queue = mod.select(tmp_path / "briefs")
    assert queue["changed"] == ["changed-co"]
    assert queue["missing"] == ["missing-co"]


def test_select_missing_excludes_changed_and_sorts_oldest_first(mod, tmp_path, monkeypatch) -> None:
    # Two briefs, both changed AND lacking an image — must appear only in `changed`.
    _write(tmp_path, "aaa", _brief(recent=SIGNAL_A + SIGNAL_B), with_png=False)
    # Two pure-backfill briefs with distinct commit times → oldest first.
    _write(tmp_path, "old-miss", _brief(recent=SIGNAL_A), with_png=False)
    _write(tmp_path, "new-miss", _brief(recent=SIGNAL_A), with_png=False)

    heads = {
        "aaa": _brief(recent=SIGNAL_A),  # material
        "old-miss": _brief(recent=SIGNAL_A),  # not material
        "new-miss": _brief(recent=SIGNAL_A),  # not material
    }
    ts = {"aaa": 300, "old-miss": 100, "new-miss": 200}
    monkeypatch.setattr(mod, "git_head_brief", lambda rel: heads[Path(rel).parent.name])
    monkeypatch.setattr(mod, "git_last_commit_ts", lambda rel: ts[Path(rel).parent.name])

    queue = mod.select(tmp_path / "briefs")
    assert queue["changed"] == ["aaa"]
    assert queue["missing"] == ["old-miss", "new-miss"]  # 100 before 200; aaa excluded


def test_main_writes_queue_json(mod, tmp_path, monkeypatch) -> None:
    _write(tmp_path, "changed-co", _brief(recent=SIGNAL_A + SIGNAL_B))
    monkeypatch.setattr(mod, "git_head_brief", lambda rel: _brief(recent=SIGNAL_A))
    monkeypatch.setattr(mod, "git_last_commit_ts", lambda rel: 100)

    out = tmp_path / "data" / "infographic-queue.json"
    argv = sys.argv
    sys.argv = [
        "select_infographic_queue.py",
        "--briefs-dir",
        str(tmp_path / "briefs"),
        "--out",
        str(out),
    ]
    try:
        rc = mod.main()
    finally:
        sys.argv = argv

    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload == {"changed": ["changed-co"], "missing": []}
