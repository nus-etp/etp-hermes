#!/usr/bin/env python3
"""Trust streak for harness auto-merge — signals/harness/trust.json.

Lists merged `harness:auto` PRs (`gh pr list --state merged --label
harness:auto --json number,mergedAt,commits,reviews`), fetches each PR's
commits (`gh pr view N --json commits`) and calls a PR human-edited when any
commit after the workflow bot's last commit was authored by someone else (or
the bot never committed at all). The streak is the run of most-recent PRs
merged without a human edit; rung-1 (config-only) auto-merge unlocks at
`streak >= 10`. Writes `{auto_merge_rung1, streak, threshold, ...}` to
signals/harness/trust.json (committed; data/harness/ is gitignored).

    trust.py                       compute, write, print
    trust.py --enable-automerge N  also `gh pr merge --auto --squash N`, only
                                   when trusted and PR N's body says `Rung: 1`

Fail open: a gh failure yields streak 0 (auto-merge stays off), exit 0.
Pure stdlib; gh goes through an injectable runner.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRUST_PATH = REPO_ROOT / "signals" / "harness" / "trust.json"
THRESHOLD = 10
AUTO_LABEL = "harness:auto"
BOT_IDENTITIES = frozenset(
    {
        "github-actions[bot]",
        "github-actions",
        "41898282+github-actions[bot]@users.noreply.github.com",
    }
)
RUNG_RE = re.compile(r"^[\s>*_-]*Rung[*_]*\s*:[*_]*\s*`?(\d+)", re.IGNORECASE | re.MULTILINE)

Runner = Callable[..., subprocess.CompletedProcess]


class GhError(RuntimeError):
    pass


def default_runner(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def gh_json(runner: Runner, args: list[str]) -> object:
    result = runner(["gh", *args])
    if result.returncode != 0:
        raise GhError((result.stderr or "").strip() or f"gh {' '.join(args)} exited {result.returncode}")
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as error:
        raise GhError(f"gh {' '.join(args)} returned invalid JSON") from error


def list_merged_prs(runner: Runner, limit: int = 200) -> list[dict]:
    listed = gh_json(
        runner,
        [
            "pr", "list",
            "--state", "merged",
            "--label", AUTO_LABEL,
            "--limit", str(limit),
            "--json", "number,mergedAt,commits,reviews",
        ],
    )
    return [pr for pr in listed or [] if isinstance(pr, dict)]


def pr_commits(runner: Runner, pr: dict) -> list[dict]:
    try:
        viewed = gh_json(runner, ["pr", "view", str(pr["number"]), "--json", "commits"])
    except GhError:
        return list(pr.get("commits") or [])
    return list((viewed or {}).get("commits") or [])


def is_bot_author(author: dict) -> bool:
    identities = {str(author.get(key) or "") for key in ("login", "name", "email")}
    return bool(identities & BOT_IDENTITIES)


def is_bot_commit(commit: dict) -> bool:
    authors = commit.get("authors") or []
    return bool(authors) and all(is_bot_author(author) for author in authors)


def human_edited(commits: list[dict]) -> bool:
    bot_positions = [index for index, commit in enumerate(commits) if is_bot_commit(commit)]
    if not bot_positions:
        return True
    return bot_positions[-1] < len(commits) - 1


def merged_order(prs: list[dict]) -> list[dict]:
    return sorted(prs, key=lambda pr: str(pr.get("mergedAt") or ""), reverse=True)


def compute_trust(runner: Runner, threshold: int = THRESHOLD) -> dict:
    trust = {"auto_merge_rung1": False, "streak": 0, "threshold": threshold, "last_human_edit_pr": None, "error": None}
    try:
        prs = merged_order(list_merged_prs(runner))
    except GhError as error:
        trust["error"] = str(error)
        return trust
    streak = 0
    for pr in prs:
        if human_edited(pr_commits(runner, pr)):
            trust["last_human_edit_pr"] = pr.get("number")
            break
        streak += 1
    trust["streak"] = streak
    trust["auto_merge_rung1"] = streak >= threshold
    return trust


def write_trust(trust: dict, path: Path = TRUST_PATH, now: dt.datetime | None = None) -> None:
    stamp = (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0)
    payload = {key: value for key, value in trust.items() if value is not None}
    payload["computed_at"] = stamp.isoformat().replace("+00:00", "Z")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def declared_rung(body: str) -> int | None:
    match = RUNG_RE.search(body or "")
    return int(match.group(1)) if match else None


def enable_automerge(runner: Runner, trust: dict, pr_number: int) -> str:
    if not trust.get("auto_merge_rung1"):
        return f"auto-merge not enabled for #{pr_number}: streak {trust.get('streak')} < {trust.get('threshold')}"
    try:
        viewed = gh_json(runner, ["pr", "view", str(pr_number), "--json", "body"])
    except GhError as error:
        return f"auto-merge not enabled for #{pr_number}: {error}"
    rung = declared_rung((viewed or {}).get("body") or "")
    if rung != 1:
        return f"auto-merge not enabled for #{pr_number}: PR declares rung {rung}, only rung 1 is eligible"
    result = runner(["gh", "pr", "merge", "--auto", "--squash", str(pr_number)])
    if result.returncode != 0:
        return f"auto-merge request for #{pr_number} failed: {(result.stderr or '').strip()}"
    return f"auto-merge enabled for #{pr_number} (rung 1, streak {trust['streak']})"


def run(runner: Runner, trust_path: Path, automerge_pr: int | None = None) -> dict:
    trust = compute_trust(runner)
    write_trust(trust, trust_path)
    print(json.dumps({key: trust[key] for key in ("auto_merge_rung1", "streak", "threshold")}))
    if trust.get("error"):
        print(f"trust: gh failed, treating streak as 0: {trust['error']}", file=sys.stderr)
    if automerge_pr is not None:
        print(f"trust: {enable_automerge(runner, trust, automerge_pr)}")
    return trust


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(TRUST_PATH))
    parser.add_argument("--enable-automerge", type=int, default=None, metavar="PR")
    return parser


def main(argv: list[str] | None = None, runner: Runner = default_runner) -> int:
    args = build_parser().parse_args(argv)
    run(runner, Path(args.out), args.enable_automerge)
    return 0


if __name__ == "__main__":
    sys.exit(main())
