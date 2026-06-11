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
<div class="startup-card" data-industry="fintech" data-location="block71-singapore" data-date="2026-01-01" data-views="12">
  <div class="startup-logo"></div>
  <div class="startupdetails">
    <h3 class="startup-name">ACME PAYMENTS PTE. LTD.</h3>
    <a href="https://block71.co/directory/startups/acme-payments/" class="view-btn">View</a>
  </div>
</div>
<div class="startup-card" data-industry="health-tech" data-location="the-hangar" data-date="2026-02-01" data-views="3">
  <div class="startupdetails">
    <h3 class="startup-name">CarePal</h3>
    <a href="https://block71.co/directory/startups/carepal/" class="view-btn">View</a>
  </div>
</div>
"""

GRIP_HTML = """
<h2 class="eael-lightbox-title">WaveSense</h2>
<div class="eael-lightbox-content"><h6 class="uk-text-meta">SENSING THE FUTURE</h6>
<div><p>WaveSense builds acoustic sensors for pipelines. It detects leaks early.</p></div>
<p><a href="http://nus.edu.sg/grip/wp-content/uploads/Run-9-Booklet.pdf">Click here to find out more</a></p></div>
</div>
<h2 class="eael-lightbox-title">ArmasTec™</h2>
<div class="eael-lightbox-content"><div><p>Exosuits for industrial workers.</p></div></div>
</div>
"""


def test_parse_block71_cards(discover):
    cards = discover.parse_block71_cards(BLOCK71_HTML)
    assert [c["name"] for c in cards] == ["Acme Payments", "CarePal"]
    assert cards[0]["hub_label"] == "BLOCK71 Singapore"
    assert cards[0]["industry"] == "fintech"
    assert cards[1]["hub_label"] == "The Hangar (NUS Enterprise)"


def test_parse_grip_lightboxes(discover):
    ventures = discover.parse_grip_lightboxes(GRIP_HTML)
    assert [v["name"] for v in ventures] == ["WaveSense", "ArmasTec™"]
    assert ventures[0]["grip_run"] == 9
    assert "acoustic sensors" in ventures[0]["description"]
    assert "Click here" not in ventures[0]["description"]
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
    ventures = discover.parse_grip_lightboxes(GRIP_HTML) + [
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
