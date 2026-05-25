"""Gate the LLM-behaviour eval suite behind DEEPSEEK_API_KEY."""

from __future__ import annotations

import os

import pytest

if not os.environ.get("DEEPSEEK_API_KEY"):
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(scope="session")
def deepseek_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        pytest.skip("DEEPSEEK_API_KEY not set; LLM evals skipped")
    assert key
    return key
