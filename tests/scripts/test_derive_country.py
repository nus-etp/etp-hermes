"""Unit tests for scripts/derive_country.py and the country auto-fill in
scripts/merge_portfolio_entries.py (pure regex, no network/LLM)."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def derive_mod(scripts_module_loader):
    return scripts_module_loader("derive_country")


@pytest.fixture(scope="module")
def merge_mod(scripts_module_loader):
    return scripts_module_loader("merge_portfolio_entries")


@pytest.mark.parametrize(
    "desc,expected",
    [
        ("Singapore-headquartered marketplace platform.", "Singapore"),
        ("NUS GRIP-incubated Singapore deep-tech startup.", "Singapore"),
        ("BLOCK71 Saigon-resident edtech startup.", "Vietnam"),
        ("BLOCK71 Jakarta-resident programmatic platform.", "Indonesia"),
        ("BLOCK71 Chongqing-listed startup.", "China"),
        ("BLOCK71 Silicon Valley-resident SaaS startup.", "United States"),
        ("BLOCK71 Tokyo-resident startup.", "Japan"),
        ("Vietnamese edtech startup founded 2017.", "Vietnam"),
    ],
)
def test_derive_basic(derive_mod, desc, expected):
    country, _ = derive_mod.derive(desc)
    assert country == expected


def test_explicit_hq_overrides_hub_residency(derive_mod):
    # Resident at one BLOCK71 hub, headquartered in another country.
    otrafy = "BLOCK71 Saigon-resident enterprise-SaaS startup (HQ Vancouver / ops Saigon)."
    taidii = "BLOCK71 Suzhou-listed Singapore childcare SaaS (HQ Singapore, Suzhou ops)."
    assert derive_mod.derive(otrafy)[0] == "Canada"
    assert derive_mod.derive(taidii)[0] == "Singapore"


def test_unresolvable_returns_none(derive_mod):
    country, evidence = derive_mod.derive("A startup with no location cues whatsoever.")
    assert country is None and evidence is None


def test_merge_fills_country_after_description(merge_mod):
    new = [{
        "name": "Zeta Example",
        "aliases": [],
        "description": "NUS GRIP-incubated Singapore deep-tech startup.",
        "funding_rounds": [],
        "funding_notes": "n/a",
    }]
    merged, added = merge_mod.merge([], new)
    assert added == 1
    entry = merged[0]
    assert entry["country"] == "Singapore"
    # placed immediately after description for file-convention consistency
    keys = list(entry)
    assert keys[keys.index("description") + 1] == "country"


def test_merge_preserves_existing_country(merge_mod):
    new = [{
        "name": "Preset Co",
        "description": "Singapore-based startup.",
        "country": "Malaysia",  # explicitly set, must not be overwritten
    }]
    merged, _ = merge_mod.merge([], new)
    assert merged[0]["country"] == "Malaysia"
