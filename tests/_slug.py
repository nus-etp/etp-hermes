"""Slug derivation, mirrors the rule in prompts/synthesis.md."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return _NON_ALNUM.sub("-", name.lower()).strip("-")
