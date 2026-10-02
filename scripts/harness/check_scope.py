#!/usr/bin/env python3
"""Enforce the per-session write scope of the harness agent sessions.

Each `hermes -z` session in `harness-optimise.yml` may only write a fixed set
of paths (docs/self-optimising-harness.md §5, rule D). This check compares the
working tree against HEAD — `git diff --name-only HEAD` plus untracked files
from `git status --porcelain` — and exits 1 listing every path outside the
session's allowance:

- write_scenarios: `evals/scenarios/**`
- cluster: `data/harness/cluster.json`
- review: `data/harness/review.json`
- propose --rung 1: `data/companies.json` (at most one company changed) + `evals/scenarios/**`
- propose --rung 2|3: exactly one of `prompts/{ingest,agent_supplement,synthesis}.md` + `evals/scenarios/**`
- propose --rung 4: exactly one of `scripts/entity_terms.py`, `scripts/collect-candidates.py`,
  exactly one test file under `tests/scripts/`, + `evals/scenarios/**`

Fail-loud by design: the workflow does not mark this step continue-on-error.
Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SESSIONS = ("write_scenarios", "cluster", "propose", "review")
RUNGS = (1, 2, 3, 4)
SCENARIOS_PREFIX = "evals/scenarios/"
COMPANIES_PATH = "data/companies.json"
LAYER_PROMPTS = ("prompts/ingest.md", "prompts/agent_supplement.md", "prompts/synthesis.md")
RULE_SCRIPTS = ("scripts/entity_terms.py", "scripts/collect-candidates.py")
SESSION_FILES = {
    "cluster": ("data/harness/cluster.json",),
    "review": ("data/harness/review.json",),
    "write_scenarios": (),
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def changed_paths(repo: Path) -> list[str]:
    tracked = git(repo, "diff", "--name-only", "HEAD").splitlines()
    untracked = [
        line[3:]
        for line in git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
        if line.startswith("?? ")
    ]
    return sorted({path.strip().strip('"') for path in tracked + untracked if path.strip()})


def is_scenario(path: str) -> bool:
    return path.startswith(SCENARIOS_PREFIX)


def is_script_test(path: str) -> bool:
    if not path.startswith("tests/scripts/") or not path.endswith(".py"):
        return False
    return "/" not in path[len("tests/scripts/"):]


def check_fixed_files(paths: list[str], allowed_files: tuple[str, ...], allow_scenarios: bool) -> list[str]:
    return [
        path
        for path in paths
        if path not in allowed_files and not (allow_scenarios and is_scenario(path))
    ]


def require_exactly_one(paths: list[str], choices: tuple[str, ...], label: str) -> list[str]:
    touched = [path for path in paths if path in choices]
    if len(touched) == 1:
        return []
    if not touched:
        return [f"<missing: exactly one of {label} must change>"]
    return [f"{path} (only one of {label} may change)" for path in touched]


def load_companies_at_head(repo: Path) -> list[dict]:
    try:
        return json.loads(git(repo, "show", f"HEAD:{COMPANIES_PATH}"))
    except subprocess.CalledProcessError:
        return []


def load_companies_in_tree(repo: Path) -> list[dict]:
    path = repo / COMPANIES_PATH
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def index_by_name(companies: list[dict]) -> dict[str, dict]:
    return {str(entry.get("name")): entry for entry in companies if isinstance(entry, dict)}


def changed_companies(before: list[dict], after: list[dict]) -> list[str]:
    old = index_by_name(before)
    new = index_by_name(after)
    return sorted(name for name in set(old) | set(new) if old.get(name) != new.get(name))


def check_rung_one(repo: Path, paths: list[str]) -> list[str]:
    problems = check_fixed_files(paths, (COMPANIES_PATH,), allow_scenarios=True)
    if COMPANIES_PATH not in paths:
        return problems + [f"<missing: {COMPANIES_PATH} must change>"]
    try:
        names = changed_companies(load_companies_at_head(repo), load_companies_in_tree(repo))
    except json.JSONDecodeError as error:
        return problems + [f"{COMPANIES_PATH} (invalid JSON: {error})"]
    if len(names) > 1:
        problems.append(f"{COMPANIES_PATH} (at most one company may change; changed: {', '.join(names)})")
    return problems


def check_prompt_rung(paths: list[str]) -> list[str]:
    problems = check_fixed_files(paths, LAYER_PROMPTS, allow_scenarios=True)
    return problems + require_exactly_one(paths, LAYER_PROMPTS, "the layer prompts")


def check_script_rung(paths: list[str]) -> list[str]:
    problems = [
        path
        for path in paths
        if path not in RULE_SCRIPTS and not is_scenario(path) and not is_script_test(path)
    ]
    problems += require_exactly_one(paths, RULE_SCRIPTS, "the rule scripts")
    tests = tuple(path for path in paths if is_script_test(path))
    if len(tests) != 1:
        problems.append(f"<exactly one test file under tests/scripts/ must change; found {len(tests)}>")
    return problems


def scope_problems(repo: Path, session: str, rung: int | None, paths: list[str]) -> list[str]:
    if session == "write_scenarios":
        return check_fixed_files(paths, (), allow_scenarios=True)
    if session in ("cluster", "review"):
        return check_fixed_files(paths, SESSION_FILES[session], allow_scenarios=False)
    if rung == 1:
        return check_rung_one(repo, paths)
    if rung in (2, 3):
        return check_prompt_rung(paths)
    if rung == 4:
        return check_script_rung(paths)
    return [f"<propose needs --rung 1-4, got {rung}>"]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", required=True, choices=SESSIONS)
    parser.add_argument("--rung", type=int, choices=RUNGS)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo_root.resolve()
    paths = changed_paths(repo)
    problems = scope_problems(repo, args.session, args.rung, paths)
    if problems:
        rung_label = f" rung {args.rung}" if args.rung else ""
        print(f"scope violation for session {args.session}{rung_label}:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"scope ok for session {args.session}: {len(paths)} changed path(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
