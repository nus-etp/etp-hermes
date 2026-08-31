#!/usr/bin/env python3
"""Draft per-company `match_terms` / `exclude_terms` for data/companies.json.

An operator-run one-off — deliberately **not** wired into any workflow. The
daily pipeline only *consumes* these fields (scripts/collect-candidates.py via
scripts/entity_terms.py); this script proposes them, and a human reviews the
diff in a PR.

Two passes:

1. **Deterministic** — the company's own web domain, harvested from its
   `sources[]` URLs and `identifiers` (third-party hosts like crunchbase,
   linkedin, github and lever are skipped). A domain always contains a dot, so
   it is a *strong* match term: "assist.id" mentioned in a story body links the
   item to Assist.id, where the bare word "assist" never should.
2. **LLM** — one DeepSeek call per company (via the shared fail-open client in
   scripts/ab_llm.py) asking for founder full names + distinctive product names
   as `match_terms`, and, for companies whose name is a generic word, the
   disambiguating `exclude_terms` that mark the *other* entities sharing it.
   Per-company granularity keeps failure containment simple: an HTTP error or
   an unparseable reply skips that company and the run continues.

Writes `data/match-terms-draft.json` (gitignored). It never touches
companies.json — that is `--merge`, a separate invocation that unions the draft
into the watchlist preserving key order and formatting.

Usage:
  # cheap trial: no API calls at all, deterministic domains only
  python3 scripts/generate_match_terms.py --no-llm

  # one company, with the LLM pass
  DEEPSEEK_API_KEY=... python3 scripts/generate_match_terms.py --only "Assist"

  # the companies that actually need help (no strong name term), 20 at a time
  DEEPSEEK_API_KEY=... python3 scripts/generate_match_terms.py --generic-only --limit 20

  # full run, then review the diff
  DEEPSEEK_API_KEY=... python3 scripts/generate_match_terms.py
  python3 scripts/generate_match_terms.py --merge && git diff data/companies.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_llm  # noqa: E402
from entity_terms import COMMON_WORDS, build_company_matchers, normalize_term  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = REPO_ROOT / "data" / "companies.json"
DRAFT_FILE = REPO_ROOT / "data" / "match-terms-draft.json"

# Hosts that are never a company's own domain — a crunchbase/linkedin/github
# URL identifies the company but its domain is worthless as a match term.
THIRD_PARTY_HOSTS = (
    "crunchbase.com",
    "linkedin.com",
    "github.com",
    "github.io",
    "lever.co",
    "greenhouse.io",
    "workable.com",
    "arxiv.org",
    "medium.com",
    "substack.com",
    "wordpress.com",
    "blogspot.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "notion.site",
    "google.com",
    "nus.edu.sg",
    "sginnovate.com",
    "prnewswire.com",
    "businesswire.com",
    "feedburner.com",
)

# Sub-domain labels that carry no brand of their own — "press.carousell.com"
# never appears in an article body, "carousell.com" might.
SUBDOMAIN_PREFIXES = {
    "www",
    "press",
    "news",
    "newsroom",
    "blog",
    "media",
    "careers",
    "jobs",
    "about",
    "investors",
    "corp",
    "ir",
    "en",
    "go",
}

MAX_MATCH_TERMS = 6
MAX_EXCLUDE_TERMS = 8
MIN_TERM_LEN = 3

SYSTEM_PROMPT = (
    "You help disambiguate news mentions of small Southeast Asian startups. "
    "Reply with a single JSON object and nothing else."
)

USER_TEMPLATE = """Company: {name}
Known aliases: {aliases}
Country: {country}
Description: {description}
The company name is a generic word or very short: {generic}

Propose two lists for a news-matching filter.

"match_terms": distinctive strings that, when they appear in an article,
identify THIS company. Good candidates: founder or CEO full names, the
distinctive product/platform name, the company's web domain. Only include a
term you can support from the description above — do not guess names. Prefer
multi-word terms. Skip anything generic ("platform", "app", "AI") and skip the
company name and aliases already listed. Empty list if nothing qualifies.

"exclude_terms": only if the name is generic — words that mark an article as
being about a DIFFERENT entity sharing this name (other industries, a stock
ticker, a foreign namesake, a common phrase the name appears in). Lowercase,
1-3 words each. Empty list otherwise.

Return: {{"match_terms": [...], "exclude_terms": [...]}}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _host(url: str) -> str:
    """Registrable-ish host: lowercased netloc with brandless sub-domains dropped."""
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    labels = host.split(".")
    while len(labels) > 2 and labels[0] in SUBDOMAIN_PREFIXES:
        labels.pop(0)
    return ".".join(labels)


def is_third_party(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in THIRD_PARTY_HOSTS)


def homepage_domains(company: dict[str, Any]) -> list[str]:
    """The company's own domains, from its sources[] URLs and identifiers.

    Deterministic, no network. Deduped, order-stable, third-party hosts and
    bare TLD-less hosts dropped.
    """
    urls = [s.get("url", "") for s in company.get("sources") or []]
    urls += [v for v in (company.get("identifiers") or {}).values() if isinstance(v, str)]
    out: list[str] = []
    for url in urls:
        host = _host(url)
        if not host or "." not in host or is_third_party(host):
            continue
        if host not in out:
            out.append(host)
    return out


def sanitize_terms(terms: Any, existing: set[str], *, limit: int, allow_common: bool) -> list[str]:
    """Keep the usable strings from a model reply: strings only, trimmed,
    deduped case-insensitively against each other and against `existing`, no
    stub-length terms, and (for match_terms) no bare dictionary words."""
    out: list[str] = []
    if not isinstance(terms, list):
        return out
    for raw in terms:
        if len(out) >= limit:
            break
        if not isinstance(raw, str):
            continue
        term = " ".join(raw.strip().split())
        key = normalize_term(term)
        if len(key) < MIN_TERM_LEN or key in existing:
            continue
        if not allow_common and " " not in key and key in COMMON_WORDS:
            continue
        out.append(term)
        existing.add(key)
    return out


def build_prompt(company: dict[str, Any], *, generic: bool) -> list[dict[str, str]]:
    user = USER_TEMPLATE.format(
        name=company["name"],
        aliases=", ".join(company.get("aliases") or []) or "(none)",
        country=company.get("country", "unknown"),
        description=(company.get("description") or "")[:800],
        generic="yes" if generic else "no",
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def propose_for_company(
    company: dict[str, Any],
    *,
    generic: bool,
    use_llm: bool,
    chat=ab_llm.chat,
) -> dict[str, Any] | None:
    """Draft entry for one company, or None when there is nothing to propose.

    Fail-open: an LLM error (chat returns None) or an unparseable reply degrades
    to the deterministic domains alone.
    """
    existing = {normalize_term(t) for t in [company["name"], *(company.get("aliases") or [])]}
    existing |= {normalize_term(t) for t in company.get("match_terms") or []}
    match_terms = [d for d in homepage_domains(company) if normalize_term(d) not in existing]
    for d in match_terms:
        existing.add(normalize_term(d))
    exclude_terms: list[str] = []

    if use_llm:
        reply = chat(build_prompt(company, generic=generic), max_tokens=400)
        obj = ab_llm.extract_json(reply) if reply else None
        if obj:
            match_terms += sanitize_terms(
                obj.get("match_terms"),
                existing,
                limit=MAX_MATCH_TERMS - len(match_terms),
                allow_common=False,
            )
            already = {normalize_term(t) for t in company.get("exclude_terms") or []}
            exclude_terms = sanitize_terms(
                obj.get("exclude_terms"),
                already,
                limit=MAX_EXCLUDE_TERMS,
                allow_common=True,
            )

    if not match_terms and not exclude_terms:
        return None
    entry: dict[str, Any] = {"name": company["name"]}
    if match_terms:
        entry["match_terms"] = match_terms
    if exclude_terms:
        entry["exclude_terms"] = exclude_terms
    return entry


def select_companies(
    companies: list[dict[str, Any]],
    *,
    only: str | None,
    generic_only: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    matchers = build_company_matchers(companies)
    out = []
    for c in companies:
        if only and only.lower() not in c["name"].lower():
            continue
        if generic_only and matchers[c["name"]].strong:
            continue  # already has a distinctive term; the guard won't bite
        out.append(c)
    return out[:limit] if limit else out


def generate(
    companies: list[dict[str, Any]],
    *,
    only: str | None = None,
    generic_only: bool = False,
    limit: int | None = None,
    use_llm: bool = True,
    sleep: float = 0.0,
    chat=ab_llm.chat,
) -> dict[str, Any]:
    selected = select_companies(companies, only=only, generic_only=generic_only, limit=limit)
    matchers = build_company_matchers(companies)
    entries: list[dict[str, Any]] = []
    for i, c in enumerate(selected):
        entry = propose_for_company(
            c,
            generic=not matchers[c["name"]].strong,
            use_llm=use_llm,
            chat=chat,
        )
        if entry:
            entries.append(entry)
        if sleep and use_llm and i + 1 < len(selected):
            time.sleep(sleep)
    return {
        "generated_at": _now(),
        "considered": len(selected),
        "llm": use_llm,
        "entries": entries,
    }


# --- merge -------------------------------------------------------------------

# Where a newly-added field is inserted, so the merged file keeps a stable
# shape: match_terms right after aliases, exclude_terms right after that.
FIELD_ANCHOR = {"match_terms": "aliases", "exclude_terms": "match_terms"}


def _union(existing: list[str] | None, new: list[str]) -> list[str]:
    out = list(existing or [])
    have = {normalize_term(t) for t in out}
    for t in new:
        if normalize_term(t) not in have:
            out.append(t)
            have.add(normalize_term(t))
    return out


def _with_field(entry: dict[str, Any], field: str, value: list[str]) -> dict[str, Any]:
    """Set `field`, inserting it after its anchor key when it's new."""
    if field in entry:
        return {k: (value if k == field else v) for k, v in entry.items()}
    anchor = FIELD_ANCHOR[field]
    out: dict[str, Any] = {}
    for k, v in entry.items():
        out[k] = v
        if k == anchor:
            out[field] = value
    out.setdefault(field, value)
    return out


def merge(companies: list[dict[str, Any]], draft: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Union drafted terms into the watchlist. Returns (companies, changed)."""
    by_name = {c["name"]: i for i, c in enumerate(companies)}
    out = list(companies)
    changed = 0
    for entry in draft.get("entries", []):
        idx = by_name.get(entry["name"])
        if idx is None:
            print(f"SKIP unknown company: {entry['name']}", file=sys.stderr)
            continue
        company = out[idx]
        touched = False
        for field in ("match_terms", "exclude_terms"):
            new = entry.get(field) or []
            if not new:
                continue
            merged = _union(company.get(field), new)
            if merged != (company.get(field) or []):
                company = _with_field(company, field, merged)
                touched = True
        if touched:
            out[idx] = company
            changed += 1
    return out, changed


def load_draft(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"entries": data}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="Only companies whose name contains this substring.")
    p.add_argument(
        "--generic-only",
        action="store_true",
        help="Only companies with no strong name term (the ones the guard restricts).",
    )
    p.add_argument("--limit", type=int, help="Stop after N companies (cheap trial runs).")
    p.add_argument("--no-llm", action="store_true", help="Deterministic domain pass only.")
    p.add_argument("--sleep", type=float, default=0.0, help="Seconds between LLM calls.")
    p.add_argument("--draft", default=str(DRAFT_FILE), help=f"Draft path (default: {DRAFT_FILE}).")
    p.add_argument(
        "--merge",
        action="store_true",
        help="Merge an existing draft into data/companies.json instead of generating.",
    )
    args = p.parse_args(argv)

    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    draft_path = Path(args.draft)

    if args.merge:
        if not draft_path.exists():
            print(f"no draft at {draft_path}", file=sys.stderr)
            return 2
        merged, changed = merge(companies, load_draft(draft_path))
        if not changed:
            print("Nothing to merge.")
            return 0
        COMPANIES_FILE.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Updated {changed} companies. Review with: git diff data/companies.json")
        return 0

    use_llm = not args.no_llm
    if use_llm and not ab_llm.have_key():
        print("DEEPSEEK_API_KEY unset — running the deterministic pass only.", file=sys.stderr)
        use_llm = False

    draft = generate(
        companies,
        only=args.only,
        generic_only=args.generic_only,
        limit=args.limit,
        use_llm=use_llm,
        sleep=args.sleep,
    )
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"generate-match-terms: considered={draft['considered']} "
        f"drafted={len(draft['entries'])} llm={draft['llm']} → {draft_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
