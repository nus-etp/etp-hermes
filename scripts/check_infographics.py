#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE_RELATIVE_PATH = Path("data") / "infographic-queue.json"
BRIEFS_RELATIVE_PATH = Path("signals") / "briefs"
IMAGE_NAME = "infographic.png"


def load_queue(queue_path: Path) -> list[str] | None:
    try:
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    slugs: list[str] = []
    for key in ("changed", "missing"):
        for slug in payload.get(key) or []:
            if slug not in slugs:
                slugs.append(slug)
    return slugs


def changed_images(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uall", "--", BRIEFS_RELATIVE_PATH.as_posix()],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    slugs: set[str] = set()
    for line in result.stdout.splitlines():
        path = Path(line[3:].strip().strip('"'))
        if path.name == IMAGE_NAME and path.parent.parent == BRIEFS_RELATIVE_PATH:
            slugs.add(path.parent.name)
    return slugs


def summarize(queued: list[str], produced: set[str]) -> tuple[str, int]:
    made = [slug for slug in queued if slug in produced]
    absent = [slug for slug in queued if slug not in produced]
    summary = f"check_infographics: {len(made)}/{len(queued)} queued infographics produced"
    if absent:
        summary += f"; missing: {', '.join(absent)}"
    if queued and not made:
        return f"{summary} — ALARM: Layer 4 produced nothing (image_generate failing?)", 1
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when Layer 4 had a non-empty infographic queue but wrote no infographic.png."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--queue", type=Path, default=None)
    args = parser.parse_args(argv)

    queue_path = args.queue or args.repo_root / QUEUE_RELATIVE_PATH
    queued = load_queue(queue_path)
    if queued is None:
        print(f"check_infographics: no queue at {queue_path}; nothing to check")
        return 0
    if not queued:
        print("check_infographics: queue empty; nothing to check")
        return 0

    summary, exit_code = summarize(queued, changed_images(args.repo_root))
    print(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
