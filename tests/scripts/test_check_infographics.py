from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def mod(scripts_module_loader):
    return scripts_module_loader("check_infographics")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    for slug in ("acme", "beta", "gamma"):
        brief_dir = tmp_path / "signals" / "briefs" / slug
        brief_dir.mkdir(parents=True)
        (brief_dir / "LIVING_BRIEF.md").write_text(f"# {slug}\n")
    (tmp_path / "signals" / "briefs" / "beta" / "infographic.png").write_bytes(b"old-beta")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    (tmp_path / "data").mkdir()
    return tmp_path


def _write_queue(repo: Path, changed: list[str], missing: list[str]) -> None:
    (repo / "data" / "infographic-queue.json").write_text(
        json.dumps({"changed": changed, "missing": missing})
    )


def _image(repo: Path, slug: str) -> Path:
    return repo / "signals" / "briefs" / slug / "infographic.png"


def test_missing_queue_file_is_ok(mod, repo: Path, capsys) -> None:
    assert mod.main(["--repo-root", str(repo)]) == 0
    assert "no queue" in capsys.readouterr().out


def test_empty_queue_is_ok(mod, repo: Path, capsys) -> None:
    _write_queue(repo, [], [])
    assert mod.main(["--repo-root", str(repo)]) == 0
    assert "queue empty" in capsys.readouterr().out


def test_alarm_when_queue_non_empty_and_nothing_produced(mod, repo: Path, capsys) -> None:
    _write_queue(repo, ["acme", "beta"], ["gamma"])
    assert mod.main(["--repo-root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "0/3" in out
    assert "ALARM" in out


def test_committed_image_untouched_does_not_count(mod, repo: Path) -> None:
    _write_queue(repo, ["beta"], [])
    assert mod.main(["--repo-root", str(repo)]) == 1


def test_new_untracked_image_counts(mod, repo: Path, capsys) -> None:
    _write_queue(repo, ["acme"], ["gamma"])
    _image(repo, "gamma").write_bytes(b"new-gamma")
    assert mod.main(["--repo-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "1/2" in out
    assert "missing: acme" in out


def test_modified_tracked_image_counts(mod, repo: Path, capsys) -> None:
    _write_queue(repo, ["beta"], [])
    _image(repo, "beta").write_bytes(b"new-beta")
    assert mod.main(["--repo-root", str(repo)]) == 0
    assert "1/1" in capsys.readouterr().out


def test_unqueued_image_change_does_not_count(mod, repo: Path) -> None:
    _write_queue(repo, ["acme"], [])
    _image(repo, "gamma").write_bytes(b"new-gamma")
    assert mod.main(["--repo-root", str(repo)]) == 1


def test_duplicate_slug_across_lists_counted_once(mod, tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps({"changed": ["acme"], "missing": ["acme", "beta"]}))
    assert mod.load_queue(queue_path) == ["acme", "beta"]


def test_summarize_partial_success_is_ok(mod) -> None:
    summary, exit_code = mod.summarize(["acme", "beta"], {"beta"})
    assert exit_code == 0
    assert summary.startswith("check_infographics: 1/2")
