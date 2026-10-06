#!/usr/bin/env python3
"""Turn graded production failures into scenario cards.

Three sources, each optional and each fail-open (a missing or unreadable file
is reported and skipped, never fatal):

  --from-ab  signals/ab/disagreements.jsonl — every labeled v1/v2 disagreement
             becomes an ``ab_seed`` ingest card whose expected verdict is the
             blind judge's label.
  --judged   data/harness/judged/<date>.jsonl — rows from
             daily_judge_sample.py where the judge disagreed with the pipeline
             become ``judge_disagreement`` ingest cards.
  --issues   data/harness/issues.json — ``gh issue list --json
             number,title,body,labels,url`` output; issues labelled
             harness:wrong-keep / harness:missed / harness:brief-error become
             ``human`` cards (weight 3 in grading). Needs GitHub Issues enabled.
  --labels   signals/harness/human-labels.jsonl — one JSON object per line,
             ``{url, company, label, note, date}`` with ``label`` one of
             wrong-keep / missed / brief-error (optional ``headline``). The
             issue-free way for an operator to file a human correction; becomes
             the same ``human`` cards, idempotent per URL.

Idempotent: a candidate whose URL already has a card (any status) is skipped,
and an issue that already produced a card is skipped. Cards are written
through scenarios.save, so every card on disk validates. Pure stdlib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenarios

INGEST_PERSONA = "Layer 1 relevance pass over data/candidates.json"
SYNTHESIS_PERSONA = "Layer 3 synthesis of today's signals into the company's living brief"
INGEST_GRADERS = ("verdict_match", "blind_judge")
SYNTHESIS_GRADERS = ("no_fabrication",)

ISSUE_LABELS = {
    "harness:wrong-keep": ("ingest", "drop"),
    "harness:missed": ("ingest", "keep"),
    "harness:brief-error": ("synthesis", None),
}
URL_RE = re.compile(r"https?://[^\s<>()\"'\]]+")
COMPANY_RE = re.compile(r"^[\s>*_-]*company[\s*_]*[:：][\s*_]*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
HEADLINE_RE = re.compile(
    r"^[\s>*_-]*(headline|title)[\s*_]*[:：][\s*_]*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def host_of(url: str) -> str | None:
    try:
        netloc = urlsplit(url).netloc.lower()
    except ValueError:
        return None
    return netloc.removeprefix("www.") or None


def created_by() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID")
    return f"write_scenarios@run {run_id}" if run_id else "write_scenarios@local"


def failure_kind_for(expected_verdict: str) -> str:
    return "false_keep" if expected_verdict == "drop" else "false_drop"


def ingest_card(row: dict, *, expected_verdict: str, origin: dict, reason: str, root: Path) -> dict:
    company = str(row.get("company", "")).strip()
    url = str(row["url"])
    headline = str(row.get("headline", "")).strip()
    action = "Keep" if expected_verdict == "keep" else "Drop"
    companies = {company: {"description": str(row.get("company_description", ""))}} if company else {}
    return {
        "id": scenarios.next_id("ingest", origin["date"], root),
        "layer": "ingest",
        "version": 1,
        "status": "active",
        "origin": origin,
        "persona": INGEST_PERSONA,
        "goal": f"{action} '{headline}' for {company or 'the matched company'}",
        "input": {
            "candidate": {
                "title": headline,
                "url": url,
                "source": str(row.get("source", "")),
                "description": str(row.get("description", "")),
                "matched": [company] if company else [],
            },
            "companies": companies,
        },
        "expected": {
            "verdict": expected_verdict,
            "company": company if expected_verdict == "keep" and company else None,
            "reason": reason,
        },
        "cluster": scenarios.cluster_key(
            "ingest", failure_kind_for(expected_verdict), company or "unknown", host_of(url)
        ),
        "graders": list(INGEST_GRADERS),
        "created_by": created_by(),
    }


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            print(f"write_scenarios: skipping malformed line in {path}", file=sys.stderr)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def row_date(row: dict, fallback: str) -> str:
    value = str(row.get("date", ""))
    return value if DATE_RE.match(value) else fallback


def save_card(card: dict, root: Path) -> bool:
    try:
        scenarios.save(card, root)
    except scenarios.ScenarioError as error:
        print(f"write_scenarios: invalid card skipped: {error}", file=sys.stderr)
        return False
    return True


def has_card_for(row: dict, root: Path) -> bool:
    return scenarios.find_by_url(str(row["url"]), root, layer="ingest") is not None


def ab_origin(row: dict, today: str) -> dict:
    return {
        "kind": "ab_seed",
        "date": row_date(row, today),
        "source": "signals/ab/disagreements.jsonl",
        "kept_by": row.get("kept_by"),
        "label_model": row.get("label_model"),
    }


def from_ab(path: Path, root: Path, today: str) -> int:
    written = 0
    for row in read_jsonl(path):
        label = row.get("label")
        if label not in {"keep", "drop"} or not row.get("url") or has_card_for(row, root):
            continue
        reason = str(row.get("label_reason") or "")
        card = ingest_card(row, expected_verdict=label, origin=ab_origin(row, today), reason=reason, root=root)
        written += save_card(card, root)
    return written


def judged_origin(row: dict, path: Path, today: str) -> dict:
    origin = {
        "kind": "judge_disagreement",
        "date": row_date(row, today),
        "source": path.name,
        "verdict_pipeline": row.get("verdict_pipeline"),
    }
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        origin["run_id"] = run_id
    return origin


def is_disagreement(row: dict) -> bool:
    judge_verdict = row.get("verdict_judge")
    return judge_verdict in {"keep", "drop"} and judge_verdict != row.get("verdict_pipeline")


def from_judged(path: Path, root: Path, today: str) -> int:
    written = 0
    for row in read_jsonl(path):
        if not is_disagreement(row) or not row.get("url") or has_card_for(row, root):
            continue
        reason = str(row.get("judge_reason") or "")
        origin = judged_origin(row, path, today)
        card = ingest_card(row, expected_verdict=row["verdict_judge"], origin=origin, reason=reason, root=root)
        written += save_card(card, root)
    return written


def label_names(issue: dict) -> list[str]:
    names: list[str] = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str):
            names.append(name)
    return names


def harness_label(issue: dict) -> str | None:
    for name in label_names(issue):
        if name in ISSUE_LABELS:
            return name
    return None


def parse_issue(issue: dict) -> dict | None:
    body = str(issue.get("body") or "")
    url_match = URL_RE.search(body)
    company_match = COMPANY_RE.search(body)
    if not url_match or not company_match:
        return None
    headline_match = HEADLINE_RE.search(body)
    headline = headline_match.group("value") if headline_match else str(issue.get("title") or "")
    company = company_match.group("value").strip("*_ ")
    if not company:
        return None
    return {
        "url": url_match.group(0).rstrip(".,;"),
        "company": company,
        "headline": headline.strip("*_ "),
    }


def human_origin(issue: dict, today: str) -> dict:
    return {"kind": "human", "date": today, "issue": issue.get("number"), "issue_url": issue.get("url")}


def synthesis_card(issue: dict, parsed: dict, root: Path, today: str) -> dict:
    company = parsed["company"]
    return {
        "id": scenarios.next_id("synthesis", today, root),
        "layer": "synthesis",
        "version": 1,
        "status": "active",
        "origin": human_origin(issue, today),
        "persona": SYNTHESIS_PERSONA,
        "goal": f"Correct the brief for {company}: {issue.get('title') or ''}".strip(),
        "input": {"company": company, "url": parsed["url"], "headline": parsed["headline"]},
        "expected": {"correction": str(issue.get("body") or "").strip()},
        "cluster": scenarios.cluster_key("synthesis", "brief_error", company, host_of(parsed["url"])),
        "graders": list(SYNTHESIS_GRADERS),
        "created_by": created_by(),
    }


def issue_card(issue: dict, label: str, parsed: dict, root: Path, today: str) -> dict:
    layer, expected_verdict = ISSUE_LABELS[label]
    if layer == "synthesis":
        return synthesis_card(issue, parsed, root, today)
    reason = str(issue.get("title") or "")
    return ingest_card(parsed, expected_verdict=expected_verdict, origin=human_origin(issue, today), reason=reason, root=root)


def card_for_issue(issue: dict, root: Path, today: str) -> dict | None:
    label = harness_label(issue)
    if label is None or scenarios.find_by_issue(issue.get("number"), root):
        return None
    parsed = parse_issue(issue)
    if parsed is None:
        print(f"write_scenarios: issue #{issue.get('number')} unparseable; skipping", file=sys.stderr)
        return None
    return issue_card(issue, label, parsed, root, today)


def from_issues(path: Path, root: Path, today: str) -> int:
    issues = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(issues, list):
        print(f"write_scenarios: {path} is not a list; skipping", file=sys.stderr)
        return 0
    written = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        card = card_for_issue(issue, root, today)
        if card is not None:
            written += save_card(card, root)
    return written


def label_origin(row: dict, today: str) -> dict:
    return {"kind": "human", "date": row_date(row, today), "source": "human-labels.jsonl"}


def card_for_label(row: dict, root: Path, today: str) -> dict | None:
    label = ISSUE_LABELS.get(f"harness:{row.get('label')}")
    url = str(row.get("url") or "").strip()
    company = str(row.get("company") or "").strip()
    if label is None or not url or not company:
        print(f"write_scenarios: label row for {url or 'no url'} invalid; skipping", file=sys.stderr)
        return None
    layer, expected_verdict = label
    if scenarios.find_by_url(url, root, layer=layer) is not None:
        return None
    note = str(row.get("note") or "").strip()
    origin = label_origin(row, today)
    if layer == "synthesis":
        headline = str(row.get("headline") or note).strip()
        parsed = {"url": url, "company": company, "headline": headline}
        card = synthesis_card({"title": note, "body": note}, parsed, root, origin["date"])
        card["origin"] = origin
        return card
    parsed = {"url": url, "company": company, "headline": str(row.get("headline") or note or url).strip()}
    return ingest_card(parsed, expected_verdict=expected_verdict, origin=origin, reason=note, root=root)


def from_labels(path: Path, root: Path, today: str) -> int:
    written = 0
    for row in read_jsonl(path):
        card = card_for_label(row, root, today)
        if card is not None:
            written += save_card(card, root)
    return written


def run_source(name: str, writer, path: Path | None, root: Path, today: str) -> int:
    if path is None:
        return 0
    if not path.exists():
        print(f"write_scenarios: {name} source {path} missing; skipping")
        return 0
    try:
        return writer(path, root, today)
    except (OSError, ValueError) as error:
        print(f"write_scenarios: {name} source failed ({error}); skipping", file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    parser.add_argument("--from-ab", type=Path, default=None)
    parser.add_argument("--judged", type=Path, default=None)
    parser.add_argument("--issues", type=Path, default=None)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=scenarios.SCENARIOS_DIR)
    args = parser.parse_args(argv)

    counts = {
        "ab_seed": run_source("ab", from_ab, args.from_ab, args.root, args.date),
        "judge_disagreement": run_source("judged", from_judged, args.judged, args.root, args.date),
        "human": run_source("issues", from_issues, args.issues, args.root, args.date)
        + run_source("labels", from_labels, args.labels, args.root, args.date),
    }
    detail = " ".join(f"{kind}={count}" for kind, count in counts.items())
    print(f"write_scenarios: wrote {sum(counts.values())} cards ({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
