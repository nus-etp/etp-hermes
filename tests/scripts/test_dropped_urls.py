"""Unit tests for scripts/dropped_urls.py (the TTL'd drop list)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest


@pytest.fixture()
def du(scripts_module_loader):
    return scripts_module_loader("dropped_urls")


TODAY = dt.date(2026, 8, 31)


def _write(tmp_repo: Path, lines: list[str]) -> Path:
    p = tmp_repo / "signals" / "dropped-urls.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_parse_line_wellformed(du) -> None:
    assert du.parse_line("2026-08-01\thttps://x.example/a") == (
        dt.date(2026, 8, 1),
        "https://x.example/a",
    )
    # Keys may themselves be non-URLs (lever://, mailto:) and may contain tabs
    # only in the key half — partition on the first tab.
    assert du.parse_line("2026-08-01\tlever://acme/123")[1] == "lever://acme/123"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "https://x.example/no-date",  # legacy bare key, no tab
        "not-a-date\thttps://x.example/a",
        "2026-13-45\thttps://x.example/a",  # impossible date
        "2026-08-01\t",  # date but no key
        "# a comment line",
    ],
)
def test_parse_line_malformed_is_none(du, line: str) -> None:
    assert du.parse_line(line) is None


def test_load_active_expired_vs_live(du, tmp_repo: Path) -> None:
    path = _write(
        tmp_repo,
        [
            "# header comment",
            f"{TODAY.isoformat()}\thttps://x.example/today",
            "2026-08-10\thttps://x.example/recent",  # 21 days old → live
            "2026-07-01\thttps://x.example/old",  # 61 days old → expired
            "2026-08-01\thttps://x.example/edge",  # exactly 30 days → expired
        ],
    )
    active = du.load_active(path, ttl=30, today=TODAY)
    assert active == {"https://x.example/today", "https://x.example/recent"}


def test_load_active_malformed_lines_are_non_blocking(du, tmp_repo: Path) -> None:
    """Fail open toward recall: garbage must never block an item forever."""
    path = _write(
        tmp_repo,
        [
            "https://x.example/legacy-bare-key",
            "garbage\thttps://x.example/bad-date",
            "2026-08-20\thttps://x.example/good",
        ],
    )
    assert du.load_active(path, ttl=30, today=TODAY) == {"https://x.example/good"}


def test_load_active_missing_file(du, tmp_repo: Path) -> None:
    assert du.load_active(tmp_repo / "signals" / "nope.txt", ttl=30, today=TODAY) == set()


def test_is_expired_boundaries(du) -> None:
    assert not du.is_expired(TODAY, TODAY, 30)  # dropped today
    assert not du.is_expired(TODAY - dt.timedelta(days=29), TODAY, 30)
    assert du.is_expired(TODAY - dt.timedelta(days=30), TODAY, 30)
    # Future-dated (clock skew) blocks — the conservative side.
    assert not du.is_expired(TODAY + dt.timedelta(days=1), TODAY, 30)


def test_ttl_days_env_override(du, monkeypatch) -> None:
    monkeypatch.delenv("DROPPED_URL_TTL_DAYS", raising=False)
    assert du.ttl_days() == du.DEFAULT_TTL_DAYS
    monkeypatch.setenv("DROPPED_URL_TTL_DAYS", "7")
    assert du.ttl_days() == 7
    monkeypatch.setenv("DROPPED_URL_TTL_DAYS", "nonsense")
    assert du.ttl_days() == du.DEFAULT_TTL_DAYS
    monkeypatch.setenv("DROPPED_URL_TTL_DAYS", "-1")
    assert du.ttl_days() == du.DEFAULT_TTL_DAYS


def test_prune_file_keeps_comments_and_live_entries(du, tmp_repo: Path) -> None:
    path = _write(
        tmp_repo,
        [
            "# header comment",
            "2026-08-20\thttps://x.example/live",
            "2026-01-01\thttps://x.example/expired",
            "https://x.example/malformed",
        ],
    )
    removed = du.prune_file(path, ttl=30, today=TODAY)
    assert removed == 2
    assert path.read_text(encoding="utf-8") == (
        "# header comment\n2026-08-20\thttps://x.example/live\n"
    )


def test_prune_file_noop_when_nothing_expired(du, tmp_repo: Path) -> None:
    path = _write(tmp_repo, ["2026-08-20\thttps://x.example/live"])
    before = path.read_text(encoding="utf-8")
    assert du.prune_file(path, ttl=30, today=TODAY) == 0
    assert path.read_text(encoding="utf-8") == before


def test_prune_file_missing_is_noop(du, tmp_repo: Path) -> None:
    assert du.prune_file(tmp_repo / "signals" / "nope.txt", ttl=30, today=TODAY) == 0


def test_format_line_roundtrips(du) -> None:
    line = du.format_line("https://x.example/a", TODAY)
    assert line == f"{TODAY.isoformat()}\thttps://x.example/a"
    assert du.parse_line(line) == (TODAY, "https://x.example/a")
