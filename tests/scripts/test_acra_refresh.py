from __future__ import annotations

import csv
import io
import json
import urllib.error

import pytest


@pytest.fixture(scope="module")
def acra(scripts_module_loader):
    return scripts_module_loader("acra_refresh")


COMPANIES = [
    {
        "name": "Pinned Robotics",
        "aliases": [],
        "description": "x",
        "country": "Singapore",
        "identifiers": {"uen": "202100001A"},
        "funding_rounds": [],
    },
    {
        "name": "Acme Labs",
        "aliases": ["Acme Laboratories"],
        "description": "x",
        "country": "Singapore",
        "funding_rounds": [],
    },
    {
        "name": "Oldname",
        "aliases": [],
        "description": "x",
        "country": "Singapore",
        "funding_rounds": [],
    },
    {
        "name": "Jakarta Co",
        "aliases": [],
        "description": "x",
        "country": "Indonesia",
        "funding_rounds": [],
    },
]


def source_row(acra, **values) -> dict:
    row = {column: "na" for column in acra.SOURCE_COLUMNS}
    row.update(values)
    return row


def write_source(acra, path, rows, header=None) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header or acra.SOURCE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fixture_rows(acra, pinned_status="Live Company", acme_status="Live Company", acme_postal="117438"):
    return [
        source_row(acra, uen="202100001A", entity_name="PINNED ROBOTICS PTE. LTD.", entity_status_description=pinned_status, postal_code="139951"),
        source_row(acra, uen="202100002B", entity_name="ACME LABS PTE. LTD.", entity_status_description=acme_status, postal_code=acme_postal, primary_ssic_code="62011"),
        source_row(acra, uen="202100003C", entity_name="SHINY NEW PTE. LTD.", former_entity_name1="OLDNAME", entity_status_description="Live Company"),
        source_row(acra, uen="202100004D", entity_name="JAKARTA CO PTE. LTD.", entity_status_description="Live Company"),
        source_row(acra, uen="202100005E", entity_name="UNRELATED TRADING", entity_status_description="Live"),
        source_row(acra, uen="202100006F", entity_name="PINNED ROBOTICS", entity_status_description="Struck Off"),
    ]


@pytest.fixture()
def workspace(tmp_path, acra):
    companies_path = tmp_path / "companies.json"
    companies_path.write_text(json.dumps(COMPANIES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    return {
        "companies": companies_path,
        "source": source_dir,
        "output": tmp_path / "acra" / "watchlist-entities.csv",
        "changes": tmp_path / "acra" / "changes.md",
    }


def run_main(acra, workspace, snapshot="2026-10-14"):
    return acra.main([
        "--from-dir", str(workspace["source"]),
        "--snapshot-date", snapshot,
        "--companies", str(workspace["companies"]),
        "--output", str(workspace["output"]),
        "--changes", str(workspace["changes"]),
        "--min-source-rows", "1",
    ])


def read_output(workspace) -> list[dict]:
    with workspace["output"].open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_name_key_strips_suffixes_punctuation_and_case(acra):
    assert acra.name_key("Acme Labs Pte. Ltd.") == acra.name_key("ACME-LABS")
    assert acra.name_key("Arch (Singapore) Private Limited") == "ARCH"


def test_filtering_matches_uen_name_and_former_name(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra))
    assert run_main(acra, workspace) == 0
    rows = read_output(workspace)
    by_uen = {row["uen"]: row for row in rows}
    assert set(by_uen) == {"202100001A", "202100002B", "202100003C"}
    assert by_uen["202100001A"]["match"] == "uen"
    assert by_uen["202100002B"]["match"] == "name"
    assert by_uen["202100002B"]["matched_company"] == "Acme Labs"
    assert by_uen["202100003C"]["match"] == "former_name"
    assert by_uen["202100003C"]["former_names"] == "OLDNAME"
    assert by_uen["202100003C"]["matched_company"] == "Oldname"
    assert by_uen["202100001A"]["primary_ssic_code"] == ""
    assert [row["uen"] for row in rows] == ["202100002B", "202100003C", "202100001A"]
    assert list(rows[0]) == acra.OUTPUT_COLUMNS


def test_first_run_writes_baseline_report(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra))
    run_main(acra, workspace)
    report = workspace["changes"].read_text(encoding="utf-8")
    assert "Baseline snapshot" in report
    assert "Matched entities: 3" in report


def test_diff_report_lists_status_rename_address_and_new(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra))
    run_main(acra, workspace)
    rows = fixture_rows(acra, acme_status="Struck Off", acme_postal="119613")
    rows[2]["entity_name"] = "SHINIER NEW PTE. LTD."
    rows.append(source_row(acra, uen="202500009Z", entity_name="ACME LABORATORIES", entity_status_description="Live"))
    write_source(acra, workspace["source"] / "A.csv", rows)
    assert run_main(acra, workspace, snapshot="2026-11-14") == 0
    report = workspace["changes"].read_text(encoding="utf-8")
    assert "## Live → Struck Off / Liquidation / Dissolved (1)" in report
    assert "ACME LABS PTE. LTD." in report and "**Struck Off**" in report
    assert "## Renames (consider adopting the new name or adding an alias) (1)" in report
    assert "was SHINY NEW PTE. LTD." in report
    assert "117438 → 119613" in report
    assert "`202500009Z` ACME LABORATORIES" in report
    assert "## Applied to companies.json: marked inactive (0)" in report
    companies = json.loads(workspace["companies"].read_text(encoding="utf-8"))
    assert all("status" not in company for company in companies)


def test_auto_inactive_only_for_uen_pinned(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra))
    run_main(acra, workspace)
    original = workspace["companies"].read_text(encoding="utf-8")
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra, pinned_status="Struck Off", acme_status="Struck Off"))
    assert run_main(acra, workspace, snapshot="2026-11-14") == 0
    updated = workspace["companies"].read_text(encoding="utf-8")
    companies = {company["name"]: company for company in json.loads(updated)}
    assert companies["Pinned Robotics"]["status"] == "inactive"
    assert companies["Pinned Robotics"]["status_reason"] == "ACRA: Struck Off (2026-11-14 snapshot)"
    assert "status" not in companies["Acme Labs"]
    added = [line for line in updated.splitlines() if line not in original.splitlines()]
    assert added == ['    "status": "inactive",', '    "status_reason": "ACRA: Struck Off (2026-11-14 snapshot)",']
    lines = updated.splitlines()
    assert lines[lines.index('    "status": "inactive",') - 1] == '    "country": "Singapore",'
    report = workspace["changes"].read_text(encoding="utf-8")
    assert "## Applied to companies.json: marked inactive (1)" in report


def test_pinned_dead_without_transition_is_reported_not_applied(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra, pinned_status="Struck Off"))
    run_main(acra, workspace)
    original = workspace["companies"].read_text(encoding="utf-8")
    run_main(acra, workspace, snapshot="2026-11-14")
    assert workspace["companies"].read_text(encoding="utf-8") == original
    report = workspace["changes"].read_text(encoding="utf-8")
    assert "not auto-applied (review manually) (1)" in report


def test_fails_loud_on_zero_rows(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", [])
    assert run_main(acra, workspace) == 1
    assert not workspace["output"].exists()


def test_fails_loud_on_zero_matches(acra, workspace):
    write_source(acra, workspace["source"] / "A.csv", [source_row(acra, uen="1", entity_name="NOBODY")])
    assert run_main(acra, workspace) == 1


def test_fails_loud_on_schema_change(acra, workspace):
    header = [column for column in acra.SOURCE_COLUMNS if column != "postal_code"]
    write_source(acra, workspace["source"] / "A.csv", fixture_rows(acra), header=header)
    assert run_main(acra, workspace) == 1


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_download_flow_with_mocked_http(acra):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=acra.SOURCE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(fixture_rows(acra))
    csv_bytes = buffer.getvalue().encode("utf-8")
    dataset_ids = [f"d_{index:02d}" for index in range(acra.EXPECTED_DATASET_COUNT)]
    calls = []
    rate_limited = {"done": False}

    def opener(request, timeout=None):
        url = request.full_url
        calls.append(url)
        assert request.get_header("User-agent") == acra.USER_AGENT
        if url.endswith("/metadata"):
            payload = {"code": 0, "data": {"collectionMetadata": {"childDatasets": dataset_ids, "lastUpdatedAt": "2026-10-14T08:00:00+08:00"}}}
        elif url.endswith("/initiate-download"):
            if not rate_limited["done"]:
                rate_limited["done"] = True
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            payload = {"code": 0, "data": {}}
        elif url.endswith("/poll-download"):
            dataset_id = url.split("/datasets/")[1].split("/")[0]
            payload = {"code": 0, "data": {"url": f"https://s3.example/{dataset_id}.csv"}}
        else:
            return FakeResponse(csv_bytes)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    watchlist = acra.Watchlist(COMPANIES)
    rows, total, snapshot = acra.download_and_filter(watchlist, 0, opener=opener, sleep=lambda seconds: None)
    assert snapshot == "2026-10-14"
    assert total == 6 * acra.EXPECTED_DATASET_COUNT
    assert {row["uen"] for row in rows} == {"202100001A", "202100002B", "202100003C"}
    assert sum(1 for url in calls if url.startswith("https://s3.example/")) == acra.EXPECTED_DATASET_COUNT


def test_download_fails_on_wrong_dataset_count(acra):
    def opener(request, timeout=None):
        payload = {"code": 0, "data": {"collectionMetadata": {"childDatasets": ["d_1"], "lastUpdatedAt": "2026-10-14"}}}
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    with pytest.raises(acra.AcraError):
        acra.fetch_collection_metadata(0, opener=opener, sleep=lambda seconds: None)
