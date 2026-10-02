"""The harness session prompts exist and name the file each session writes."""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPT_OUTPUTS = {
    "cluster.md": ["data/harness/cluster.json", "root_cause", "check_scope.py --session cluster"],
    "propose.md": ["data/harness/proposal.json", "data/harness/cluster.json", "check_scope.py --session propose"],
    "review.md": ["data/harness/review.json", "data/harness/replay-table.md", "check_scope.py --session review"],
}


@pytest.mark.parametrize("name", sorted(PROMPT_OUTPUTS))
def test_prompt_mentions_outputs(repo_root: Path, name: str):
    path = repo_root / "prompts" / "harness" / name
    assert path.exists(), f"missing {path}"
    text = path.read_text(encoding="utf-8")
    for needle in PROMPT_OUTPUTS[name]:
        assert needle in text, f"{name} does not mention {needle}"
