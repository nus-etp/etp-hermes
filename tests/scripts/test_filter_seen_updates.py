"""Unit tests for scripts/filter_seen_updates.py."""

from __future__ import annotations

from pathlib import Path


def _write_updates(tmp_repo: Path, date: str, body: str) -> Path:
    p = tmp_repo / "signals" / "updates" / f"{date}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _write_snapshot(tmp_repo: Path, urls: list[str]) -> Path:
    p = tmp_repo / "data" / "seen-urls-prerun.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(urls) + ("\n" if urls else ""))
    return p


def test_removes_seen_item_keeps_unseen(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## NEU Battery Materials\n"
        "- **Europe's New LFP Storage Plants** — NEU · newsroom · 2026-07-16\n"
        "  https://neu.example/lfp\n"
        "- **Why China's Lithium Futures Matter** — NEU · newsroom · 2026-07-16\n"
        "  https://neu.example/lithium\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://neu.example/lfp"]))
    removed = mod.process_file(target, seen)

    assert removed == 1
    out = target.read_text()
    assert "Europe's New LFP" not in out
    assert "Why China's Lithium" in out
    assert "## NEU Battery Materials" in out


def test_drops_emptied_company_heading(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme raises Series B** — Acme · news · 2026-07-16\n"
        "  https://acme.example/seed\n"
        "\n"
        "## Beta\n"
        "- **Beta ships product** — Beta · news · 2026-07-16\n"
        "  https://beta.example/ship\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://acme.example/seed"]))
    removed = mod.process_file(target, seen)

    assert removed == 1
    out = target.read_text()
    assert "## Acme" not in out
    assert "## Beta" in out
    assert "Beta ships product" in out


def test_deletes_fully_emptied_file(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme only item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/only\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://acme.example/only"]))
    removed = mod.process_file(target, seen)

    assert removed == 1
    assert not target.exists()


def test_noop_when_snapshot_missing(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/x\n",
    )
    # Exercise via main() with args so we cover the fail-open branch.
    import sys

    argv = sys.argv
    sys.argv = [
        "filter_seen_updates.py",
        "--repo-root",
        str(tmp_repo),
        "--date",
        "2026-07-16",
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = argv
    # File untouched (no snapshot → fail open).
    assert (tmp_repo / "signals" / "updates" / "2026-07-16.md").exists()


def test_noop_when_updates_missing(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    _write_snapshot(tmp_repo, ["https://acme.example/x"])
    import sys

    argv = sys.argv
    sys.argv = [
        "filter_seen_updates.py",
        "--repo-root",
        str(tmp_repo),
        "--date",
        "2026-07-16",
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = argv


def test_preserves_run_at_dividers(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Run at 13:00 UTC\n"
        "\n"
        "## Acme\n"
        "- **Acme seen item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/seen\n"
        "- **Acme fresh item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/fresh\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://acme.example/seen"]))
    removed = mod.process_file(target, seen)

    assert removed == 1
    out = target.read_text()
    assert "## Run at 13:00 UTC" in out
    assert "Acme seen item" not in out
    assert "Acme fresh item" in out


def test_preserves_multiline_items(scripts_module_loader, tmp_repo: Path) -> None:
    """A multi-line item (headline + link + extra indented detail) is removed whole."""
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme seen item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/seen\n"
        "  extra context line about the seen item\n"
        "- **Acme fresh item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/fresh\n"
        "  extra context line about the fresh item\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://acme.example/seen"]))
    removed = mod.process_file(target, seen)

    assert removed == 1
    out = target.read_text()
    assert "extra context line about the seen item" not in out
    assert "Acme fresh item" in out
    assert "extra context line about the fresh item" in out
    assert "https://acme.example/fresh" in out


def test_no_removal_when_nothing_seen(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme fresh item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/fresh\n",
    )
    seen = mod.load_seen(_write_snapshot(tmp_repo, ["https://other.example/x"]))
    assert mod.process_file(target, seen) == 0
    assert "Acme fresh item" in target.read_text()


def _write_dropped_snapshot(tmp_repo: Path, lines: list[str]) -> Path:
    p = tmp_repo / "data" / "dropped-urls-prerun.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + ("\n" if lines else ""))
    return p


def test_non_expired_drop_counts_as_seen(scripts_module_loader, tmp_repo: Path) -> None:
    """A key dropped a few days ago still blocks; an expired one does not."""
    import datetime as dt

    mod = scripts_module_loader("filter_seen_updates")
    recent = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=3)).isoformat()
    target = _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Acme dropped item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/dropped\n"
        "- **Acme lapsed item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/lapsed\n",
    )
    snapshot = _write_dropped_snapshot(
        tmp_repo,
        [
            "# header comment",
            f"{recent}\thttps://acme.example/dropped",
            "2020-01-01\thttps://acme.example/lapsed",  # long expired → not blocking
        ],
    )
    removed = mod.process_file(target, mod.load_dropped(snapshot))

    assert removed == 1
    out = target.read_text()
    assert "Acme dropped item" not in out
    assert "Acme lapsed item" in out


def test_malformed_dropped_lines_are_non_blocking(
    scripts_module_loader, tmp_repo: Path
) -> None:
    mod = scripts_module_loader("filter_seen_updates")
    snapshot = _write_dropped_snapshot(
        tmp_repo,
        ["https://acme.example/bare-key", "junk\thttps://acme.example/bad-date"],
    )
    assert mod.load_dropped(snapshot) == set()


def test_main_unions_both_snapshots(scripts_module_loader, tmp_repo: Path) -> None:
    import datetime as dt
    import sys

    mod = scripts_module_loader("filter_seen_updates")
    recent = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    _write_updates(
        tmp_repo,
        "2026-07-16",
        "# Daily Updates — 2026-07-16\n"
        "\n"
        "## Acme\n"
        "- **Seen item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/seen\n"
        "- **Dropped item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/dropped\n"
        "- **Fresh item** — Acme · news · 2026-07-16\n"
        "  https://acme.example/fresh\n",
    )
    _write_snapshot(tmp_repo, ["https://acme.example/seen"])
    _write_dropped_snapshot(tmp_repo, [f"{recent}\thttps://acme.example/dropped"])

    argv = sys.argv
    sys.argv = [
        "filter_seen_updates.py",
        "--repo-root",
        str(tmp_repo),
        "--date",
        "2026-07-16",
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = argv

    out = (tmp_repo / "signals" / "updates" / "2026-07-16.md").read_text()
    assert "Seen item" not in out
    assert "Dropped item" not in out
    assert "Fresh item" in out
