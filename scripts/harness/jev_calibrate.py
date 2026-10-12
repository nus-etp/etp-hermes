#!/usr/bin/env python3
"""Calibrate the Jev judge against labeled ground truth.

Runs ``jev_judge.judge_item`` over every active ingest scenario card (whose
``expected.verdict`` is the label) and prints agreement overall, by origin
kind and by failure kind, a confusion table and mean confidence of agreements
vs disagreements. ``--judged`` additionally compares Jev against the
DeepSeek ``verdict_judge`` in ``data/harness/judged/*.jsonl``. Writes
``data/harness/jev-calibration.json``. Fail-open: no key exits 0. Pure stdlib.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios


def load_jev_judge():
    if "jev_judge" in sys.modules:
        return sys.modules["jev_judge"]
    spec = importlib.util.spec_from_file_location("jev_judge", REPO_ROOT / "scripts" / "jev_judge.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["jev_judge"] = module
    spec.loader.exec_module(module)
    return module


def row_from_card(card: dict) -> dict:
    candidate = card["input"]["candidate"]
    company = (candidate.get("matched") or [""])[0]
    companies = card["input"].get("companies") or {}
    return {
        "company": company,
        "company_description": str((companies.get(company) or {}).get("description") or ""),
        "headline": candidate.get("title", ""),
        "description": candidate.get("description", ""),
        "source": candidate.get("source", ""),
    }


def failure_kind(card: dict) -> str:
    parts = str(card["cluster"]).split("/")
    return parts[1] if len(parts) > 1 else "unknown"


def load_judged_rows(directory: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def agreement_summary(records: list[dict]) -> dict:
    judged = [r for r in records if r["jev"] is not None]
    agreed = [r for r in judged if r["jev"] == r["truth"]]
    disagreed = [r for r in judged if r["jev"] != r["truth"]]
    return {
        "total": len(records),
        "judged": len(judged),
        "agreed": len(agreed),
        "agreement": round(len(agreed) / len(judged), 4) if judged else None,
        "mean_confidence_agree": mean_confidence(agreed),
        "mean_confidence_disagree": mean_confidence(disagreed),
    }


def mean_confidence(records: list[dict]) -> float | None:
    values = [r["confidence"] for r in records if r["confidence"] is not None]
    return round(sum(values) / len(values), 4) if values else None


def group_summary(records: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[record[key]].append(record)
    return {name: agreement_summary(group) for name, group in sorted(groups.items())}


def confusion(records: list[dict]) -> dict:
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        table[f"truth_{record['truth']}"][f"jev_{record['jev']}"] += 1
    return {truth: dict(counts) for truth, counts in table.items()}


def token_cost(usages: list[dict]) -> dict:
    inputs = [u.get("input_tokens", 0) for u in usages if u]
    return {
        "items": len(inputs),
        "input_tokens_total": sum(inputs),
        "input_tokens_mean": round(sum(inputs) / len(inputs), 1) if inputs else 0,
    }


def calibrate_cards(jev, root: Path) -> tuple[list[dict], list[dict]]:
    records, usages = [], []
    for card in scenarios.load_all(root, layer="ingest"):
        result = jev.judge_item(row_from_card(card))
        usages.append(result["usage"])
        records.append(
            {
                "id": card["id"],
                "truth": card["expected"]["verdict"],
                "jev": result["verdict"],
                "confidence": result["confidence"],
                "origin_kind": card["origin"]["kind"],
                "failure_kind": failure_kind(card),
            }
        )
    return records, usages


def calibrate_judged(jev, directory: Path) -> tuple[list[dict], list[dict]]:
    records, usages = [], []
    for row in load_judged_rows(directory):
        if row.get("verdict_judge") not in {"keep", "drop"}:
            continue
        result = jev.judge_item(row)
        usages.append(result["usage"])
        records.append(
            {
                "url": row["url"],
                "truth": row["verdict_judge"],
                "jev": result["verdict"],
                "confidence": result["confidence"],
                "origin_kind": row.get("origin", "daily"),
                "failure_kind": f"pipeline_{row.get('verdict_pipeline')}",
            }
        )
    return records, usages


def section(records: list[dict], usages: list[dict]) -> dict:
    return {
        "overall": agreement_summary(records),
        "by_origin_kind": group_summary(records, "origin_kind"),
        "by_failure_kind": group_summary(records, "failure_kind"),
        "confusion": confusion([r for r in records if r["jev"] is not None]),
        "tokens": token_cost(usages),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=scenarios.SCENARIOS_DIR)
    parser.add_argument("--judged-dir", type=Path, default=REPO_ROOT / "data" / "harness" / "judged")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "harness" / "jev-calibration.json")
    args = parser.parse_args(argv)

    jev = load_jev_judge()
    if not jev.have_key():
        print("jev_calibrate: TYPESAFE_API_KEY not set; nothing calibrated")
        return 0
    report = {"cards": section(*calibrate_cards(jev, args.root))}
    if args.judged_dir.exists():
        report["judged_vs_deepseek"] = section(*calibrate_judged(jev, args.judged_dir))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, body in report.items():
        summary = {k: v for k, v in body.items() if k != "records"}
        print(name, json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
