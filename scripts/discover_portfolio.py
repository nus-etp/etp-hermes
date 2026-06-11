#!/usr/bin/env python3
"""Discover new BLOCK71 / NUS GRIP portfolio companies not yet in the watchlist.

Scrapes two public directories:
  - https://block71.co/startup/          (all BLOCK71 hubs incl. The Hangar,
                                          NUS@SSP, Social Impact Hub)
  - https://www.nus.edu.sg/grip/portfolio/  (NUS GRIP ventures)

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

BLOCK71_URL = "https://block71.co/startup/"
GRIP_URL = "https://www.nus.edu.sg/grip/portfolio/"

HUB_LABELS = {
    "block71-singapore": "BLOCK71 Singapore",
    "the-hangar": "The Hangar (NUS Enterprise)",
    "nus-enterprisesingapore-science-park": "NUS Enterprise @ Singapore Science Park",
    "social-impact-hub": "NUS Social Impact Hub",
    "block71-jakarta": "BLOCK71 Jakarta",
    "block71-saigon": "BLOCK71 Saigon",
    "block71-suzhou": "BLOCK71 Suzhou",
    "block71-chongqing": "BLOCK71 Chongqing",
    "block71-guangzhou": "BLOCK71 Guangzhou",
    "block71-silicon-valley": "BLOCK71 Silicon Valley",
    "block71-tokyo": "BLOCK71 Tokyo",
    "block71-yogyakarta": "BLOCK71 Yogyakarta",
    "select-block71-bandung-block71-bandung": "BLOCK71 Bandung",
}

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
TAG_STRIP = re.compile(r"<[^>]+>")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (etp-hermes portfolio-discovery)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
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


def text_of(fragment: str) -> str:
    return html.unescape(TAG_STRIP.sub(" ", fragment)).strip()


# --------------------------------------------------------------------------
# BLOCK71 directory (startup cards)
# --------------------------------------------------------------------------

def parse_block71_cards(src: str) -> list[dict]:
    pattern = re.compile(
        r'class="startup-card"\s+'
        r'data-industry="([^"]*)"\s+'
        r'data-location="([^"]*)"\s+'
        r'data-date="[^"]*"\s+'
        r'data-views="[^"]*">'
        r".*?"
        r'<h3 class="startup-name">([^<]+)</h3>',
        re.DOTALL,
    )
    out: list[dict] = []
    for industry, location, name in pattern.findall(src):
        raw_name = html.unescape(name).strip()
        out.append(
            {
                "source": "block71",
                "name": normalize_name(raw_name),
                "hub_label": HUB_LABELS.get(location, location),
                "industry": industry.replace("-", " ").strip() or None,
            }
        )
    return out


# --------------------------------------------------------------------------
# NUS GRIP portfolio (Essential-Addons lightboxes)
# --------------------------------------------------------------------------

def parse_grip_lightboxes(src: str) -> list[dict]:
    out: list[dict] = []
    blocks = re.split(r'<h2 class="eael-lightbox-title">', src)[1:]
    for block in blocks:
        name_match = re.match(r"([^<]+)</h2>", block)
        if not name_match:
            continue
        content_match = re.search(
            r'<div class="eael-lightbox-content">(.*?)</div>\s*</div>', block, re.DOTALL
        )
        content = content_match.group(1) if content_match else ""
        paras = [text_of(p) for p in re.findall(r"<p>(.*?)</p>", content, re.DOTALL)]
        paras = [p for p in paras if p and not p.lower().startswith("click here")]
        link_match = re.search(r'<a href="([^"]+)"[^>]*>\s*Click here', content)
        run_match = RUN_IN_URL.search(link_match.group(1) if link_match else "")
        out.append(
            {
                "source": "grip",
                "name": html.unescape(name_match.group(1)).strip(),
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
    else:
        industry = f" ({venture['industry']})" if venture.get("industry") else ""
        description = (
            f"{venture['hub_label']} portfolio startup{industry}. "
            "Drop unrelated companies sharing the same name."
        )
        notes = f"{venture['hub_label']} portfolio company. No publicly verifiable funding round announcements found."
    return {
        "name": venture["name"],
        "aliases": [],
        "description": description,
        "funding_rounds": [],
        "funding_notes": notes,
    }


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
    block71 = parse_block71_cards(fetch(BLOCK71_URL))
    grip = parse_grip_lightboxes(fetch(GRIP_URL))
    if not block71:
        print(f"FATAL: zero startup cards parsed from {BLOCK71_URL}; markup changed?", file=sys.stderr)
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
