#!/usr/bin/env python3
"""Blind relevance labeler for the A/B significance test.

``ab_compare.py`` records every item the two arms *disagreed* on into
``signals/ab/disagreements.jsonl`` (one arm kept it, the other dropped it),
with ``label: null``. This script fills those labels: for each unlabeled row it
asks a neutral LLM judge — *blind to which arm kept the item* — whether the
item belongs in the digest (``keep``) or is noise (``drop``). That verdict is
the ground truth ``ab_stats.py`` scores the arms against.

Why blind: the judge must rule on the item's own merits, not rationalize
whichever arm's decision it's shown. The prompt never says which arm kept it.

Fail-open: no ``DEEPSEEK_API_KEY`` or any per-row error leaves that row's label
null (it gets retried next run) and exits 0, so the experiment never blocks the
daily sync. Idempotent — only null-label rows are sent to the model.

Pure stdlib (uses ``scripts/ab_llm.py``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_ab_llm():
    if "ab_llm" in sys.modules:
        return sys.modules["ab_llm"]
    spec = importlib.util.spec_from_file_location("ab_llm", Path(__file__).resolve().parent / "ab_llm.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab_llm"] = mod
    spec.loader.exec_module(mod)
    return mod


JUDGE_SYSTEM = (
    "You are the editorial filter for a private-market intelligence digest that "
    "tracks specific early-stage, mostly Singapore-linked companies. Decide "
    "whether ONE candidate item earns a place in the digest.\n\n"
    "Keep it only if reading it would teach the reader something new about THAT "
    "specific company: funding, product launches, partnerships, customers, "
    "hiring, regulatory events, founder moves, acquisitions, shutdowns.\n\n"
    "Drop it if: it is about a different entity that merely shares the name (a "
    "public ticker, a band, a foreign company, a generic word) — the company "
    "description is authoritative for identity; it is a passing mention in a "
    "list or roundup; it is an evergreen job req with no information; or it is "
    "an arXiv revision notice.\n\n"
    "You are NOT told which pipeline kept or dropped this item; judge it on its "
    "own merits. Respond with ONLY a JSON object: "
    '{"verdict": "keep" | "drop", "reason": "<one short clause>"}'
)


def build_user_message(row: dict) -> str:
    fields = {
        "company": row.get("company", ""),
        "company_description": row.get("company_description", ""),
        "headline": row.get("headline", ""),
        "description": row.get("description", ""),
        "source": row.get("source", ""),
    }
    return json.dumps(fields, ensure_ascii=False)


def judge_row(llm, row: dict, model: str | None) -> tuple[str | None, str | None]:
    """Return (verdict, reason); verdict is 'keep'/'drop' or None on failure."""
    reply = llm.chat(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": build_user_message(row)},
        ],
        model=model,
        max_tokens=120,
    )
    obj = llm.extract_json(reply) if reply else None
    if not obj:
        return None, None
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in {"keep", "drop"}:
        return None, None
    reason = str(obj.get("reason", "")).strip()[:200]
    return verdict, reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--limit", type=int, default=0, help="Max rows to label this run (0 = all).")
    parser.add_argument("--model", default=None, help="Override the judge model.")
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()

    llm = _load_ab_llm()
    path = repo / "signals" / "ab" / "disagreements.jsonl"
    if not path.exists():
        print("no disagreements file; nothing to label")
        return 0
    if not llm.have_key():
        print("DEEPSEEK_API_KEY not set; leaving labels unfilled (fail-open)")
        return 0

    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    pending = [r for r in rows if r.get("label") is None]
    if args.limit > 0:
        pending = pending[: args.limit]
    if not pending:
        print("all disagreements already labeled")
        return 0

    labeled = 0
    for row in pending:
        verdict, reason = judge_row(llm, row, args.model)
        if verdict is None:
            continue  # fail-open: leave null, retry next run
        row["label"] = verdict
        row["label_model"] = args.model or "deepseek-chat"
        row["label_reason"] = reason
        labeled += 1

    path.write_text(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    print(f"labeled {labeled}/{len(pending)} pending disagreements ({len(rows)} total rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
