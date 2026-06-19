#!/usr/bin/env python3
"""Significance test for the v1/v2 A/B experiment (McNemar, fixed-N).

Reads the blind-judge labels in ``signals/ab/disagreements.jsonl`` and asks one
question: *do the arms make errors at different rates?*

Every disagreement is a **discordant pair** by construction — one arm kept the
item, the other dropped it, so exactly one arm matches the judge's verdict:

  kept_by=v1, judge=keep -> v1 was right (v2 missed a real item)
  kept_by=v1, judge=drop -> v2 was right (v1 let noise through)
  kept_by=v2, judge=keep -> v2 was right
  kept_by=v2, judge=drop -> v1 was right

Let b = #(v1 right), c = #(v2 right). Under H0 (equal error rates) each
discordant pair favors either arm with probability 0.5, so we run an **exact
two-sided binomial test** (this is McNemar's test; with all pairs discordant it
reduces to the sign test) on b vs c.

**Fixed-N, test once.** We pre-register a target number of discordant pairs
(default 40 — ~80% power to detect a 72/28 split at alpha=0.05) and only read
the p-value as decisive once that target is reached. Before then the report
shows progress only; peeking at an unpowered p-value inflates false positives.

**Volume guardrail.** The binomial test only rules on the *discordant subset*.
A challenger can win it while silently keeping far too much (noise flood) or
far too little (signal collapse) overall — and that regression never shows up
in a kept-vs-dropped pair count. So alongside the win metric we read the daily
``metrics.jsonl`` and flag when v2's keep-volume relative to v1 drifts outside a
sanity band over a trailing window. It's a cheap proxy (we don't track
tokens/ops); a ``warn`` means "investigate before trusting the verdict," not a
hard stop.

Writes ``signals/ab/significance.json`` (machine) and appends a "Significance"
section to ``signals/ab/report.md`` (human). Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALPHA = 0.05
SECTION_HEADER = "## Significance (McNemar, blind-judge labels)"


def right_arm(row: dict) -> str | None:
    """Which arm the judge's verdict vindicated, or None if unusable."""
    kept_by, label = row.get("kept_by"), row.get("label")
    if label not in {"keep", "drop"} or kept_by not in {"v1", "v2"}:
        return None
    # The arm that kept it is right iff the item should be kept.
    if label == "keep":
        return kept_by
    return "v2" if kept_by == "v1" else "v1"


def binom_two_sided(b: int, c: int) -> float | None:
    """Exact two-sided binomial p-value for b vs c under p=0.5."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def load_metrics(repo: Path) -> list[dict]:
    """Daily compare rows written by ab_compare.py (v1_items / v2_items / ...)."""
    path = repo / "signals" / "ab" / "metrics.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]


def compute_guardrail(metrics: list[dict], lo: float, hi: float, window: int) -> dict:
    """Volume sanity check, independent of the win metric.

    Sums v1/v2 kept items over the trailing ``window`` compared days (summing
    before dividing so a light day can't swing the ratio) and compares
    v2/v1 against ``[lo, hi]``. ``warn_low`` = v2 keeping too little (signal
    collapse), ``warn_high`` = too much (noise flood).
    """
    rows = sorted((r for r in metrics if r.get("date")), key=lambda r: r["date"])
    recent = rows[-window:] if window else rows
    v1 = sum(int(r.get("v1_items", 0)) for r in recent)
    v2 = sum(int(r.get("v2_items", 0)) for r in recent)
    result = {"window_days": len(recent), "v1_items": v1, "v2_items": v2, "lo": lo, "hi": hi}
    if v1 == 0:
        result["ratio"] = None
        result["status"] = "insufficient"
        return result
    ratio = v2 / v1
    result["ratio"] = round(ratio, 3)
    if ratio < lo:
        result["status"] = "warn_low"
    elif ratio > hi:
        result["status"] = "warn_high"
    else:
        result["status"] = "ok"
    return result


def compute(rows: list[dict], target: int) -> dict:
    labeled = [r for r in rows if right_arm(r) is not None]
    b = sum(1 for r in labeled if right_arm(r) == "v1")  # v1 right / v2 wrong
    c = sum(1 for r in labeled if right_arm(r) == "v2")  # v2 right / v1 wrong
    n = b + c
    p = binom_two_sided(b, c)
    result = {
        "n_discordant": n,
        "v1_right": b,
        "v2_right": c,
        "p_value": None if p is None else round(p, 5),
        "target": target,
        "unlabeled": sum(1 for r in rows if r.get("label") is None),
    }
    if n < target:
        result["status"] = "collecting"
        result["winner"] = None
    elif p is not None and p < ALPHA:
        result["status"] = "significant"
        result["winner"] = "v2" if c > b else "v1"
    else:
        result["status"] = "not_significant"
        result["winner"] = None
    return result


def render_section(res: dict) -> str:
    lines = [SECTION_HEADER, ""]
    n, b, c, target = res["n_discordant"], res["v1_right"], res["v2_right"], res["target"]
    pct = round(100 * n / target) if target else 0
    lines += [
        f"- Discordant pairs labeled: **{n}** / {target} target ({pct}%)",
        f"- v1 right (v2 missed): **{b}** · v2 right (v1 let noise through): **{c}**",
        f"- Unlabeled disagreements awaiting judge: {res['unlabeled']}",
    ]
    p = res["p_value"]
    p_str = "n/a" if p is None else f"{p:.4f}"
    if res["status"] == "collecting":
        lines.append(
            f"- **Verdict: collecting** — need {target - n} more discordant pairs before "
            f"reading the p-value (current p={p_str}, not yet powered)."
        )
    elif res["status"] == "significant":
        better = res["winner"]
        worse = "v1" if better == "v2" else "v2"
        lines.append(
            f"- **Verdict: {better} judges significantly better than {worse}** "
            f"(p={p_str} < {ALPHA}, n={n})."
        )
    else:
        lines.append(
            f"- **Verdict: no significant difference** (p={p_str} ≥ {ALPHA}, n={n}); "
            f"the arms' error rates are statistically indistinguishable at this sample."
        )
    g = res.get("guardrail")
    if g:
        if g["status"] == "insufficient":
            lines.append(
                f"- Volume guardrail: insufficient data "
                f"(no v1 items in the last {g['window_days']} compared days)."
            )
        else:
            band = f"[{g['lo']}, {g['hi']}]"
            vol = f"{g['v2_items']} vs {g['v1_items']} items over {g['window_days']} days"
            if g["status"] == "ok":
                lines.append(
                    f"- Volume guardrail: **ok** — v2 kept {g['ratio']}× v1's volume "
                    f"({vol}), within {band}."
                )
            else:
                why = "too little (signal collapse?)" if g["status"] == "warn_low" else "too much (noise flood?)"
                lines.append(
                    f"- ⚠️ **Volume guardrail: {g['status']}** — v2 kept {g['ratio']}× v1's "
                    f"volume ({vol}), outside {band}; v2 is keeping {why} "
                    f"Investigate before trusting the verdict."
                )
    lines.append("")
    return "\n".join(lines)


def upsert_section(report_path: Path, section: str) -> None:
    """Append the Significance section, replacing a prior one if present."""
    body = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    idx = body.find(SECTION_HEADER)
    if idx != -1:
        body = body[:idx].rstrip() + "\n"
    else:
        body = body.rstrip() + "\n" if body.strip() else ""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(body + "\n" + section, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--target", type=int, default=40, help="Pre-registered discordant-pair target (default 40)."
    )
    parser.add_argument(
        "--guardrail-lo", type=float, default=0.5, help="Min acceptable v2/v1 keep-volume ratio."
    )
    parser.add_argument(
        "--guardrail-hi", type=float, default=2.0, help="Max acceptable v2/v1 keep-volume ratio."
    )
    parser.add_argument(
        "--guardrail-window", type=int, default=14, help="Trailing days for the volume guardrail."
    )
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()

    path = repo / "signals" / "ab" / "disagreements.jsonl"
    rows = (
        [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if path.exists()
        else []
    )
    res = compute(rows, args.target)
    res["guardrail"] = compute_guardrail(
        load_metrics(repo), args.guardrail_lo, args.guardrail_hi, args.guardrail_window
    )

    out = repo / "signals" / "ab" / "significance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    upsert_section(repo / "signals" / "ab" / "report.md", render_section(res))

    print(
        f"status={res['status']} n={res['n_discordant']}/{args.target} "
        f"v1_right={res['v1_right']} v2_right={res['v2_right']} p={res['p_value']} "
        f"guardrail={res['guardrail']['status']} ratio={res['guardrail']['ratio']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
