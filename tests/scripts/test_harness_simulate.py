"""Unit tests for scripts/harness/simulate.py — the scenario + window replayer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import scenarios, simulate


def make_card(root: Path, *, headline: str, expected: str, company: str = "Acme", origin_kind: str = "ab_seed") -> dict:
    card = {
        "id": scenarios.next_id("ingest", "2026-10-01", root),
        "layer": "ingest",
        "version": 1,
        "status": "active",
        "origin": {"kind": origin_kind, "date": "2026-10-01"},
        "persona": "Layer 1 relevance pass",
        "goal": f"{expected} {headline}",
        "input": {
            "candidate": {"title": headline, "url": f"https://news.test/{headline}", "source": "TC",
                          "matched": [company]},
            "companies": {company: {"description": "SG robotics startup"}},
        },
        "expected": {"verdict": expected, "company": company if expected == "keep" else None},
        "cluster": "ingest/false_keep/weak_term/acme",
        "graders": ["verdict_match", "company_match", "blind_judge"],
        "created_by": "test",
    }
    scenarios.save(card, root)
    return card


def write_snapshot(window: Path, date: str, candidates: list[dict], companies: dict | None = None) -> None:
    window.mkdir(parents=True, exist_ok=True)
    payload = {"candidates": candidates, "companies": companies or {"Acme": "SG robotics startup"}}
    (window / f"{date}.json").write_text(json.dumps(payload))


def candidate(slug: str, **extra) -> dict:
    return {"company": "Acme", "headline": slug, "description": "", "source": "TC",
            "link": f"https://news.test/{slug}", "source_kind": "rss", **extra}


class FakeChat:
    def __init__(self, reply_for=None) -> None:
        self.calls: list[dict] = []
        self.reply_for = reply_for or self.default_reply

    @staticmethod
    def default_reply(user: dict) -> str | None:
        keep = "raises" in user["headline"]
        company = json.dumps(user["company"] if keep else None)
        return f'{{"keep": {json.dumps(keep)}, "company": {company}}}'

    def __call__(self, messages, *, model=None, max_tokens=512, runtime="deepseek", **_):
        user = json.loads(messages[1]["content"])
        self.calls.append({"system": messages[0]["content"], "user": user, "runtime": runtime})
        return self.reply_for(user)


@pytest.fixture()
def fake_chat(monkeypatch) -> FakeChat:
    fake = FakeChat()
    monkeypatch.setattr(simulate.ab_llm, "chat", fake)
    monkeypatch.setattr(simulate.ab_llm, "have_key", lambda runtime="deepseek": True)
    return fake


@pytest.fixture()
def layout(tmp_path: Path) -> dict:
    prompt = tmp_path / "ingest.md"
    prompt.write_text("PRODUCTION INGEST POLICY")
    companies = tmp_path / "companies.json"
    write_watchlist(companies, [{"name": "Unrelated", "description": "other"}])
    return {"root": tmp_path / "scenarios", "window": tmp_path / "replay", "prompt": prompt,
            "out": tmp_path / "sim" / "champion-nvidia.json", "companies": companies}


def write_watchlist(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries))


def run(layout: dict, *extra: str, layer: str = "ingest") -> int:
    return simulate.main([
        "--layer", layer, "--prompt-path", str(layout["prompt"]), "--runtime", "nvidia",
        "--out", str(layout["out"]), "--scenarios", str(layout["root"]), "--window", str(layout["window"]),
        "--companies", str(layout["companies"]), "--today", "2026-10-03", "--sleep", "0", *extra,
    ])


def read_out(layout: dict) -> dict:
    return json.loads(layout["out"].read_text())


def test_scenarios_replayed_with_override_and_runtime(layout, fake_chat) -> None:
    keep_card = make_card(layout["root"], headline="Acme raises seed", expected="keep")
    drop_card = make_card(layout["root"], headline="Arch stablecoin", expected="drop")
    assert run(layout) == 0
    result = read_out(layout)
    assert result["arm"] == "champion" and result["runtime"] == "nvidia" and result["layer"] == "ingest"
    assert result["scenario_results"][keep_card["id"]]["verdict"] == "keep"
    assert result["scenario_results"][keep_card["id"]]["company"] == "Acme"
    assert result["scenario_results"][drop_card["id"]]["verdict"] == "drop"
    assert result["scenario_results"][drop_card["id"]]["company"] is None
    first = fake_chat.calls[0]
    assert first["runtime"] == "nvidia"
    assert first["system"].startswith("PRODUCTION INGEST POLICY")
    assert '"company"' in first["system"]
    assert first["user"]["company_description"] == "SG robotics startup"
    assert set(first["user"]) == {"company", "company_description", "headline", "description", "source", "source_kind"}


def test_retired_cards_are_not_replayed(layout, fake_chat) -> None:
    card = make_card(layout["root"], headline="Acme raises seed", expected="keep")
    card["status"] = "retired"
    scenarios.save(card, layout["root"])
    assert run(layout) == 0
    assert read_out(layout)["scenario_results"] == {}


def test_window_filters_dedupes_skips_pre_extracted_and_limits(layout, fake_chat) -> None:
    write_snapshot(layout["window"], "2026-10-03", [
        candidate("acme-raises-a"),
        candidate("auto-kept", pre_extracted=True),
    ])
    write_snapshot(layout["window"], "2026-10-01", [
        candidate("acme-raises-a"),
        candidate("acme-hiring"),
    ])
    write_snapshot(layout["window"], "2026-09-01", [candidate("too-old")])
    (layout["window"] / "notes.json").write_text("{}")
    assert run(layout, "--window-days", "14") == 0
    window = read_out(layout)["window_results"]
    assert [(r["date"], r["url"], r["verdict"]) for r in window] == [
        ("2026-10-03", "https://news.test/acme-raises-a", "keep"),
        ("2026-10-01", "https://news.test/acme-hiring", "drop"),
    ]
    assert window[0]["company_description"] == "SG robotics startup"
    assert run(layout, "--limit", "1") == 0
    assert len(read_out(layout)["window_results"]) == 1


def test_failopen_per_item_records_null(layout, monkeypatch) -> None:
    replies = iter(["not json", None, '{"keep": true, "company": "Acme"}'])
    monkeypatch.setattr(simulate.ab_llm, "chat", lambda *a, **k: next(replies))
    monkeypatch.setattr(simulate.ab_llm, "have_key", lambda runtime="deepseek": True)
    write_snapshot(layout["window"], "2026-10-02", [candidate("a"), candidate("b"), candidate("c")])
    assert run(layout) == 0
    verdicts = [r["verdict"] for r in read_out(layout)["window_results"]]
    assert verdicts == [None, None, "keep"]


def test_all_null_verdicts_exit_2_with_loud_message(layout, monkeypatch, capsys) -> None:
    monkeypatch.setattr(simulate.ab_llm, "chat", lambda *a, **k: None)
    monkeypatch.setattr(simulate.ab_llm, "have_key", lambda runtime="deepseek": True)
    write_snapshot(layout["window"], "2026-10-02", [candidate("a"), candidate("b")])
    assert run(layout) == 2
    assert "unreachable or misconfigured" in capsys.readouterr().err


def test_replay_calls_use_reasoning_safe_max_tokens(layout, monkeypatch) -> None:
    seen = []

    def fake(messages, *, max_tokens=0, **_):
        seen.append(max_tokens)
        return '{"keep": false, "company": null}'

    monkeypatch.setattr(simulate.ab_llm, "chat", fake)
    monkeypatch.setattr(simulate.ab_llm, "have_key", lambda runtime="deepseek": True)
    write_snapshot(layout["window"], "2026-10-02", [candidate("a")])
    assert run(layout) == 0
    assert seen and all(tokens >= 1024 for tokens in seen)


def test_missing_key_exits_2(layout, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert run(layout) == 2
    assert not layout["out"].exists()


@pytest.mark.parametrize("layer", ["agent_supplement", "synthesis"])
def test_unimplemented_layers_write_empty_result(layout, monkeypatch, layer) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert run(layout, layer=layer) == 0
    result = read_out(layout)
    assert result["implemented"] is False
    assert result["scenario_results"] == {} and result["window_results"] == []


def test_explicit_arm_label(layout, fake_chat) -> None:
    assert run(layout, "--arm", "proposal") == 0
    result = read_out(layout)
    assert result["arm"] == "proposal"
    assert result["companies_path"] == str(layout["companies"])


def test_live_description_overlays_frozen_one(layout, fake_chat) -> None:
    make_card(layout["root"], headline="Acme raises seed", expected="keep")
    write_snapshot(layout["window"], "2026-10-02", [candidate("acme-raises-b")])
    write_watchlist(layout["companies"], [{"name": "Acme", "description": "LIVE: Singapore warehouse robots"}])
    assert run(layout) == 0
    descriptions = [call["user"]["company_description"] for call in fake_chat.calls]
    assert descriptions == ["LIVE: Singapore warehouse robots"] * 2
    assert read_out(layout)["window_results"][0]["company_description"] == "LIVE: Singapore warehouse robots"


def test_live_exclude_terms_veto_without_model_call(layout, fake_chat) -> None:
    card = make_card(layout["root"], headline="Acme stablecoin raises", expected="drop")
    write_snapshot(layout["window"], "2026-10-02", [
        candidate("acme-raises-stablecoin", description="a stablecoin issuer"),
        candidate("acme-raises-robots"),
    ])
    write_watchlist(layout["companies"], [{"name": "Acme", "description": "robots", "exclude_terms": ["stablecoin"]}])
    assert run(layout) == 0
    result = read_out(layout)
    assert result["scenario_results"][card["id"]] == {
        "verdict": "drop", "company": None, "raw": None, "via": "exclude_terms",
    }
    by_url = {row["url"]: row for row in result["window_results"]}
    assert by_url["https://news.test/acme-raises-stablecoin"]["verdict"] == "drop"
    assert by_url["https://news.test/acme-raises-stablecoin"]["via"] == "exclude_terms"
    assert by_url["https://news.test/acme-raises-robots"]["via"] == "llm"
    assert [call["user"]["headline"] for call in fake_chat.calls] == ["acme-raises-robots"]


def test_companies_default_prefers_cwd(tmp_path, monkeypatch) -> None:
    local = tmp_path / "data" / "companies.json"
    local.parent.mkdir()
    local.write_text("[]")
    monkeypatch.chdir(tmp_path)
    assert simulate.resolve_companies_path(None) == local
    monkeypatch.chdir(tmp_path / "data")
    assert simulate.resolve_companies_path(None) == simulate.REPO_ROOT / "data" / "companies.json"
