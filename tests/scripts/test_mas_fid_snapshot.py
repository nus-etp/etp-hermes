"""Unit tests for scripts/mas_fid_snapshot.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib import error, parse

import pytest

NOW = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)

COMPANIES = [
    {"name": "Qapita", "aliases": [], "country": "India"},
    {"name": "Acme Pay", "aliases": ["Acme Pay Pte Ltd"], "country": "Singapore"},
    {"name": "Nova", "aliases": [], "country": "Singapore", "sources": [{"url": "https://nova.sg/blog"}]},
    {"name": "Twin", "aliases": [], "country": "Singapore"},
    {"name": "Twin Labs", "aliases": ["Twin"], "country": "Singapore"},
]


@pytest.fixture()
def mas(scripts_module_loader):
    return scripts_module_loader("mas_fid_snapshot")


def _home(categories: list[str]) -> str:
    links = "".join(
        f'<a href="/fid/institution?category={parse.quote(c)}">{c}</a>' for c in categories + categories
    )
    return f"<html>{links}</html>"


def _entry(slug: str, name: str, website: str = "") -> str:
    site = f'<a class="font-resize" target="_blank" href="{website}">{website}</a>' if website else ""
    return (
        f'<div class="inner"><a href="/fid/institution/detail/{slug}"><h3 class="header-inner-2">{name}</h3></a>'
        f"<div class=\"info\">{site}</div></div>"
    )


def _listing(entries: list[str], total: int = 1) -> str:
    return f'<div class="box-wrapper" data-total="{total}" data-page="1">{"".join(entries)}</div>'


def _detail(licences: dict[str, list[str]]) -> str:
    body = ""
    for category, activities in licences.items():
        lis = "".join(f"<li>{a}</li>" for a in activities)
        body += (
            f'<h3 class="header-inner-2 toggle">{category}</h3><div class="inner-2">'
            f"<h4 class=\"header-inner-3\">Dealing</h4><ul>{lis}</ul></div>"
        )
    return f'<h2 class="header">Licence Type/Status</h2>{body}<div class="key-personnel">x</div>'


def _site(listings: dict[str, list[str]], details: dict[str, dict[str, list[str]]], fail: set[str] | None = None):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if fail and any(f in url for f in fail):
            raise error.URLError("boom")
        if url.endswith("/fid"):
            return _home(list(listings))
        if "resultpartial" in url:
            category = parse.parse_qs(parse.urlparse(url).query)["category"][0]
            return _listing(listings[category])
        slug = url.rsplit("/", 1)[1]
        return _detail(details[slug])

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _no_sleep(_s: float) -> None:
    return None


def test_discover_categories_dedupes_and_unquotes(mas) -> None:
    assert mas.discover_categories(_home(["Full Bank", "Major Payment Institution"])) == [
        "Full Bank",
        "Major Payment Institution",
    ]


def test_parse_listing_and_detail(mas) -> None:
    entries, total = mas.parse_listing(_listing([_entry("1-A", "A &amp; B PTE. LTD.", "http://a.com")], 3))
    assert entries == [{"slug": "1-A", "name": "A & B PTE. LTD.", "website": "http://a.com"}]
    assert total == 3
    assert mas.parse_detail(_detail({"CMS": ["Securities", "Funds"]})) == {"CMS": ["Dealing", "Funds", "Securities"]}


def test_match_requires_unique_exact_key_and_weak_domain(mas) -> None:
    institutions = {
        "1-QAPITA": {"name": "QAPITA PTE. LTD.", "website": "", "categories": ["CMS"]},
        "2-ACME": {"name": "ACME PAY PTE. LTD.", "website": "", "categories": ["MPI"]},
        "3-NOVA": {"name": "NOVA PTE. LTD.", "website": "", "categories": ["MPI"]},
        "4-TWIN": {"name": "TWIN PTE. LTD.", "website": "", "categories": ["MPI"]},
        "5-OTHER": {"name": "UNRELATED PTE. LTD.", "website": "", "categories": ["MPI"]},
    }
    assert mas.match_institutions(COMPANIES, institutions) == {"Acme Pay": "2-ACME", "Qapita": "1-QAPITA"}
    institutions["3-NOVA"]["website"] = "https://www.nova.sg"
    assert mas.match_institutions(COMPANIES, institutions)["Nova"] == "3-NOVA"


def test_duplicate_register_names_are_not_matched(mas) -> None:
    institutions = {
        "1-QAPITA": {"name": "QAPITA PTE. LTD.", "website": "", "categories": ["CMS"]},
        "9-QAPITA": {"name": "Qapita Private Limited", "website": "", "categories": ["MPI"]},
    }
    assert mas.match_institutions(COMPANIES, institutions) == {}


def _run(mas, tmp_path, fetcher):
    snapshot = tmp_path / "masfid" / "snap.json"
    items = tmp_path / "items.json"
    code = mas.run(COMPANIES, snapshot, items, fetcher=fetcher, sleep=_no_sleep, now=NOW)
    return code, snapshot, items


LISTINGS = {
    "Capital Markets Services Licensee": [_entry("1-QAPITA", "QAPITA PTE. LTD.")],
    "Major Payment Institution": [_entry("2-ACME", "ACME PAY PTE. LTD.")],
}
DETAILS = {
    "1-QAPITA": {"Capital Markets Services Licensee": ["Securities"]},
    "2-ACME": {"Major Payment Institution": ["Cross-border Money Transfer Service"]},
}


def test_first_run_seeds_without_items(mas, tmp_path) -> None:
    code, snapshot, items = _run(mas, tmp_path, _site(LISTINGS, DETAILS))
    assert code == 0
    assert set(json.loads(snapshot.read_text())["companies"]) == {"Qapita", "Acme Pay"}
    payload = json.loads(items.read_text())
    assert payload["items"] == []
    assert payload["snapshot_updated"] is True


def test_detail_fetch_only_for_matches(mas, tmp_path) -> None:
    listings = dict(LISTINGS)
    listings["Full Bank"] = [_entry("7-BIG", "BIG BANK LTD")]
    fetch = _site(listings, DETAILS)
    _run(mas, tmp_path, fetch)
    detail_calls = [c for c in fetch.calls if "/detail/" in c]
    assert sorted(c.rsplit("/", 1)[1] for c in detail_calls) == ["1-QAPITA", "2-ACME"]


def test_new_category_is_discovered_without_hardcoding(mas, tmp_path) -> None:
    listings = {"Brand New Licence Class": [_entry("1-QAPITA", "QAPITA PTE. LTD.")]}
    details = {"1-QAPITA": {"Brand New Licence Class": ["Thing"]}}
    _, snapshot, _ = _run(mas, tmp_path, _site(listings, details))
    assert "Brand New Licence Class" in json.loads(snapshot.read_text())["companies"]["Qapita"]["licences"]


def test_granted_changed_and_revoked_items(mas, tmp_path) -> None:
    _run(mas, tmp_path, _site(LISTINGS, DETAILS))
    listings = {
        "Capital Markets Services Licensee": [_entry("1-QAPITA", "QAPITA PTE. LTD.")],
        "Major Payment Institution": [_entry("1-QAPITA", "QAPITA PTE. LTD.")],
    }
    details = {
        "1-QAPITA": {
            "Capital Markets Services Licensee": ["Securities", "Funds"],
            "Major Payment Institution": ["E-money Issuance Service"],
        }
    }
    code, _, items_path = _run(mas, tmp_path, _site(listings, details))
    assert code == 0
    items = json.loads(items_path.read_text())["items"]
    keys = {i["dedup_key"] for i in items}
    assert "masfid://qapita/major-payment-institution/granted" in keys
    assert any(k.startswith("masfid://qapita/capital-markets-services-licensee/changed-") for k in keys)
    assert "masfid://acme-pay/major-payment-institution/revoked" in keys
    assert {i["source_kind"] for i in items} == {"mas_fid"}
    assert all(i["link"].startswith("https://eservices.mas.gov.sg/fid/institution/detail/") for i in items)


def test_newly_matched_company_emits_listed_item_after_seed(mas, tmp_path) -> None:
    only_qapita = {k: v for k, v in LISTINGS.items() if "Capital" in k}
    _run(mas, tmp_path, _site(only_qapita, DETAILS))
    _, _, items_path = _run(mas, tmp_path, _site(LISTINGS, DETAILS))
    items = json.loads(items_path.read_text())["items"]
    assert [i["dedup_key"] for i in items] == ["masfid://acme-pay/major-payment-institution/granted"]


def test_failed_listing_page_keeps_snapshot_and_emits_nothing(mas, tmp_path) -> None:
    _run(mas, tmp_path, _site(LISTINGS, DETAILS))
    snapshot = tmp_path / "masfid" / "snap.json"
    before = snapshot.read_text()
    code, _, items_path = _run(mas, tmp_path, _site(LISTINGS, DETAILS, fail={"Major"}))
    assert code == 0
    assert snapshot.read_text() == before
    payload = json.loads(items_path.read_text())
    assert payload["items"] == [] and payload["snapshot_updated"] is False


def test_failed_detail_keeps_snapshot(mas, tmp_path) -> None:
    _run(mas, tmp_path, _site(LISTINGS, DETAILS))
    snapshot = tmp_path / "masfid" / "snap.json"
    before = snapshot.read_text()
    code, _, items_path = _run(mas, tmp_path, _site(LISTINGS, DETAILS, fail={"/detail/2-ACME"}))
    assert code == 0
    assert snapshot.read_text() == before
    assert json.loads(items_path.read_text())["snapshot_updated"] is False


def test_everything_failing_exits_one(mas, tmp_path) -> None:
    code, snapshot, _ = _run(mas, tmp_path, _site(LISTINGS, DETAILS, fail={"mas.gov.sg"}))
    assert code == 1
    assert not snapshot.exists()
