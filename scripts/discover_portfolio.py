#!/usr/bin/env python3
"""Discover new BLOCK71 / NUS GRIP portfolio companies not yet in the watchlist.

Scrapes two public directories:
  - https://nusx.edu.sg/startups/b71/<region>/  (BLOCK71 hub showcases: the
                                          per-region logo walls — Singapore,
                                          Indonesia, Vietnam, China, Japan, USA)
  - https://www.nus.edu.sg/grip/portfolio/  (NUS GRIP ventures)

Both upstreams were reworked in 2026: block71.co became a client-rendered SPA
that serves the same shell for every path (no scrapable directory), so the
BLOCK71 companies are now read from the server-rendered logo walls on
nusx.edu.sg instead; and the GRIP portfolio page went behind Incapsula bot
protection (a 212-byte JS challenge to urllib), so it is fetched through
r.jina.ai — the same gated/unreachable-host fallback the collector uses.

Cross-checks every venture against data/companies.json names + aliases using a
normalised key (case/spacing/hyphen/trademark-insensitive), drafts a watchlist
entry for each genuinely new venture, and writes them to
data/portfolio-new-entries.json (gitignored) for scripts/merge_portfolio_entries.py.

Run by .github/workflows/portfolio-discovery.yml monthly; the drafted
descriptions are deliberately conservative ("drop unrelated companies sharing
the same name") — sharpen disambiguation by hand in the PR review when a name
is generic.

Fails loud (exit 1) when either directory parses to zero ventures: that means
the markup changed and silent success would look like "no new companies".
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_JSON = REPO_ROOT / "data" / "companies.json"
OUT_JSON = REPO_ROOT / "data" / "portfolio-new-entries.json"

GRIP_URL = "https://www.nus.edu.sg/grip/portfolio/"
JINA_PREFIX = "https://r.jina.ai/"

# BLOCK71 hub region pages on nusx.edu.sg. block71.co itself is a client-rendered
# SPA (serves the same shell for every path), so the companies are read from the
# server-rendered logo walls here instead. Each entry: (slug, hub_label, country).
# The hub_label sets the drafted description/notes; the country is set explicitly
# because the regional labels don't spell out their country, so
# scripts/derive_country.py's regex heuristic can't infer it from the blurb (see
# tests/static/test_companies_schema.py's test_every_company_has_country).
BLOCK71_REGIONS = [
    ("singapore", "BLOCK71 Singapore", "Singapore"),
    ("indonesia", "BLOCK71 Indonesia", "Indonesia"),
    ("vietnam", "BLOCK71 Vietnam", "Vietnam"),
    ("china", "BLOCK71 China", "China"),
    ("japan", "BLOCK71 Japan", "Japan"),
    ("usa", "BLOCK71 USA", "United States"),
]


def block71_region_url(slug: str) -> str:
    return f"https://nusx.edu.sg/startups/b71/{slug}/"

LEGAL_SUFFIX = re.compile(
    r"\s*[,(]?\s*"
    r"(pte\.?\s*ltd\.?|pte\.?\s*ltd\.|private\s+limited|"
    r"co\.?,?\s*ltd\.?|company\s+limited|ltd\.?|"
    r"inc\.?|incorporated|llc|l\.l\.c\.|llp|"
    r"sdn\.?\s*bhd\.?|gmbh|s\.a\.|ag|kk|k\.k\.|"
    r"limited)"
    r"\s*[)]?\s*$",
    re.IGNORECASE,
)

RUN_IN_URL = re.compile(r"[Rr]un[-_ ]?(\d+)")


def fetch(url: str, *, via_jina: bool = False) -> str:
    target = f"{JINA_PREFIX}{url}" if via_jina else url
    req = urllib.request.Request(target, headers={"User-Agent": "Mozilla/5.0 (portfolio-discovery)"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------

def normalize_name(raw: str) -> str:
    """Drop trailing legal-entity suffixes and title-case obvious ALL-CAPS names."""
    name = raw.strip()
    while True:
        new = LEGAL_SUFFIX.sub("", name).strip().rstrip(",")
        if new == name:
            break
        name = new
    letters = "".join(c for c in name if c.isalpha())
    if letters and letters == letters.upper() and len(letters) > 3:
        name = title_case_keepers(name)
    return name


def title_case_keepers(s: str) -> str:
    """Title-case but preserve obvious initialisms (AI, IT, IoT, NUS, etc.)."""
    keepers = {"AI", "IT", "IOT", "NUS", "SG", "API", "AR", "VR", "XR", "EV", "ML", "RX", "DC", "HR", "PR", "QA", "QC", "CS", "DNA", "RNA", "USA", "UK", "EU", "ASEAN", "II", "III", "IV", "VI"}
    words = re.split(r"(\s+|[-/])", s)
    out: list[str] = []
    for w in words:
        if not w or w.isspace() or w in "-/":
            out.append(w)
            continue
        out.append(w.upper() if w.upper() in keepers else w.capitalize())
    return "".join(out)


def norm_key(s: str) -> str:
    """Overlap key: 'ArmasTec™' == 'ARMAS TEC', 'Lexikat (Formerly Vox Dei)' == 'LEXIKAT'."""
    s = re.sub(r"\(.*?\)|™|®", "", s).lower()
    return re.sub(r"[^a-z0-9]", "", s)


# --------------------------------------------------------------------------
# BLOCK71 hub showcases (per-region logo walls)
# --------------------------------------------------------------------------

# Logo-wall alts that are placeholders, not company names.
LOGO_WALL_JUNK = re.compile(r"\bunknown\b|^b71\b", re.IGNORECASE)
# Trailing cruft some alts carry: "AIPath Visual Logo" → "AIPath".
LOGO_WALL_SUFFIX = re.compile(r"\s*(visual\s+)?logo\s*$", re.IGNORECASE)


def parse_block71_region(src: str, hub_label: str, country: str) -> list[dict]:
    pattern = re.compile(
        r'data-logo-wall-item[^>]*>\s*<img[^>]*\balt="([^"]*)"',
        re.DOTALL,
    )
    out: list[dict] = []
    for alt in pattern.findall(src):
        raw = LOGO_WALL_SUFFIX.sub("", html.unescape(alt)).strip()
        if not raw or LOGO_WALL_JUNK.search(raw):
            continue
        out.append(
            {
                "source": "block71",
                "name": normalize_name(raw),
                "hub_label": hub_label,
                "hub_country": country,
                "industry": None,
            }
        )
    return out


# --------------------------------------------------------------------------
# NUS GRIP portfolio (r.jina.ai markdown — the page is Incapsula-gated)
# --------------------------------------------------------------------------

# Non-company section headings in the GRIP portfolio markdown (page chrome, not
# ventures). Compared case-insensitively.
GRIP_HEADING_SKIP = {
    "portfolio",
    "solving problems with research",
    "our portfolio",
    "access our world-class deal flow",
}


def parse_grip_portfolio(md: str) -> list[dict]:
    out: list[dict] = []
    blocks = re.split(r"^##\s+", md, flags=re.MULTILINE)[1:]
    for block in blocks:
        name_line, _, rest = block.partition("\n")
        name = name_line.strip()
        if not name or name.lower() in GRIP_HEADING_SKIP:
            continue
        # The venture's blurb runs until its first image; the leading
        # "###### TAGLINE" line and the "Click here to find out more" link are
        # dropped, matching the old lightbox parser's output.
        body = rest.split("\n![", 1)[0]
        run_match = RUN_IN_URL.search(rest)
        paras: list[str] = []
        for para in re.split(r"\n\s*\n", body):
            text = " ".join(para.split())
            if not text or text.startswith("#") or text.lower().startswith("click here"):
                continue
            paras.append(text)
        out.append(
            {
                "source": "grip",
                "name": name,
                "description": "\n\n".join(paras) or None,
                "grip_run": int(run_match.group(1)) if run_match else None,
            }
        )
    return out


# --------------------------------------------------------------------------
# Drafting watchlist entries
# --------------------------------------------------------------------------

def first_sentences(text: str, limit: int = 240) -> str:
    """First sentence(s) of a scraped blurb, trimmed to roughly `limit` chars."""
    flat = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+", flat)
    out = ""
    for s in sentences:
        if out and len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
        if len(out) >= limit:
            break
    return out or flat[:limit]


def draft_entry(venture: dict) -> dict:
    if venture["source"] == "grip":
        run = f" (Run {venture['grip_run']})" if venture.get("grip_run") else ""
        blurb = first_sentences(venture["description"]) + " " if venture.get("description") else ""
        description = (
            f"NUS GRIP-incubated Singapore deep-tech startup{run}. {blurb}"
            "Drop unrelated companies sharing the same name."
        )
        notes = "NUS GRIP portfolio company. No publicly verifiable funding round announcements found."
        country = "Singapore"
    else:
        industry = f" ({venture['industry']})" if venture.get("industry") else ""
        description = (
            f"{venture['hub_label']} portfolio startup{industry}. "
            "Drop unrelated companies sharing the same name."
        )
        notes = f"{venture['hub_label']} portfolio company. No publicly verifiable funding round announcements found."
        country = venture.get("hub_country")
    entry = {
        "name": venture["name"],
        "aliases": [],
        "description": description,
    }
    if country:
        entry["country"] = country
    entry["funding_rounds"] = []
    entry["funding_notes"] = notes
    return entry


# Directory entries that aren't real company names
JUNK_NAMES = {"stealth", "tbd", "tba", "na", "confidential"}

# Minimum key length for containment matching, so short existing keys
# ("otrafy", "goritax") still match their dirty directory variants without
# 4-char keys like "arch" matching everything.
CONTAINMENT_MIN = 6


def find_new(companies: list[dict], ventures: list[dict]) -> list[dict]:
    """Ventures with no existing companies.json entry.

    Overlap is containment-based, not just exact: the directories carry dirty
    legal names ("Doinn Apac Pte Ltd Online Marketplace For Services") whose
    watchlist entries were hand-cleaned ("Doinn APAC"), so a venture also
    counts as covered when its normalised key contains — or is contained in —
    an existing key of CONTAINMENT_MIN+ chars. Conservative by design: a
    genuinely new company whose name embeds an existing one is treated as
    covered rather than risking duplicate watchlist entries.
    """
    keys: set[str] = set()
    for c in companies:
        for candidate in [c["name"], *(c.get("aliases") or [])]:
            k = norm_key(candidate)
            if k:
                keys.add(k)
    long_keys = [k for k in keys if len(k) >= CONTAINMENT_MIN]

    def covered(key: str) -> bool:
        if key in keys:
            return True
        return any(
            (e in key) or (len(key) >= CONTAINMENT_MIN and key in e)
            for e in long_keys
        )

    new: list[dict] = []
    seen: set[str] = set()
    for v in ventures:
        key = norm_key(v["name"])
        if not key or key in JUNK_NAMES or key in seen or covered(key):
            continue
        seen.add(key)
        new.append(v)
    return new


def main() -> int:
    block71: list[dict] = []
    for slug, hub_label, country in BLOCK71_REGIONS:
        block71 += parse_block71_region(fetch(block71_region_url(slug)), hub_label, country)
    grip = parse_grip_portfolio(fetch(GRIP_URL, via_jina=True))
    if not block71:
        print("FATAL: zero companies parsed from BLOCK71 region logo walls; markup changed?", file=sys.stderr)
        return 1
    if not grip:
        print(f"FATAL: zero ventures parsed from {GRIP_URL}; markup changed?", file=sys.stderr)
        return 1
    companies = json.loads(COMPANIES_JSON.read_text())
    new = find_new(companies, block71 + grip)
    entries = [draft_entry(v) for v in new]
    OUT_JSON.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    print(f"BLOCK71 directory: {len(block71)} ventures; GRIP portfolio: {len(grip)} ventures")
    print(f"New (not in companies.json): {len(entries)}")
    for v in new:
        origin = v.get("hub_label") or f"GRIP Run {v.get('grip_run') or '?'}"
        print(f"  + {v['name']}  [{origin}]")
    print(f"Wrote {OUT_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
