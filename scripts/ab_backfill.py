#!/usr/bin/env python3
"""Backfill the A/B significance sample by replaying both arms over a candidate pool.

The daily cron yields ~1-5 disagreements/day, so reaching a powered sample
(~40 discordant pairs, see ``ab_stats.py``) takes weeks. This harness collapses
that to a single batch: it replays **both arms' relevance judgment** over an
existing ``data/candidates.json`` (e.g. a wide ``collect-candidates.py`` pull
over the last N days) and records the items the arms decide differently.

Faithfulness: each arm's actual ingest prompt (``prompts/ingest.md`` for v1,
``prompts/v2/ingest.md`` for v2) is used verbatim as the system context, with a
one-candidate override appended — so the variable under test (the relevance
policy) is exactly the production one; only the surrounding fetch/dedup/write
scaffolding is bypassed. ``pre_extracted`` candidates auto-keep in both arms, so
they are skipped (never a disagreement).

Output rows land in ``signals/ab/disagreements.jsonl`` flagged ``origin:
"backfill"``, ready for ``ab_judge.py`` then ``ab_stats.py`` — the same pipeline
the daily path feeds. Existing rows (incl. their labels) are preserved.

Fail-open per candidate. Pure stdlib (uses ``scripts/ab_llm.py``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

ARM_OVERRIDE = (
    "\n\n---\n\nA/B BACKFILL MODE. Ignore every instruction above about fetching "
    "URLs, deduplication, seen-urls files, writing output files, and stdout "
    "format. Apply ONLY this arm's relevance policy to the SINGLE candidate in "
    "the user message and respond with ONLY a JSON object: "
    '{"keep": true | false}. No prose.'
)


def _load_ab_llm():
    if "ab_llm" in sys.modules:
        return sys.modules["ab_llm"]
    spec = importlib.util.spec_from_file_location("ab_llm", Path(__file__).resolve().parent / "ab_llm.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab_llm"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_ab_compare():
    if "ab_compare" in sys.modules:
        return sys.modules["ab_compare"]
    spec = importlib.util.spec_from_file_location(
        "ab_compare", Path(__file__).resolve().parent / "ab_compare.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab_compare"] = mod
    spec.loader.exec_module(mod)
    return mod


def candidate_date(cand: dict, fallback: str) -> str:
    m = DATE_RE.search(cand.get("pubDate") or "")
    return m.group(1) if m else fallback


def arm_keep(llm, system_prompt: str, cand: dict, companies: dict, model: str | None) -> bool | None:
    """Run one arm's policy over one candidate; True/False kept, None on failure."""
    user = json.dumps(
        {
            "company": cand.get("company", ""),
            "company_description": (companies.get(cand.get("company", "")) or "").strip(),
            "headline": cand.get("headline", ""),
            "description": cand.get("description", ""),
            "source": cand.get("source", ""),
            "source_kind": cand.get("source_kind", ""),
        },
        ensure_ascii=False,
    )
    reply = llm.chat(
        [
            {"role": "system", "content": system_prompt + ARM_OVERRIDE},
            {"role": "user", "content": user},
        ],
        model=model,
        max_tokens=40,
    )
    obj = llm.extract_json(reply) if reply else None
    if not obj or "keep" not in obj:
        return None
    return bool(obj["keep"])


def append_disagreements(path: Path, new_rows: list[dict]) -> int:
    """Add rows whose (date, url) is not already recorded; preserve existing."""
    cmp_mod = _load_ab_compare()
    existing = cmp_mod.load_rows(path)
    seen = {(r.get("date"), r.get("url")) for r in existing}
    added = 0
    for row in new_rows:
        key = (row["date"], row["url"])
        if key in seen:
            continue
        seen.add(key)
        existing.append(row)
        added += 1
    existing.sort(key=lambda r: (r["date"], r["url"]))
    cmp_mod.write_rows(path, existing)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--candidates", default=None, help="Path to candidates.json (default: data/candidates.json).")
    parser.add_argument("--limit", type=int, default=0, help="Max candidates to replay (0 = all).")
    parser.add_argument("--date", default="2000-01-01", help="Fallback date for candidates without a pubDate.")
    parser.add_argument("--model", default=None, help="Override the replay model.")
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()

    llm = _load_ab_llm()
    if not llm.have_key():
        print("DEEPSEEK_API_KEY not set; backfill needs the model (fail-open, exiting 0)")
        return 0

    cand_path = Path(args.candidates) if args.candidates else repo / "data" / "candidates.json"
    if not cand_path.exists():
        print(f"no candidates file at {cand_path}; nothing to replay")
        return 0
    data = json.loads(cand_path.read_text(encoding="utf-8"))
    companies = data.get("companies", {}) or {}
    candidates = [c for c in data.get("candidates", []) if not c.get("pre_extracted")]
    if args.limit > 0:
        candidates = candidates[: args.limit]

    v1_prompt = (repo / "prompts" / "ingest.md").read_text(encoding="utf-8")
    v2_prompt = (repo / "prompts" / "v2" / "ingest.md").read_text(encoding="utf-8")

    normalize = _load_ab_compare().normalize_url
    new_rows: list[dict] = []
    errors = 0
    for cand in candidates:
        link = cand.get("link") or ""
        if not link:
            continue
        keep_v1 = arm_keep(llm, v1_prompt, cand, companies, args.model)
        keep_v2 = arm_keep(llm, v2_prompt, cand, companies, args.model)
        if keep_v1 is None or keep_v2 is None:
            errors += 1
            continue
        if keep_v1 == keep_v2:
            continue  # agreement
        new_rows.append(
            {
                "date": candidate_date(cand, args.date),
                "url": normalize(link),
                "company": cand.get("company", ""),
                "headline": cand.get("headline", ""),
                "source": cand.get("source", ""),
                "description": (cand.get("description") or "").strip(),
                "company_description": (companies.get(cand.get("company", "")) or "").strip(),
                "kept_by": "v1" if keep_v1 else "v2",
                "origin": "backfill",
                "label": None,
                "label_model": None,
                "label_reason": None,
            }
        )

    added = append_disagreements(repo / "signals" / "ab" / "disagreements.jsonl", new_rows)
    print(
        f"replayed {len(candidates)} candidates: {len(new_rows)} disagreements, "
        f"{added} new rows added, {errors} skipped on error"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
