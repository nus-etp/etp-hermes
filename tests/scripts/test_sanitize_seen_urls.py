"""Unit tests for scripts/sanitize-seen-urls.py."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def sanitize(scripts_module_loader):
    return scripts_module_loader("sanitize-seen-urls").sanitize


def test_strips_trailing_whitespace(sanitize, tmp_path: Path) -> None:
    p = tmp_path / "seen.txt"
    p.write_text("a \nb\tc \t\nd\n", encoding="utf-8")
    assert sanitize(p) is True
    assert p.read_text(encoding="utf-8") == "a\nb\tc\nd\n"


def test_preserves_final_newline_absence(sanitize, tmp_path: Path) -> None:
    p = tmp_path / "seen.txt"
    p.write_text("a \nb ", encoding="utf-8")
    assert sanitize(p) is True
    assert p.read_text(encoding="utf-8") == "a\nb"


def test_idempotent_on_clean_file(sanitize, tmp_path: Path) -> None:
    p = tmp_path / "seen.txt"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    assert sanitize(p) is False
    assert p.read_text(encoding="utf-8") == "a\nb\nc\n"


def test_missing_file_is_noop(sanitize, tmp_path: Path) -> None:
    assert sanitize(tmp_path / "does-not-exist.txt") is False


def test_realistic_mailto_key(sanitize, tmp_path: Path) -> None:
    """The exact failure that tripped test_seen_urls_format."""
    p = tmp_path / "seen.txt"
    p.write_text(
        "https://example.com/a\nmailto:contact@hicuramedical.com \n",
        encoding="utf-8",
    )
    assert sanitize(p) is True
    assert p.read_text(encoding="utf-8") == (
        "https://example.com/a\nmailto:contact@hicuramedical.com\n"
    )
