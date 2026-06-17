#!/usr/bin/env python3
"""Derive an HQ `country` tag for each company in data/companies.json.

The portfolio descriptions encode HQ location in a small set of structured
phrasings (e.g. "Singapore-headquartered", "BLOCK71 Saigon-resident",
"NUS GRIP-incubated Singapore deep-tech startup"). This script resolves a
country from those cues with an explicit priority order, writes the field
back into companies.json (preserving key order), and emits an audit list of
(name, country, evidence) so the inference is reviewable.

Run: python3 scripts/derive_country.py [--write]
Without --write it only reports; with --write it updates companies.json.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "data" / "companies.json"

# City / region token -> country. Lower-cased keys.
CITY_COUNTRY = {
    "singapore": "Singapore",
    "sg": "Singapore",
    "ssp": "Singapore",          # Singapore Science Park (NUS Enterprise@SSP)
    "nus": "Singapore",          # National University of Singapore
    "jakarta": "Indonesia",
    "bandung": "Indonesia",
    "yogyakarta": "Indonesia",
    "surabaya": "Indonesia",
    "saigon": "Vietnam",
    "hcmc": "Vietnam",
    "hanoi": "Vietnam",
    "chongqing": "China",
    "suzhou": "China",
    "guangzhou": "China",
    "shenzhen": "China",
    "shanghai": "China",
    "beijing": "China",
    "tokyo": "Japan",
    "hong kong": "Hong Kong",
    "silicon valley": "United States",
    "vancouver": "Canada",
    "swiss": "Switzerland",
}

# Country name / nationality adjective -> canonical country. Lower-cased.
COUNTRY_WORDS = {
    "singapore": "Singapore", "singaporean": "Singapore",
    "vietnam": "Vietnam", "vietnamese": "Vietnam",
    "indonesia": "Indonesia", "indonesian": "Indonesia",
    "malaysia": "Malaysia", "malaysian": "Malaysia",
    "philippine": "Philippines", "philippines": "Philippines", "filipino": "Philippines",
    "thailand": "Thailand", "thai": "Thailand",
    "china": "China", "chinese": "China",
    "hong kong": "Hong Kong",
    "taiwan": "Taiwan", "taiwanese": "Taiwan",
    "india": "India", "indian": "India",
    "japan": "Japan", "japanese": "Japan",
    "korea": "South Korea", "korean": "South Korea",
    "australia": "Australia", "australian": "Australia",
    "switzerland": "Switzerland", "swiss": "Switzerland",
    "united states": "United States", "u.s.": "United States",
    "american": "United States",
    "united kingdom": "United Kingdom", "british": "United Kingdom",
    "cambodia": "Cambodia", "cambodian": "Cambodia",
    "myanmar": "Myanmar", "laos": "Laos", "brunei": "Brunei",
    "france": "France", "french": "France",
    "germany": "Germany", "german": "Germany",
    "canada": "Canada", "canadian": "Canada",
}

# HQ-strength suffixes attached to a location token (X-headquartered etc.).
HQ_SUFFIX = r"(?:headquartered|based|resident|listed|founded|incorporated|registered|affiliated)"


def _match_token(token, table):
    return table.get(token.strip().lower())


def derive(desc):
    """Return (country, evidence) or (None, None)."""
    # 0. Explicit "HQ <Place>" — overrides BLOCK71 hub *residency*, since a
    #    company can be resident at one hub while headquartered elsewhere
    #    (e.g. Otrafy: "Saigon-resident … HQ Vancouver").
    for m in re.finditer(r"HQ ([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)", desc):
        tok = m.group(1)
        c = _match_token(tok, COUNTRY_WORDS) or _match_token(tok, CITY_COUNTRY)
        if c:
            return c, m.group(0)

    # 1. "<Location>-<hqsuffix>" — strongest HQ signal (incl. BLOCK71 hubs).
    for m in re.finditer(rf"([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)-{HQ_SUFFIX}", desc):
        tok = m.group(1)
        c = _match_token(tok, CITY_COUNTRY) or _match_token(tok, COUNTRY_WORDS)
        if c:
            return c, m.group(0)

    # 2. "headquartered/based in <Place>".
    for m in re.finditer(rf"(?:headquartered|based|registered) in (?:the )?([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)", desc):
        tok = m.group(1)
        c = _match_token(tok, COUNTRY_WORDS) or _match_token(tok, CITY_COUNTRY)
        if c:
            return c, m.group(0)

    # 3. Nationality adjective / country word immediately qualifying the company
    #    e.g. "Singapore deep-tech startup", "Vietnamese edtech startup".
    for m in re.finditer(
        r"\b([A-Z][A-Za-z.]+(?: [A-Z][A-Za-z]+)?)\b(?=\s+(?:deep-tech|deeptech|edtech|fintech|biotech|medtech|agritech|proptech|cleantech|healthtech|startup|start-up|company|firm|scale-up|venture))",
        desc,
    ):
        c = _match_token(m.group(1), COUNTRY_WORDS)
        if c:
            return c, m.group(0)

    # 4. Last resort: first country word appearing anywhere in the description.
    best = None
    # Sort multi-word keys first so "hong kong" beats a stray "kong".
    for word in sorted(COUNTRY_WORDS, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(word)}\b", desc, re.IGNORECASE)
        if m and (best is None or m.start() < best[1]):
            best = (COUNTRY_WORDS[word], m.start(), desc[max(0, m.start() - 15): m.start() + 25])
    if best:
        return best[0], f"…{best[2].strip()}…"

    return None, None


def main():
    write = "--write" in sys.argv
    companies = json.loads(COMPANIES.read_text())
    resolved = 0
    unresolved = []
    for c in companies:
        country, _ = derive(c.get("description", ""))
        if country:
            resolved += 1
            if write:
                # Insert `country` right after `description` for readability.
                new = {}
                for k, v in c.items():
                    new[k] = v
                    if k == "description":
                        new["country"] = country
                if "country" not in new:
                    new["country"] = country
                c.clear()
                c.update(new)
        else:
            unresolved.append(c["name"])

    print(f"resolved: {resolved}/{len(companies)}")
    print(f"unresolved ({len(unresolved)}): {unresolved}")

    if write:
        COMPANIES.write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {COMPANIES}")

    # Country distribution for sanity.
    from collections import Counter
    dist = Counter()
    for c in companies:
        co = c.get("country") if write else derive(c.get("description", ""))[0]
        dist[co or "UNRESOLVED"] += 1
    print("distribution:", dict(dist.most_common()))


if __name__ == "__main__":
    main()
