#!/usr/bin/env python3
"""A/B arm registry — single source of truth for the experiment's arms.

The experiment is **champion vs challenger**: one production baseline (the
champion) and one or more challenger arms, each A/B'd against the champion
*pairwise*. The pairing is deliberate, not a free-for-all N-way diff: the
significance test (McNemar/sign) scores *discordant pairs*, which only exist
between two arms. With three challengers you run three independent
champion-vs-challenger tests, never one three-way test.

Every scoring script (``ab_compare`` / ``ab_stats`` / ``ab_backfill``) reads its
arm set from here, so adding a challenger is: append its name below, create its
prompts (``prompts/<name>/ingest.md``) and isolated signal state
(``signals/<name>/``, seeded seen-urls), wire its workflow steps — the scoring
adapts automatically (see the ab-test skill).

Conventions every arm follows:
- champion signal state lives at ``signals/`` (no subdir); challenger ``<name>``
  at ``signals/<name>/``.
- champion ingest prompt is ``prompts/ingest.md``; challenger ``<name>`` is
  ``prompts/<name>/ingest.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

CHAMPION = "v1"
DEFAULT_CHALLENGERS = ("v2",)


def challengers() -> list[str]:
    """Challenger arm names. ``AB_CHALLENGERS=v2,v3`` overrides for ad-hoc runs."""
    env = os.environ.get("AB_CHALLENGERS", "").strip()
    if env:
        return [a.strip() for a in env.split(",") if a.strip()]
    return list(DEFAULT_CHALLENGERS)


def signal_root(repo: Path, arm: str) -> Path:
    """Directory holding ``updates/`` and ``agent/`` for ``arm``."""
    return repo / "signals" if arm == CHAMPION else repo / "signals" / arm


def ingest_prompt_path(repo: Path, arm: str) -> Path:
    """Ingest prompt file for ``arm``."""
    if arm == CHAMPION:
        return repo / "prompts" / "ingest.md"
    return repo / "prompts" / arm / "ingest.md"
