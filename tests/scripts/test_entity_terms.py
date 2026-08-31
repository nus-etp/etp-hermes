"""Unit tests for scripts/entity_terms.py — the weak/strong term classifier."""

from __future__ import annotations

import pytest


@pytest.fixture()
def et(scripts_module_loader):
    return scripts_module_loader("entity_terms")


# --- classifier ---------------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "arch",  # 4 chars — the stablecoin false positive
        "ava",  # 3 chars — the Anthropic/Claude false positive
        "amble",  # 5 chars — the SpaceX IPO false positive
        "finan",  # 5 chars
        "bae",
        "alpha",
        "assist",  # 6 chars but a common English word
        "emerge",
        "venture",
        "quantum",
    ],
)
def test_weak_terms(et, term: str) -> None:
    assert et.term_is_strong(term) is False


@pytest.mark.parametrize(
    "term",
    [
        "assist.id",  # dot
        "x0pa",  # digit
        "bright sight",  # multi-word
        "patsnap",  # long enough
        "horizon quantum computing",
        "carousell",
    ],
)
def test_strong_terms(et, term: str) -> None:
    assert et.term_is_strong(term) is True


def test_classifier_is_case_and_whitespace_insensitive(et) -> None:
    assert et.term_is_strong("  Bright   Sight ") is True
    assert et.term_is_strong(" ARCH ") is False
    assert et.term_is_strong("") is False


def test_curated_match_terms_are_strong_but_never_dictionary_words(et) -> None:
    # A curated founder/product term is trusted even when short...
    assert et.term_is_strong("zeno", curated=True) is True
    assert et.term_is_strong("zeno") is False
    # ...but a generator proposing a bare English word must not upgrade it.
    assert et.term_is_strong("assist", curated=True) is False


def test_company_terms_split(et) -> None:
    strong, weak, exclude = et.company_terms(
        {
            "name": "Assist.id",
            "aliases": ["Assist", "AssistID"],
            "match_terms": ["Jean Paul", "assist.id"],
            "exclude_terms": ["Siri"],
        }
    )
    assert "assist.id" in strong and "assistid" in strong
    assert "jean paul" in strong
    assert weak == ["assist"]
    assert exclude == ["siri"]


# --- acceptance rule ----------------------------------------------------------


def _matcher(et, **company):
    company.setdefault("aliases", [])
    return et.build_company_matchers([company])[company["name"]]


def test_weak_term_matches_title_only(et) -> None:
    m = _matcher(et, name="Arch")
    assert et.match_kind(m, "Arch raises $2M seed", "") == et.MATCH_WEAK_TITLE
    # Weak term in the description only → the suppressed class.
    assert (
        et.match_kind(m, "Japan's largest banks to jointly issue stablecoins", "arch of the bridge")
        == et.MATCH_WEAK_DESC
    )
    assert et.match_kind(m, "Unrelated headline", "unrelated body") == et.MATCH_NONE


def test_strong_term_matches_description(et) -> None:
    m = _matcher(et, name="Acme Robotics", aliases=["Acme"])
    assert (
        et.match_kind(m, "Weekly funding roundup", "Acme Robotics closed a bridge round")
        == et.MATCH_STRONG
    )


def test_match_terms_add_founder_recall(et) -> None:
    m = _matcher(et, name="Akro", match_terms=["Jane Q Founder"])
    assert (
        et.match_kind(m, "Ten founders to watch", "Jane Q Founder is building sensors")
        == et.MATCH_STRONG
    )


def test_whole_word_matching_survives(et) -> None:
    m = _matcher(et, name="Arch")
    assert et.match_kind(m, "New research lab opens") == et.MATCH_NONE
    assert et.match_kind(m, "Arch ships v2") == et.MATCH_WEAK_TITLE


def test_is_excluded(et) -> None:
    m = _matcher(et, name="Horizon Quantum Computing", exclude_terms=["nasdaq", "ticker"])
    assert et.is_excluded(m, "Horizon Quantum Computing lists on NASDAQ", "") is True
    assert et.is_excluded(m, "Horizon Quantum Computing hires CTO", "") is False
    # Whole-word: "nasdaq" must not fire on a glued token.
    assert et.is_excluded(m, "xnasdaqy news", "") is False
