"""Unit tests for scripts/filter_exclusions.py."""

from __future__ import annotations

import json
from pathlib import Path


def _write_companies(tmp_repo: Path, companies: list[dict]) -> None:
    (tmp_repo / "data" / "companies.json").write_text(json.dumps(companies))


def _write_updates(tmp_repo: Path, date: str, body: str) -> Path:
    p = tmp_repo / "signals" / "updates" / f"{date}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _write_agent(tmp_repo: Path, date: str, body: str) -> Path:
    p = tmp_repo / "signals" / "agent" / f"{date}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_drops_layer1_item_matching_exclude_term(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    _write_companies(
        tmp_repo,
        [
            {"name": "Acme", "exclude_terms": ["sponsored"]},
            {"name": "Beta", "exclude_terms": []},
        ],
    )
    body = (
        "# Company Updates — 2026-05-20\n"
        "\n"
        "## Acme\n"
        "- **Acme launches new product** — Acme · news · 2026-05-20\n"
        "  https://acme.example/launch\n"
        "- **Acme sponsored advertorial** — Acme · news · 2026-05-20\n"
        "  https://acme.example/sponsored\n"
        "\n"
        "## Beta\n"
        "- **Beta hits ARR milestone** — Beta · news · 2026-05-20\n"
        "  https://beta.example/arr\n"
    )
    target = _write_updates(tmp_repo, "2026-05-20", body)

    excludes = mod.load_exclude_terms(tmp_repo / "data" / "companies.json")
    seen = tmp_repo / "signals" / "seen-urls.txt"
    dropped = mod.process_file(target, excludes, seen)

    assert dropped == 1
    out = target.read_text()
    assert "sponsored advertorial" not in out
    assert "launches new product" in out
    assert "Beta hits ARR" in out
    assert seen.read_text().strip().splitlines() == ["https://acme.example/sponsored"]
    assert "## Beta" in out


def test_case_insensitive_match(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    _write_companies(tmp_repo, [{"name": "Acme", "exclude_terms": ["WEBINAR"]}])
    target = _write_updates(
        tmp_repo,
        "2026-05-20",
        "# Company Updates — 2026-05-20\n"
        "\n"
        "## Acme\n"
        "- **Acme joins free webinar on cloud** — Acme\n"
        "  https://acme.example/webinar\n",
    )
    excludes = mod.load_exclude_terms(tmp_repo / "data" / "companies.json")
    dropped = mod.process_file(target, excludes, tmp_repo / "signals" / "seen-urls.txt")
    assert dropped == 1


def test_run_at_divider_does_not_become_current_company(scripts_module_loader, tmp_repo: Path) -> None:
    """`## Run at <time>` is a re-run divider; bullets after the next H2 must still be filtered."""
    mod = scripts_module_loader("filter_exclusions")
    _write_companies(tmp_repo, [{"name": "Acme", "exclude_terms": ["junk"]}])
    target = _write_updates(
        tmp_repo,
        "2026-05-20",
        "# Company Updates — 2026-05-20\n"
        "\n"
        "## Run at 13:00 UTC\n"
        "\n"
        "## Acme\n"
        "- **Acme launches** — Acme\n"
        "  https://acme.example/launch\n"
        "- **Acme junk advertorial** — Acme\n"
        "  https://acme.example/junk\n",
    )
    excludes = mod.load_exclude_terms(tmp_repo / "data" / "companies.json")
    seen = tmp_repo / "signals" / "seen-urls.txt"
    dropped = mod.process_file(target, excludes, seen)
    assert dropped == 1
    out = target.read_text()
    assert "junk advertorial" not in out
    assert "launches" in out
    assert seen.read_text().splitlines() == ["https://acme.example/junk"]


def test_layer2_cohort_pruned_when_all_children_dropped(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    _write_companies(tmp_repo, [{"name": "Acme", "exclude_terms": ["promo"]}])
    target = _write_agent(
        tmp_repo,
        "2026-05-20",
        "# Agent supplement — 2026-05-20\n"
        "\n"
        "## Gap-fill cohort\n"
        "\n"
        "### Acme\n"
        "- **Acme promo email blast** — Acme\n"
        "  https://acme.example/promo\n",
    )
    excludes = mod.load_exclude_terms(tmp_repo / "data" / "companies.json")
    dropped = mod.process_file(target, excludes, tmp_repo / "signals" / "seen-urls.txt")
    assert dropped == 1
    out = target.read_text()
    assert "### Acme" not in out
    assert "## Gap-fill cohort" not in out


def test_idempotent_second_run_drops_nothing(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    _write_companies(tmp_repo, [{"name": "Acme", "exclude_terms": ["sponsored"]}])
    target = _write_updates(
        tmp_repo,
        "2026-05-20",
        "# Company Updates — 2026-05-20\n"
        "\n"
        "## Acme\n"
        "- **Acme launches** — Acme\n"
        "  https://acme.example/launch\n"
        "- **Acme sponsored** — Acme\n"
        "  https://acme.example/sponsored\n",
    )
    excludes = mod.load_exclude_terms(tmp_repo / "data" / "companies.json")
    seen = tmp_repo / "signals" / "seen-urls.txt"
    assert mod.process_file(target, excludes, seen) == 1
    assert mod.process_file(target, excludes, seen) == 0
    assert seen.read_text().strip().splitlines() == ["https://acme.example/sponsored"]


def test_seen_urls_file_is_production(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    assert mod.seen_urls_file(tmp_repo) == tmp_repo / "signals" / "seen-urls.txt"


def test_default_targets(scripts_module_loader, tmp_repo: Path) -> None:
    mod = scripts_module_loader("filter_exclusions")
    targets = mod.default_targets(tmp_repo, "2026-06-10")
    rels = [str(t.relative_to(tmp_repo)) for t in targets]
    assert rels == [
        "signals/updates/2026-06-10.md",
        "signals/agent/2026-06-10.md",
    ]
