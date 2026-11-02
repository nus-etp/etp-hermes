"""Schema and invariants for data/companies.json."""

from __future__ import annotations

import re
from urllib.parse import urlparse

ALLOWED_SOURCE_TYPES = {"rss", "github_org", "ats_jobs", "lever_jobs", "recruitee_jobs", "html_scrape", "appstore_dev"}
ATS_PROVIDERS = {
    "lever",
    "recruitee",
    "smartrecruiters",
    "breezy",
    "ashby",
    "jazzhr",
    "manatal",
    "mycareersfuture",
    "jobstreet",
}
ALLOWED_IDENTIFIER_KEYS = {"crunchbase", "linkedin", "nus_cde", "sginnovate", "uen", "website"}
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _is_url(s: object) -> bool:
    if not isinstance(s, str):
        return False
    p = urlparse(s)
    return p.scheme in {"http", "https"} and bool(p.netloc)


def test_top_level_is_list(companies: list[dict]) -> None:
    assert isinstance(companies, list)
    assert companies, "companies.json must not be empty"


def test_required_keys_and_types(companies: list[dict]) -> None:
    errors: list[str] = []
    for c in companies:
        name = c.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"missing or empty name in entry: {c!r}")
            continue
        if not isinstance(c.get("description"), str) or not c["description"].strip():
            errors.append(f"{name}: missing/empty description")
        aliases = c.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            errors.append(f"{name}: aliases must be list[str]")
        sources = c.get("sources")
        if sources is None:
            continue
        if not isinstance(sources, list):
            errors.append(f"{name}: sources must be a list")
            continue
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                errors.append(f"{name}: sources[{i}] not a dict")
                continue
            stype = s.get("type")
            if stype not in ALLOWED_SOURCE_TYPES:
                errors.append(f"{name}: sources[{i}] type {stype!r} not in {sorted(ALLOWED_SOURCE_TYPES)}")
            if stype == "ats_jobs" and s.get("provider") not in ATS_PROVIDERS:
                errors.append(f"{name}: sources[{i}] ats_jobs provider {s.get('provider')!r} not in {sorted(ATS_PROVIDERS)}")
            if not _is_url(s.get("url")):
                errors.append(f"{name}: sources[{i}] url not a valid http(s) URL: {s.get('url')!r}")
            label = s.get("label")
            if label is not None and not isinstance(label, str):
                errors.append(f"{name}: sources[{i}] label must be str when present")
    assert not errors, "schema violations:\n  - " + "\n  - ".join(errors)


def test_match_and_exclude_terms_shape(companies: list[dict]) -> None:
    """Optional entity-linking fields honoured at triage time.

    `match_terms` are extra *positive* terms (founder names, product names, the
    homepage domain) and `exclude_terms` are negative ones — both consumed by
    scripts/collect-candidates.py via scripts/entity_terms.py, and drafted by
    scripts/generate_match_terms.py. Both are optional list[str] of non-empty,
    already-trimmed strings.
    """
    errors: list[str] = []
    for c in companies:
        for field in ("match_terms", "exclude_terms"):
            v = c.get(field)
            if v is None:
                continue
            if not isinstance(v, list):
                errors.append(f"{c['name']}: {field} must be a list")
                continue
            for i, t in enumerate(v):
                if not isinstance(t, str):
                    errors.append(f"{c['name']}: {field}[{i}] must be str, got {type(t).__name__}")
                elif not t.strip():
                    errors.append(f"{c['name']}: {field}[{i}] is empty")
                elif t != t.strip():
                    errors.append(f"{c['name']}: {field}[{i}] has surrounding whitespace: {t!r}")
            lowered = [t.lower() for t in v if isinstance(t, str)]
            if len(set(lowered)) != len(lowered):
                errors.append(f"{c['name']}: {field} has case-insensitive duplicates")
    assert not errors, "match/exclude term violations:\n  - " + "\n  - ".join(errors)


def test_every_company_has_country(companies: list[dict]) -> None:
    """Every company must carry a non-empty HQ `country` tag so the site's
    region filter covers the whole watchlist. New entries get one
    deterministically at merge time (scripts/merge_portfolio_entries.py via
    scripts/derive_country.py); a manual addition that trips this should run
    `python3 scripts/derive_country.py --write`."""
    missing = [
        c.get("name", repr(c))
        for c in companies
        if not isinstance(c.get("country"), str) or not c["country"].strip()
    ]
    assert not missing, (
        "companies missing a `country` tag (run "
        "`python3 scripts/derive_country.py --write`):\n  - " + "\n  - ".join(missing)
    )


def test_funding_rounds_shape(companies: list[dict]) -> None:
    errors: list[str] = []
    for c in companies:
        rounds = c.get("funding_rounds")
        if rounds is None:
            continue
        if not isinstance(rounds, list):
            errors.append(f"{c['name']}: funding_rounds must be list")
            continue
        for i, r in enumerate(rounds):
            tag = f"{c['name']}.funding_rounds[{i}]"
            if not isinstance(r, dict):
                errors.append(f"{tag}: not a dict")
                continue
            date = r.get("date")
            if date is not None and (not isinstance(date, str) or not DATE_RE.match(date)):
                errors.append(f"{tag}: date {date!r} must be YYYY / YYYY-MM / YYYY-MM-DD or null")
            amount_usd = r.get("amount_usd")
            if amount_usd is not None and not isinstance(amount_usd, (int, float)):
                errors.append(f"{tag}: amount_usd must be number or null, got {type(amount_usd).__name__}")
            if not _is_url(r.get("source")):
                errors.append(f"{tag}: source must be a valid URL, got {r.get('source')!r}")
            for k in ("lead_investors", "investors"):
                v = r.get(k, [])
                if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                    errors.append(f"{tag}: {k} must be list[str]")
    assert not errors, "funding_rounds violations:\n  - " + "\n  - ".join(errors)


ALLOWED_STATUSES = {"inactive"}
UEN_RE = re.compile(r"^(\d{8}[A-Z]|\d{9}[A-Z]|[ST]\d{2}[A-Z]{2}\d{4}[A-Z])$")


def test_status_fields_shape(companies: list[dict]) -> None:
    """Optional lifecycle fields. `status: inactive` marks a company that is
    legally dead (e.g. ACRA struck off / in liquidation); scripts/select_gapfill_queue.py
    skips it, firehose matching does not. An inactive entry must say why
    (`status_reason`) and carry the registry `identifiers.uen` it was matched on."""
    errors: list[str] = []
    for c in companies:
        name = c["name"]
        status = c.get("status")
        if status is not None and status not in ALLOWED_STATUSES:
            errors.append(f"{name}: status {status!r} not in {sorted(ALLOWED_STATUSES)}")
        reason = c.get("status_reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            errors.append(f"{name}: status_reason must be a non-empty string")
        uen = (c.get("identifiers") or {}).get("uen")
        if uen is not None and (not isinstance(uen, str) or not UEN_RE.match(uen)):
            errors.append(f"{name}: uen {uen!r} is not a valid UEN")
        if status == "inactive":
            if reason is None:
                errors.append(f"{name}: inactive entries need a status_reason")
            if uen is None:
                errors.append(f"{name}: inactive entries need identifiers.uen")
        if status is None and reason is not None:
            errors.append(f"{name}: status_reason without status")
    assert not errors, "status violations:\n  - " + "\n  - ".join(errors)


def test_identifiers_shape(companies: list[dict]) -> None:
    errors: list[str] = []
    for c in companies:
        ids = c.get("identifiers")
        if ids is None:
            continue
        if not isinstance(ids, dict):
            errors.append(f"{c['name']}: identifiers must be a dict")
            continue
        for k, v in ids.items():
            if k not in ALLOWED_IDENTIFIER_KEYS:
                errors.append(f"{c['name']}: identifier key {k!r} not in {sorted(ALLOWED_IDENTIFIER_KEYS)}")
                continue
            if k == "uen":
                if not isinstance(v, str) or not v.strip():
                    errors.append(f"{c['name']}: uen must be non-empty string")
            else:
                if not _is_url(v):
                    errors.append(f"{c['name']}: identifiers[{k}] must be http(s) URL, got {v!r}")
    assert not errors, "identifier violations:\n  - " + "\n  - ".join(errors)


# TODO: resolve the Unomove cluster (Chongqing + Suzhou sister entities sharing
# a brand with a separately-tracked NUS-GRIP UnoMove) and shrink this back to
# the empty set. Each tolerated token risks Layer 1 dedup misrouting.
ACCEPTED_TOKEN_COLLISIONS: set[str] = {"unomove"}


def test_no_case_insensitive_alias_collisions(companies: list[dict]) -> None:
    owner: dict[str, str] = {}
    errors: list[str] = []
    for c in companies:
        name = c["name"]
        tokens = [name] + list(c.get("aliases", []))
        seen_local: set[str] = set()
        for tok in tokens:
            key = tok.strip().lower()
            if not key:
                errors.append(f"{name}: empty token in name+aliases")
                continue
            if key in seen_local:
                errors.append(f"{name}: duplicate token within own aliases: {tok!r}")
                continue
            seen_local.add(key)
            if key in owner and owner[key] != name:
                if key in ACCEPTED_TOKEN_COLLISIONS:
                    continue
                errors.append(f"alias collision: {tok!r} claimed by {owner[key]!r} and {name!r}")
            else:
                owner[key] = name
    assert not errors, "alias collisions:\n  - " + "\n  - ".join(errors)


def test_unique_names(companies: list[dict]) -> None:
    seen: dict[str, int] = {}
    for c in companies:
        seen[c["name"]] = seen.get(c["name"], 0) + 1
    dups = [n for n, k in seen.items() if k > 1]
    assert not dups, f"duplicate company names: {dups}"
