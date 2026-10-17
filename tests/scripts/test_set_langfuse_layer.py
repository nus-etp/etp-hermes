"""Tests for scripts/set-langfuse-layer.sh (runs the shell script against a temp HERMES_HOME)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "set-langfuse-layer.sh"


def _run(tmp_path: Path, layer: str, shell_env: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "HERMES_LANGFUSE_ENV"}
    env["HERMES_HOME"] = str(tmp_path)
    if shell_env is not None:
        env["HERMES_LANGFUSE_ENV"] = shell_env
    return subprocess.run(["bash", str(SCRIPT), layer], env=env, capture_output=True, text=True)


def _env_value(tmp_path: Path) -> str | None:
    lines = [l for l in (tmp_path / ".env").read_text().splitlines() if l.startswith("HERMES_LANGFUSE_ENV=")]
    assert len(lines) <= 1
    return lines[0].split("=", 1)[1] if lines else None


def test_appends_layer_suffix_to_base_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("NVIDIA_API_KEY=x\nHERMES_LANGFUSE_ENV=production\n")
    result = _run(tmp_path, "ingest")
    assert result.returncode == 0, result.stderr
    assert _env_value(tmp_path) == "production-ingest"
    assert "NVIDIA_API_KEY=x" in (tmp_path / ".env").read_text()


def test_replaces_previous_layer_suffix(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("HERMES_LANGFUSE_ENV=production-ingest\n")
    assert _run(tmp_path, "synthesis").returncode == 0
    assert _env_value(tmp_path) == "production-synthesis"
    assert _run(tmp_path, "infographics").returncode == 0
    assert _env_value(tmp_path) == "production-infographics"


def test_falls_back_to_shell_env_when_file_lacks_it(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OTHER=1\n")
    assert _run(tmp_path, "agent", shell_env="harness").returncode == 0
    assert _env_value(tmp_path) == "harness-agent"


def test_noop_without_any_base_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OTHER=1\n")
    result = _run(tmp_path, "agent")
    assert result.returncode == 0
    assert _env_value(tmp_path) is None
    assert "untagged" in result.stderr


def test_rejects_unknown_layer(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("HERMES_LANGFUSE_ENV=production\n")
    assert _run(tmp_path, "bogus").returncode == 2
    assert _env_value(tmp_path) == "production"
