"""Integration eval: drive hermes-agent's batch_runner.py against a tiny fixture.

Reference: https://hermes-agent.nousresearch.com/docs/user-guide/features/batch-processing
Opt-in via HERMES_BATCH_EVAL=1; also requires a local hermes install at
~/.hermes/hermes-agent/batch_runner.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


BATCH_RUNNER = Path.home() / ".hermes" / "hermes-agent" / "batch_runner.py"


def _skip_unless_opted_in() -> None:
    if os.environ.get("HERMES_BATCH_EVAL") != "1":
        pytest.skip("HERMES_BATCH_EVAL!=1; opt-in required for the batch integration eval")
    if not BATCH_RUNNER.exists():
        pytest.skip(f"batch_runner.py not found at {BATCH_RUNNER}")


@pytest.mark.llm
def test_batch_runner_produces_completed_trajectory(
    deepseek_api_key: str, tmp_path: Path
) -> None:
    _skip_unless_opted_in()

    prompt = (
        "You are running a tiny smoke test of the firehose-triage step in "
        "prompts/ingest.md. Given the following two headlines, return a JSON "
        "object that maps each id to either the company name or null. "
        "Watchlist: Carousell (Singapore consumer marketplace), Patsnap (IP SaaS).\n"
        "Headlines:\n"
        "h1: Carousell raises $100M Series E (techcrunch.com)\n"
        "h2: Top 10 SaaS firms in APAC for 2026 (techinasia.com)\n"
        "Respond with the JSON object only."
    )

    dataset = tmp_path / "prompts.jsonl"
    dataset.write_text(json.dumps({"prompt": prompt}) + "\n")

    run_name = "etp-hermes-eval-batch"
    cmd = [
        sys.executable,
        str(BATCH_RUNNER),
        f"--dataset_file={dataset}",
        "--batch_size=1",
        f"--run_name={run_name}",
        "--num_workers=1",
        "--max_samples=1",
    ]
    # batch_runner writes under <cwd>/data/<run_name>/.
    proc = subprocess.run(
        cmd,
        cwd=tmp_path,
        env={**os.environ, "DEEPSEEK_API_KEY": deepseek_api_key},
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"batch_runner.py exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    trajectories = tmp_path / "data" / run_name / "trajectories.jsonl"
    assert trajectories.exists(), (
        f"expected trajectories.jsonl at {trajectories}; got dir contents: "
        f"{list((tmp_path / 'data' / run_name).glob('*'))}"
    )
    rows = [json.loads(line) for line in trajectories.read_text().splitlines() if line.strip()]
    assert rows, f"trajectories.jsonl was empty\nstdout:\n{proc.stdout}"
    assert any(r.get("completed") for r in rows), f"no completed trajectory in output:\n{rows}"

    shutil.rmtree(tmp_path / "data" / run_name, ignore_errors=True)
