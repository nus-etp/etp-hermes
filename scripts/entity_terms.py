#!/usr/bin/env python3
"""Entity-linked matching terms for candidate triage.

Shared by ``scripts/collect-candidates.py`` (triage-time enforcement) and
``scripts/generate_match_terms.py`` (the offline term generator).

A company is linked to a feed item through three per-company term lists:

* **name + aliases** — the historical watchlist tokens.
* ``match_terms`` — extra *positive* terms an operator curated: founder full
  names, distinctive product names, the homepage domain (``assist.id``).
* ``exclude_terms`` — negative terms. A hit suppresses the company's match for
  that item, no matter what else matched.

Bare name matching over-fires badly on the ~99 watchlist entries with short or
dictionary-word names: "Arch" matched a stablecoin story, "AVA" an Anthropic
story, "Assist.id" a Siri article (via the bare "assist" alias), "amble" a
SpaceX IPO story. Rather than hand-listing those offenders, terms are graded:

* **strong** — distinctive enough that a mention anywhere in the item
  (title *or* description) identifies the company.
* **weak** — short (≤ ``WEAK_MAX_LEN`` chars) or a common English word, so a
  mention only counts when it lands in the *title*. Prose about something else
  routinely contains "assist" or "alpha"; a headline about a 4-letter startup
  almost always names it up front.

Curated ``match_terms`` are strong by construction (that is the point of
curating them) unless the term is itself a common English word — an LLM pass
proposing "assist" as a match term must not upgrade it.

Pure stdlib, no I/O — importable from any script or test.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

# A term this short is assumed ambiguous regardless of dictionary membership:
# "arch", "ava", "bae", "alpha", "amble", "finan" are all ≤ 5 characters.
WEAK_MAX_LEN = 5

# Common English words that also read as brand names. Membership demotes a term
# to weak (title-only matching) however long it is. This is deliberately a
# *word* list, not a company list — company-specific disambiguation belongs in
# that company's `exclude_terms`.
_COMMON_ENGLISH = """
    able about above accept access account across action active adapt advance
    agent agile alpha amble anchor answer appeal arch ardent arrive ascend
    ascent aspire assist assure atlas attend august aurora avenue banner beacon
    become before begin behind belief better beyond binary blend bloom border
    bounce branch breeze bridge bright broad budget buffer build bundle canopy
    canvas carbon career carrier catalyst center centre change channel charge
    charm choice circle circuit clarity clean clear clever climate cloud
    cluster coast collect column combine comfort command common compass
    compose concept connect consult contact content convert corner course
    cradle craft create credit crest crown crystal culture current custom
    cycle deliver delta demand deploy design detect device digest digital
    direct discover domain double drive eager eagle earth easy edge effort
    elevate embark emerge empire enable endure energy engage engine enrich
    ensure enter equal escape essence evolve exact examine expand expert
    explore export express extend fabric factor family fathom favor feature
    fetch fiber field filter finance finder finite flame flare flash fleet
    flight flourish flow focus follow forest forge format forward foster
    foundation fountain frame fresh front fusion future gadget garden gather
    genesis gentle gesture giant glance global glory golden govern grace grade
    grand grant graph green grid ground group grove growth guard guide habit
    handle happy harbor harbour harvest health helix helper heritage horizon
    humble hunter hustle ideal ignite image impact impulse income index infer
    infinite inform inside insight inspire instant intent invest island issue
    jewel journal journey juniper kernel kindle knowledge ladder lantern latch
    launch layer leader leaf ledger legacy legend lever liberty lift light
    linear listen little lively locate logic lookout loyal lucid lumina
    machine magnet major manage manner market marvel master matrix matter
    meadow measure medium member memory mentor merge merit method metric
    mighty mingle mirror mobile modern module moment momentum monitor motion
    motive mount move nature navigate nectar needle nested network never
    nimble noble north notion novel nurture object observe ocean offer offset
    online opal open optic option orbit orchard order origin outlook output
    oxygen packet paper parcel partner patent path pattern peak pearl people
    period permit person phase phoenix pilot pinnacle pioneer pivot planet
    plateau pledge plenty pocket point polar portal potent power practice
    praise precise prefer premier prepare present preserve prevent primary
    prime prism process produce profile project promise proof proper propel
    prosper protect proud provide public pulse pure purpose pursue puzzle
    quality quantum quest question quick quiet radar radiant radius rally
    random range rapid reach ready realm reason rebel record reduce refine
    reflect reform refresh region relate relay release relief remedy remote
    render renew repair report rescue reserve resolve resource respond result
    retain return reveal revive reward rhythm ridge right ripple rise river
    rocket rooted rotate route royal safety sample scale scatter scholar
    school science scope score scout screen script search season second secret
    sector secure select sense sensor series serve server settle shade shadow
    shape share shelter shield shift shine shore short signal silver simple
    single skill sketch smart smooth social socket solar solid solve sonic
    sorted source space spark spectrum sphere spirit splash spring sprint
    sprout square stable stack staff stage stake stand start state steady
    steam steel stellar stone storm story strand strategy stream street strong
    studio study style submit subtle sudden summit sunrise sunset supply
    support surface surge survey sustain swift switch symbol system table
    tablet tackle talent target teach tempo tender terminal thread threshold
    thrive thunder ticket tidal timber timing tissue title today together
    token total touch trace track trade trail transform travel tribe trigger
    triple trust truth tunnel turbine unify union unique unite unity universe
    unlock update upload upward urban urgent useful valley value vantage
    vector vendor venture verify vessel victor vigor virtue vision visual
    vital voice volume voyage wander watch water wealth weather weave welcome
    whisper willow window winner winter wisdom wonder worker world worthy
    yield zenith zephyr zone
    """

# Business boilerplate: never distinctive on its own, and the exact vocabulary a
# generator pass is most tempted to propose as a "product name".
_GENERIC_BUSINESS = """
    academy analytics business capital company consulting corporation customer
    enterprise financial foundation group holdings incubator industry
    innovation institute investment labs partners platform portfolio product
    program project research robotics service services software solution
    solutions startup strategy studio systems technologies technology ventures
    """

COMMON_WORDS = frozenset((_COMMON_ENGLISH + _GENERIC_BUSINESS).split())

# Match verdicts returned by `match_kind`.
MATCH_NONE = ""
MATCH_STRONG = "strong"
MATCH_WEAK_TITLE = "weak_title"
MATCH_WEAK_DESC = "weak_description"  # suppressed by the generic-term guard


class CompanyMatcher(NamedTuple):
    """Compiled matchers for one watchlist entry."""

    strong: list[re.Pattern[str]]
    weak: list[re.Pattern[str]]
    exclude: list[re.Pattern[str]]


def normalize_term(term: str) -> str:
    return " ".join(term.strip().lower().split())


def term_is_strong(term: str, *, curated: bool = False) -> bool:
    """Is `term` distinctive enough to match outside the title?

    Strong when it carries a digit or a dot (``x0pa``, ``assist.id``), spans
    more than one word (``Bright Sight``), is a curated ``match_term``, or is
    simply long enough to be unambiguous. A common English word is never strong
    — not even as a curated term.
    """
    t = normalize_term(term)
    if not t:
        return False
    if any(ch.isdigit() for ch in t) or "." in t:
        return True
    if " " in t:
        return True
    if t in COMMON_WORDS:
        return False
    if curated:
        return True
    return len(t) > WEAK_MAX_LEN


def build_term_matchers(terms: dict[str, list[str]]) -> dict[str, list[re.Pattern[str]]]:
    """Compile each company's lowercased terms into whole-word matchers.

    Firehose triage matches watchlist names as tokens, not raw substrings, so a
    short alias like "arch" no longer fires on "research", "wise" on "WiseTech",
    "ava" on "available", or "finan" on "financial". Lookarounds (not ``\\b``) so
    terms with adjacent punctuation such as "assist.id" still anchor correctly.
    Terms are already lowercased by the caller and matched against a lowercased
    haystack, so the patterns are case-sensitive by construction.
    """
    return {
        name: [_compile(t) for t in ts if t]
        for name, ts in terms.items()
    }


def _compile(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def company_terms(company: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Split one company entry's terms into (strong, weak, exclude), lowercased."""
    strong: list[str] = []
    weak: list[str] = []
    for term in [company["name"], *(company.get("aliases") or [])]:
        t = normalize_term(term)
        if not t:
            continue
        (strong if term_is_strong(t) else weak).append(t)
    for term in company.get("match_terms") or []:
        t = normalize_term(term)
        if not t:
            continue
        (strong if term_is_strong(t, curated=True) else weak).append(t)
    exclude = [t for t in (normalize_term(x) for x in company.get("exclude_terms") or []) if t]
    return strong, weak, exclude


def build_company_matchers(companies: list[dict[str, Any]]) -> dict[str, CompanyMatcher]:
    """Compile every watchlist entry into its strong/weak/exclude matchers."""
    out: dict[str, CompanyMatcher] = {}
    for c in companies:
        strong, weak, exclude = company_terms(c)
        out[c["name"]] = CompanyMatcher(
            strong=[_compile(t) for t in strong],
            weak=[_compile(t) for t in weak],
            exclude=[_compile(t) for t in exclude],
        )
    return out


def match_kind(matcher: CompanyMatcher, title: str, description: str = "") -> str:
    """Classify how (and whether) an item matches one company.

    Returns ``MATCH_STRONG`` / ``MATCH_WEAK_TITLE`` (both accepted by the
    caller), ``MATCH_WEAK_DESC`` (a weak term hit the description only — the
    generic-term false-positive class, rejected but worth counting), or
    ``MATCH_NONE``.
    """
    title_l = title.lower()
    haystack = f"{title} {description}".lower()
    if any(p.search(haystack) for p in matcher.strong):
        return MATCH_STRONG
    if any(p.search(title_l) for p in matcher.weak):
        return MATCH_WEAK_TITLE
    # With no description the haystack is just the title, already searched.
    if description and any(p.search(haystack) for p in matcher.weak):
        return MATCH_WEAK_DESC
    return MATCH_NONE


def is_excluded(matcher: CompanyMatcher, title: str, description: str = "") -> bool:
    """Does any exclude_term hit the item? Suppresses the match unconditionally."""
    if not matcher.exclude:
        return False
    haystack = f"{title} {description}".lower()
    return any(p.search(haystack) for p in matcher.exclude)
