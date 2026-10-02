"""Unit tests for scripts/harness/check_scope.py against a throwaway git repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import check_scope

COMPANIES = [
    {"name": "Arch", "description": "robots"},
    {"name": "Nova", "description": "health"},
]


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    run_git(tmp_path, "init", "-q")
    run_git(tmp_path, "config", "user.email", "test@example.com")
    run_git(tmp_path, "config", "user.name", "test")
    run_git(tmp_path, "config", "commit.gpgsign", "false")
    for relative in (
        "prompts/ingest.md",
        "prompts/agent_supplement.md",
        "prompts/synthesis.md",
        "scripts/entity_terms.py",
        "scripts/collect-candidates.py",
        "tests/scripts/test_entity_terms.py",
        "docs/readme.md",
        "evals/scenarios/ingest/.gitkeep",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    write_companies(tmp_path, COMPANIES)
    (tmp_path / ".gitignore").write_text("data/harness/*\n")
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def write_companies(repo: Path, companies: list[dict]) -> None:
    path = repo / "data" / "companies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(companies, indent=2) + "\n")


def touch(repo: Path, relative: str, content: str = "changed\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def check(repo: Path, session: str, rung: int | None = None) -> int:
    argv = ["--session", session, "--repo-root", str(repo)]
    if rung is not None:
        argv += ["--rung", str(rung)]
    return check_scope.main(argv)


def test_clean_tree_passes_cluster_and_review(repo: Path):
    assert check(repo, "cluster") == 0
    assert check(repo, "review") == 0


def test_cluster_rejects_prompt_edit(repo: Path, capsys):
    touch(repo, "data/harness/cluster.json", "{}")
    touch(repo, "prompts/ingest.md")
    assert check(repo, "cluster") == 1
    assert "prompts/ingest.md" in capsys.readouterr().err


def test_write_scenarios_allows_only_cards(repo: Path, capsys):
    touch(repo, "evals/scenarios/ingest/ingest-2026-10-01-0001.json", "{}")
    assert check(repo, "write_scenarios") == 0
    touch(repo, "docs/readme.md")
    assert check(repo, "write_scenarios") == 1
    assert "docs/readme.md" in capsys.readouterr().err


def test_review_rejects_untracked_file(repo: Path, capsys):
    touch(repo, "notes.txt")
    assert check(repo, "review") == 1
    assert "notes.txt" in capsys.readouterr().err


def test_rung_one_one_company(repo: Path):
    changed = [dict(COMPANIES[0], exclude_terms=["stablecoin"]), COMPANIES[1]]
    write_companies(repo, changed)
    touch(repo, "evals/scenarios/ingest/ingest-2026-10-01-0001.json", "{}")
    assert check(repo, "propose", 1) == 0


def test_rung_one_two_companies_rejected(repo: Path, capsys):
    changed = [dict(COMPANIES[0], description="x"), dict(COMPANIES[1], description="y")]
    write_companies(repo, changed)
    assert check(repo, "propose", 1) == 1
    assert "Arch, Nova" in capsys.readouterr().err


def test_rung_one_added_company_counts(repo: Path, capsys):
    write_companies(repo, [*COMPANIES, {"name": "New", "description": "z"}, dict(COMPANIES[0], description="q")])
    assert check(repo, "propose", 1) == 1
    assert "New" in capsys.readouterr().err


def test_rung_one_rejects_prompt(repo: Path, capsys):
    write_companies(repo, [dict(COMPANIES[0], description="x"), COMPANIES[1]])
    touch(repo, "prompts/ingest.md")
    assert check(repo, "propose", 1) == 1
    assert "prompts/ingest.md" in capsys.readouterr().err


def test_rung_one_requires_companies_change(repo: Path):
    assert check(repo, "propose", 1) == 1


def test_rung_two_one_prompt(repo: Path):
    touch(repo, "prompts/ingest.md")
    touch(repo, "evals/scenarios/ingest/ingest-2026-10-01-0001.json", "{}")
    assert check(repo, "propose", 2) == 0


def test_rung_three_two_prompts_rejected(repo: Path, capsys):
    touch(repo, "prompts/ingest.md")
    touch(repo, "prompts/synthesis.md")
    assert check(repo, "propose", 3) == 1
    assert "only one of the layer prompts" in capsys.readouterr().err


def test_rung_two_rejects_companies(repo: Path):
    touch(repo, "prompts/ingest.md")
    write_companies(repo, [dict(COMPANIES[0], description="x"), COMPANIES[1]])
    assert check(repo, "propose", 2) == 1


def test_rung_two_rejects_other_prompt_file(repo: Path, capsys):
    touch(repo, "prompts/infographics.md")
    assert check(repo, "propose", 2) == 1
    assert "prompts/infographics.md" in capsys.readouterr().err


def test_rung_four_script_and_test(repo: Path):
    touch(repo, "scripts/entity_terms.py")
    touch(repo, "tests/scripts/test_entity_terms.py")
    assert check(repo, "propose", 4) == 0


def test_rung_four_requires_test(repo: Path, capsys):
    touch(repo, "scripts/entity_terms.py")
    assert check(repo, "propose", 4) == 1
    assert "exactly one test file" in capsys.readouterr().err


def test_rung_four_rejects_both_scripts(repo: Path):
    touch(repo, "scripts/entity_terms.py")
    touch(repo, "scripts/collect-candidates.py")
    touch(repo, "tests/scripts/test_entity_terms.py")
    assert check(repo, "propose", 4) == 1


def test_propose_without_rung_fails(repo: Path):
    touch(repo, "prompts/ingest.md")
    assert check(repo, "propose") == 1


def test_changed_companies_ignores_key_order():
    before = [{"name": "A", "x": 1, "y": 2}]
    after = [{"y": 2, "name": "A", "x": 1}]
    assert check_scope.changed_companies(before, after) == []
