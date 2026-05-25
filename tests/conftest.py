"""Shared fixtures for the etp-hermes test suites."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    return repo_root / "data"


@pytest.fixture(scope="session")
def signals_dir(repo_root: Path) -> Path:
    return repo_root / "signals"


@pytest.fixture(scope="session")
def companies(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "companies.json").read_text())


@pytest.fixture(scope="session")
def feeds(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "feeds.json").read_text())


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "signals").mkdir()
    (tmp_path / "signals" / "updates").mkdir()
    (tmp_path / "signals" / "agent").mkdir()
    return tmp_path


@pytest.fixture(scope="session")
def scripts_module_loader():
    """Load a `scripts/<stem>.py` as a module. Stems may contain dashes."""

    import importlib.util
    import sys

    def _load(stem: str):
        path = REPO_ROOT / "scripts" / f"{stem}.py"
        mod_name = f"etp_hermes_scripts.{stem.replace('-', '_')}"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        assert spec and spec.loader, f"could not load {path}"
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    return _load
