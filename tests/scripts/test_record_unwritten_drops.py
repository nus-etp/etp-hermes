"""Unit tests for scripts/record_unwritten_drops.py."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

DAY = dt.date(2026, 10, 31)


def _candidates(tmp_repo: Path, items: list[dict]) -> Path:
    path = tmp_repo / "data" / "candidates.json"
    path.write_text(json.dumps({"candidates": items}))
    return path


def _item(key: str, link: str | None = None) -> dict:
    return {"company": "Acme", "dedup_key": key, "link": link or key}


def _paths(tmp_repo: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_repo / "signals" / "updates" / f"{DAY.isoformat()}.md",
        tmp_repo / "signals" / "seen-urls.txt",
        tmp_repo / "signals" / "dropped-urls.txt",
    )


def test_appends_only_unrecorded_keys(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)
    seen.write_text("https://a.example/kept\n")
    dropped.write_text("2026-10-31\thttps://a.example/dropped\n")
    updates.write_text(
        "# Daily Updates — 2026-10-31\n\n## Acme\n"
        "- **Kept but not in seen** — Acme · news\n  https://a.example/in-updates\n"
    )
    candidates = _candidates(
        tmp_repo,
        [
            _item("https://a.example/kept"),
            _item("https://a.example/dropped"),
            _item("https://a.example/in-updates"),
            _item("https://a.example/silent"),
            _item("https://a.example/silent"),
            _item("tag:github.com,2008:9", "https://github.com/acme/repo"),
        ],
    )

    count = mod.record(candidates, updates, seen, dropped, DAY)

    assert count == 2
    lines = dropped.read_text().splitlines()
    assert lines == [
        "2026-10-31\thttps://a.example/dropped",
        "2026-10-31\thttps://a.example/silent",
        "2026-10-31\ttag:github.com,2008:9",
    ]


def test_link_in_updates_counts_as_kept_for_non_url_key(
    scripts_module_loader, tmp_repo: Path
) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)
    updates.write_text("- **Repo** — Acme · github\n  https://github.com/acme/repo\n")
    candidates = _candidates(
        tmp_repo, [_item("tag:github.com,2008:9", "https://github.com/acme/repo")]
    )

    assert mod.record(candidates, updates, seen, dropped, DAY) == 0
    assert not dropped.exists()


def test_expired_drop_is_rerecorded(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)
    dropped.write_text("2026-08-01\thttps://a.example/old")
    candidates = _candidates(tmp_repo, [_item("https://a.example/old")])

    assert mod.record(candidates, updates, seen, dropped, DAY) == 1
    assert dropped.read_text().splitlines()[-1] == "2026-10-31\thttps://a.example/old"
    assert dropped.read_text().splitlines()[0] == "2026-08-01\thttps://a.example/old"


def test_missing_candidates_is_noop(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)

    result = mod.record(tmp_repo / "data" / "candidates.json", updates, seen, dropped, DAY)

    assert result is None
    assert not dropped.exists()


def test_skips_when_layer1_left_no_trace(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)
    seen.write_text("https://a.example/kept\n")
    seen_prerun = tmp_repo / "data" / "seen-urls-prerun.txt"
    seen_prerun.write_text("https://a.example/kept\n")
    dropped_prerun = tmp_repo / "data" / "dropped-urls-prerun.txt"
    candidates = _candidates(tmp_repo, [_item("https://a.example/silent")])

    result = mod.record(
        candidates, updates, seen, dropped, DAY, seen_prerun, dropped_prerun
    )

    assert result is None
    assert not dropped.exists()


def test_runs_when_layer1_recorded_a_drop(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    updates, seen, dropped = _paths(tmp_repo)
    dropped.write_text("2026-10-31\thttps://a.example/judged\n")
    dropped_prerun = tmp_repo / "data" / "dropped-urls-prerun.txt"
    dropped_prerun.write_text("")
    candidates = _candidates(
        tmp_repo, [_item("https://a.example/judged"), _item("https://a.example/silent")]
    )

    result = mod.record(candidates, updates, seen, dropped, DAY, None, dropped_prerun)

    assert result == 1


def test_main_cli_fails_open(scripts_module_loader, tmp_repo: Path, monkeypatch) -> None:
    mod = scripts_module_loader("record_unwritten_drops")
    monkeypatch.setattr(
        "sys.argv",
        ["record_unwritten_drops.py", "--repo-root", str(tmp_repo), "--date", "2026-10-31"],
    )
    assert mod.main() == 0
