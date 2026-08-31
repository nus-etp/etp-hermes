"""Unit tests for scripts/generate_match_terms.py (mocked LLM, no network)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def gm(scripts_module_loader):
    return scripts_module_loader("generate_match_terms")


def _chat(reply: str | None):
    """Fake ab_llm.chat returning a canned reply (None = fail-open path)."""

    def chat(messages, **kwargs):  # noqa: ARG001
        return reply

    return chat


# --- deterministic domain extraction -----------------------------------------


def test_homepage_domains_from_sources_and_identifiers(gm) -> None:
    company = {
        "name": "Assist.id",
        "sources": [
            {"type": "html_scrape", "url": "https://assist.id/news"},
            {"type": "github_org", "url": "https://github.com/assistid.atom"},
            {"type": "lever_jobs", "url": "https://api.lever.co/v0/postings/assistid?mode=json"},
        ],
        "identifiers": {
            "crunchbase": "https://www.crunchbase.com/organization/assist-id",
            "linkedin": "https://www.linkedin.com/company/assistid",
        },
    }
    assert gm.homepage_domains(company) == ["assist.id"]


def test_homepage_domains_strips_brandless_subdomains_and_dedupes(gm) -> None:
    company = {
        "name": "Horizon Quantum Computing",
        "sources": [
            {"type": "html_scrape", "url": "https://www.horizonquantum.com/resources/newsroom"},
            {"type": "rss", "url": "https://horizonquantum.com/feed"},
            {"type": "rss", "url": "https://arxiv.org/a/fitzsimons_j_1.atom2"},
        ],
    }
    assert gm.homepage_domains(company) == ["horizonquantum.com"]
    # "press.carousell.com" never shows up in prose; "carousell.com" might.
    assert gm.homepage_domains(
        {"name": "Carousell", "sources": [{"type": "rss", "url": "https://press.carousell.com/feed"}]}
    ) == ["carousell.com"]
    # A brand-bearing sub-domain is kept as-is.
    assert gm.homepage_domains(
        {"name": "Seamless", "sources": [{"type": "rss", "url": "https://sg.seamless.example/rss"}]}
    ) == ["sg.seamless.example"]


def test_homepage_domains_empty_without_own_urls(gm) -> None:
    assert gm.homepage_domains({"name": "Akro"}) == []


# --- LLM pass ----------------------------------------------------------------


def test_propose_merges_llm_terms_with_domains(gm) -> None:
    company = {
        "name": "Arch",
        "aliases": [],
        "description": "SG fintech founded by Jane Q Founder.",
        "sources": [{"type": "rss", "url": "https://archfin.sg/feed"}],
    }
    reply = json.dumps(
        {
            "match_terms": ["Jane Q Founder", "ArchPay", "Arch"],  # "Arch" already known → dropped
            "exclude_terms": ["gothic arch", "arch linux"],
        }
    )
    entry = gm.propose_for_company(company, generic=True, use_llm=True, chat=_chat(reply))
    assert entry["name"] == "Arch"
    assert entry["match_terms"] == ["archfin.sg", "Jane Q Founder", "ArchPay"]
    assert entry["exclude_terms"] == ["gothic arch", "arch linux"]


def test_propose_drops_bare_dictionary_match_terms(gm) -> None:
    company = {"name": "Assist.id", "aliases": ["Assist"], "description": "clinic SaaS"}
    reply = json.dumps({"match_terms": ["assist", "platform", "Dr Amelia Tan"], "exclude_terms": []})
    entry = gm.propose_for_company(company, generic=True, use_llm=True, chat=_chat(reply))
    # "assist" is an existing alias AND a dictionary word; "platform" is a
    # dictionary word — only the founder name survives.
    assert entry["match_terms"] == ["Dr Amelia Tan"]
    assert "exclude_terms" not in entry


def test_propose_fails_open_on_llm_error(gm) -> None:
    company = {"name": "Akro", "aliases": [], "sources": [{"type": "rss", "url": "https://akro.sg/f"}]}
    entry = gm.propose_for_company(company, generic=True, use_llm=True, chat=_chat(None))
    assert entry == {"name": "Akro", "match_terms": ["akro.sg"]}


def test_propose_fails_open_on_unparseable_reply(gm) -> None:
    company = {"name": "Akro", "aliases": []}
    entry = gm.propose_for_company(company, generic=True, use_llm=True, chat=_chat("sorry, no"))
    assert entry is None  # nothing deterministic either → skipped entirely


def test_propose_caps_term_counts(gm) -> None:
    company = {"name": "Koel", "aliases": []}
    reply = json.dumps(
        {
            "match_terms": [f"Founder Number {i}" for i in range(20)],
            "exclude_terms": [f"other thing {i}" for i in range(20)],
        }
    )
    entry = gm.propose_for_company(company, generic=True, use_llm=True, chat=_chat(reply))
    assert len(entry["match_terms"]) == gm.MAX_MATCH_TERMS
    assert len(entry["exclude_terms"]) == gm.MAX_EXCLUDE_TERMS


# --- selection ---------------------------------------------------------------


COMPANIES = [
    {"name": "Arch", "aliases": []},  # weak name → generic
    {"name": "Patsnap", "aliases": []},  # strong name
    {"name": "amble", "aliases": []},  # weak name → generic
]


def test_select_generic_only_and_limit(gm) -> None:
    assert [c["name"] for c in gm.select_companies(COMPANIES, only=None, generic_only=True, limit=None)] == [
        "Arch",
        "amble",
    ]
    assert [c["name"] for c in gm.select_companies(COMPANIES, only=None, generic_only=False, limit=2)] == [
        "Arch",
        "Patsnap",
    ]
    assert [c["name"] for c in gm.select_companies(COMPANIES, only="snap", generic_only=False, limit=None)] == [
        "Patsnap"
    ]


def test_generate_no_llm_is_domains_only(gm) -> None:
    companies = [
        {"name": "Arch", "aliases": [], "sources": [{"type": "rss", "url": "https://archfin.sg/f"}]},
        {"name": "Patsnap", "aliases": []},
    ]

    def boom(messages, **kwargs):  # noqa: ARG001
        raise AssertionError("the LLM must not be called with use_llm=False")

    draft = gm.generate(companies, use_llm=False, chat=boom)
    assert draft["llm"] is False
    assert draft["entries"] == [{"name": "Arch", "match_terms": ["archfin.sg"]}]


# --- merge -------------------------------------------------------------------


def test_merge_unions_and_places_new_fields(gm) -> None:
    companies = [
        {"name": "Arch", "aliases": ["archsg"], "description": "d", "country": "Singapore"},
        {"name": "Patsnap", "aliases": [], "description": "d", "country": "Singapore"},
    ]
    draft = {"entries": [{"name": "Arch", "match_terms": ["archfin.sg"], "exclude_terms": ["arch linux"]}]}
    merged, changed = gm.merge(companies, draft)
    assert changed == 1
    # New keys land right after `aliases`, keeping the rest of the order intact.
    assert list(merged[0]) == ["name", "aliases", "match_terms", "exclude_terms", "description", "country"]
    assert merged[0]["match_terms"] == ["archfin.sg"]
    assert merged[1] == companies[1]  # untouched


def test_merge_is_a_union_not_a_replace(gm) -> None:
    companies = [{"name": "Arch", "aliases": [], "match_terms": ["archfin.sg"], "exclude_terms": ["ticker"]}]
    draft = {
        "entries": [
            {"name": "Arch", "match_terms": ["ARCHFIN.SG", "Jane Q Founder"], "exclude_terms": ["ticker"]}
        ]
    }
    merged, changed = gm.merge(companies, draft)
    assert changed == 1
    assert merged[0]["match_terms"] == ["archfin.sg", "Jane Q Founder"]  # case-insensitive dedup
    assert merged[0]["exclude_terms"] == ["ticker"]


def test_merge_skips_unknown_company_and_reports_no_change(gm) -> None:
    companies = [{"name": "Arch", "aliases": []}]
    merged, changed = gm.merge(companies, {"entries": [{"name": "Ghost Co", "match_terms": ["x.com"]}]})
    assert changed == 0
    assert merged == companies


def test_load_draft_accepts_bare_list(gm, tmp_path) -> None:
    p = tmp_path / "draft.json"
    p.write_text(json.dumps([{"name": "Arch", "match_terms": ["archfin.sg"]}]))
    assert gm.load_draft(p)["entries"][0]["name"] == "Arch"


def test_merged_watchlist_still_passes_the_schema(gm, companies) -> None:
    """A merged draft must not violate data/companies.json's schema rules."""
    draft = {"entries": [{"name": companies[0]["name"], "match_terms": ["example.com"]}]}
    merged, changed = gm.merge(companies, draft)
    assert changed == 1
    entry = next(c for c in merged if c["name"] == companies[0]["name"])
    assert all(isinstance(t, str) and t.strip() == t for t in entry["match_terms"])
