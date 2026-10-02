"""Unit tests for scripts/discover_portfolio.py and merge_portfolio_entries.py
(fixture HTML, no network)."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def discover(scripts_module_loader):
    return scripts_module_loader("discover_portfolio")


@pytest.fixture(scope="module")
def merge_mod(scripts_module_loader):
    return scripts_module_loader("merge_portfolio_entries")


BLOCK71_HTML = """
<div class="logo-wall">
  <div data-logo-wall-item data-logo-index="0">
    <img src="/wp-media/2026/08/acme.webp" alt="ACME PAYMENTS PTE. LTD." class="object-contain" loading="lazy">
  </div>
  <div data-logo-wall-item data-logo-index="1">
    <img src="/wp-media/2026/08/carepal.webp" alt="CarePal" class="object-contain" loading="lazy">
  </div>
  <div data-logo-wall-item data-logo-index="2">
    <img src="/wp-media/2026/08/aipath.webp" alt="AIPath Visual Logo" class="object-contain" loading="lazy">
  </div>
  <div data-logo-wall-item data-logo-index="3">
    <img src="/wp-media/2026/08/placeholder.webp" alt="B71 US : Unknown Logo 1" class="object-contain" loading="lazy">
  </div>
</div>
"""

GRIP_MARKDOWN = """Title: NUS Graduate Research Innovation Programme

Markdown Content:
## Portfolio

## Our Portfolio

![Image 2](http://nus.edu.sg/grip/wp-content/uploads/WaveSense.jpg)

## WaveSense

###### SENSING THE FUTURE

WaveSense builds acoustic sensors for pipelines. It detects leaks early.

![Image 3](http://nus.edu.sg/grip/wp-content/uploads/more.jpg)

[Click here to find out more](http://nus.edu.sg/grip/wp-content/uploads/Run-9-Booklet.pdf)

## ArmasTec™

###### STRENGTH TO SPARE

Exosuits for industrial workers.

![Image 4](http://nus.edu.sg/grip/wp-content/uploads/armastec.jpg)
"""


def test_parse_block71_region(discover):
    cards = discover.parse_block71_region(BLOCK71_HTML, "BLOCK71 Singapore", "Singapore")
    assert [c["name"] for c in cards] == ["Acme Payments", "CarePal", "AIPath"]
    assert cards[0]["hub_label"] == "BLOCK71 Singapore"
    assert cards[0]["hub_country"] == "Singapore"
    assert cards[0]["industry"] is None


def test_parse_grip_portfolio(discover):
    ventures = discover.parse_grip_portfolio(GRIP_MARKDOWN)
    assert [v["name"] for v in ventures] == ["WaveSense", "ArmasTec™"]
    assert ventures[0]["grip_run"] == 9
    assert "acoustic sensors" in ventures[0]["description"]
    assert "Click here" not in ventures[0]["description"]
    assert "SENSING THE FUTURE" not in ventures[0]["description"]
    assert ventures[1]["grip_run"] is None


def test_norm_key_matches_spacing_and_trademark_variants(discover):
    assert discover.norm_key("ArmasTec™") == discover.norm_key("ARMAS TEC")
    assert discover.norm_key("Lexikat (Formerly Vox Dei)") == discover.norm_key("LEXIKAT")
    assert discover.norm_key("Hi-Transfer") == discover.norm_key("HiTransfer")


def test_find_new_excludes_overlaps_and_dedupes(discover):
    companies = [
        {"name": "ARMAS TEC", "aliases": []},
        {"name": "Other Co", "aliases": ["WaveSense"]},
    ]
    ventures = discover.parse_grip_portfolio(GRIP_MARKDOWN) + [
        {"source": "grip", "name": "Wave-Sense", "description": None, "grip_run": None},  # dup of alias
        {"source": "block71", "name": "Fresh Startup", "hub_label": "BLOCK71 Jakarta", "industry": None},
        {"source": "block71", "name": "FRESH STARTUP", "hub_label": "BLOCK71 Jakarta", "industry": None},  # dup within batch
    ]
    new = discover.find_new(companies, ventures)
    assert [v["name"] for v in new] == ["Fresh Startup"]


def test_find_new_containment_covers_dirty_legal_names(discover):
    companies = [
        {"name": "Doinn APAC", "aliases": []},
        {"name": "Goritax", "aliases": []},
        {"name": "Infinit Group", "aliases": []},
        {"name": "Arch", "aliases": []},  # short key: must NOT containment-match
    ]
    ventures = [
        {"source": "block71", "name": "Doinn Apac Pte Ltd Online Marketplace For Services",
         "hub_label": "BLOCK71 Singapore", "industry": None},
        {"source": "block71", "name": "PT Goritax Prospera Indonesia GORI-TAX",
         "hub_label": "BLOCK71 Jakarta", "industry": None},
        {"source": "block71", "name": "Infinit Singapore (INFINIT GROUP",
         "hub_label": "BLOCK71 Bandung", "industry": None},
        {"source": "block71", "name": "Archipelago Labs",
         "hub_label": "BLOCK71 Jakarta", "industry": None},
    ]
    new = discover.find_new(companies, ventures)
    assert [v["name"] for v in new] == ["Archipelago Labs"]


def test_find_new_skips_junk_names(discover):
    ventures = [
        {"source": "block71", "name": "Stealth", "hub_label": "BLOCK71 Silicon Valley", "industry": None},
        {"source": "block71", "name": "TBD", "hub_label": "BLOCK71 Tokyo", "industry": None},
    ]
    assert discover.find_new([], ventures) == []


def test_normalize_name_strips_comma_co_ltd(discover):
    assert discover.normalize_name("Chongqing FengKai Technology Co., Ltd.") == "Chongqing FengKai Technology"
    assert discover.normalize_name("Chongqing Lixing Biomaterial Co., Ltd. Ltd") == "Chongqing Lixing Biomaterial"


def test_draft_entry_shapes(discover):
    grip_entry = discover.draft_entry(
        {"source": "grip", "name": "WaveSense", "grip_run": 9,
         "description": "WaveSense builds acoustic sensors for pipelines. It detects leaks early."}
    )
    assert grip_entry["name"] == "WaveSense"
    assert grip_entry["description"].startswith("NUS GRIP-incubated Singapore deep-tech startup (Run 9).")
    assert "acoustic sensors" in grip_entry["description"]
    assert grip_entry["description"].endswith("Drop unrelated companies sharing the same name.")
    assert grip_entry["aliases"] == [] and grip_entry["funding_rounds"] == []

    b71_entry = discover.draft_entry(
        {"source": "block71", "name": "CarePal", "hub_label": "The Hangar (NUS Enterprise)", "industry": "health tech"}
    )
    assert b71_entry["description"] == (
        "The Hangar (NUS Enterprise) portfolio startup (health tech). "
        "Drop unrelated companies sharing the same name."
    )


def test_draft_entry_sets_country_for_hubs_without_country_in_label(discover):
    # "The Hangar" and "NUS Social Impact Hub" never spell out "Singapore" in
    # their label, so derive_country.py's regex can't infer a country from
    # the drafted description alone (github: portfolio-discovery run
    # 30689689605 failed test_every_company_has_country on exactly this).
    # draft_entry must set `country` explicitly so merged entries never rely
    # on that inference.
    hangar_entry = discover.draft_entry(
        {"source": "block71", "name": "CarePal", "hub_label": "The Hangar (NUS Enterprise)",
         "hub_country": "Singapore", "industry": "health tech"}
    )
    assert hangar_entry["country"] == "Singapore"

    jakarta_entry = discover.draft_entry(
        {"source": "block71", "name": "Fresh Startup", "hub_label": "BLOCK71 Jakarta",
         "hub_country": "Indonesia", "industry": None}
    )
    assert jakarta_entry["country"] == "Indonesia"

    grip_entry = discover.draft_entry(
        {"source": "grip", "name": "WaveSense", "grip_run": 9, "description": "Acoustic sensors."}
    )
    assert grip_entry["country"] == "Singapore"


def test_parse_block71_region_skips_placeholder_alts(discover):
    cards = discover.parse_block71_region(BLOCK71_HTML, "BLOCK71 USA", "United States")
    names = [c["name"] for c in cards]
    assert "B71 Us : Unknown" not in " ".join(names)  # "Unknown" placeholder dropped
    assert all(not n.lower().endswith("logo") for n in names)  # "… Logo" suffix stripped
    assert all(c["hub_country"] == "United States" for c in cards)


def test_first_sentences_trims_long_blurbs(discover):
    long = "First sentence here. " + "Second very long sentence " * 30
    out = discover.first_sentences(long, limit=60)
    assert out.startswith("First sentence here.")
    assert len(out) < 200


def test_merge_preserves_pinned_head_and_sorts(merge_mod):
    companies = [
        {"name": "Carousell"}, {"name": "Patsnap"}, {"name": "Horizon Quantum Computing"},
        {"name": "Beta Co"}, {"name": "Delta Co"},
    ]
    merged, added = merge_mod.merge(companies, [{"name": "Charlie Co"}, {"name": "beta co"}])
    assert added == 1  # "beta co" skipped as duplicate
    assert [c["name"] for c in merged] == [
        "Carousell", "Patsnap", "Horizon Quantum Computing",
        "Beta Co", "Charlie Co", "Delta Co",
    ]


def test_merge_noop_when_all_duplicates(merge_mod):
    companies = [{"name": "Carousell"}, {"name": "Patsnap"}, {"name": "Horizon Quantum Computing"}]
    merged, added = merge_mod.merge(companies, [{"name": "CAROUSELL"}])
    assert added == 0
    assert merged == companies
