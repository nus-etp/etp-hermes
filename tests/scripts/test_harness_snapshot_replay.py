"""Unit tests for scripts/harness/snapshot_replay.py."""

from __future__ import annotations

from pathlib import Path

from harness import snapshot_replay


def test_snapshot_copies_candidates(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "candidates.json").write_text('{"candidates": []}', encoding="utf-8")
    assert snapshot_replay.main(["--repo-root", str(tmp_path), "--date", "2026-10-03"]) == 0
    assert (tmp_path / "data" / "replay" / "2026-10-03.json").read_text(encoding="utf-8") == '{"candidates": []}'


def test_snapshot_fails_open_without_candidates(tmp_path: Path) -> None:
    assert snapshot_replay.main(["--repo-root", str(tmp_path), "--date", "2026-10-03"]) == 0
    assert not (tmp_path / "data" / "replay").exists()


def test_prune_keeps_newest_dated_snapshots(tmp_path: Path) -> None:
    replay = tmp_path / "data" / "replay"
    replay.mkdir(parents=True)
    for day in range(1, 6):
        (replay / f"2026-09-0{day}.json").write_text("{}", encoding="utf-8")
    (replay / "README.txt").write_text("keep me", encoding="utf-8")
    (tmp_path / "data" / "candidates.json").write_text("{}", encoding="utf-8")
    assert snapshot_replay.main(["--repo-root", str(tmp_path), "--date", "2026-09-06", "--keep", "3"]) == 0
    assert sorted(p.name for p in replay.iterdir()) == [
        "2026-09-04.json",
        "2026-09-05.json",
        "2026-09-06.json",
        "README.txt",
    ]
