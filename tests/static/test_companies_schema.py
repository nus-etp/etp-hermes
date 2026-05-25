"""Schema and invariants for data/companies.json."""

from __future__ import annotations

import re
from urllib.parse import urlparse

ALLOWED_SOURCE_TYPES = {"rss", "github_org", "lever_jobs", "html_scrape"}
ALLOWED_IDENTIFIER_KEYS = {"crunchbase", "linkedin", "nus_cde", "sginnovate", "uen"}
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
            if not _is_url(s.get("url")):
                errors.append(f"{name}: sources[{i}] url not a valid http(s) URL: {s.get('url')!r}")
            label = s.get("label")
            if label is not None and not isinstance(label, str):
                errors.append(f"{name}: sources[{i}] label must be str when present")
    assert not errors, "schema violations:\n  - " + "\n  - ".join(errors)


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
