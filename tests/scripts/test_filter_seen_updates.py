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
