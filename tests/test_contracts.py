import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contracts import ContractError, freshness, validate

FIX = Path(__file__).parent / "fixtures"
MARTS = sorted((FIX / "marts").glob("*.json"))


def load(p):
    return json.loads(p.read_text())


@pytest.mark.parametrize("path", MARTS, ids=lambda p: p.stem)
def test_fixture_marts_are_valid(path):
    validate("chart_spec", load(path))


def test_fixtures_cover_every_state_the_ui_must_render():
    marts = [load(p) for p in MARTS]
    freshness_seen = {s["freshness"] for m in marts for s in m["sources"]}
    assert freshness_seen == {"fresh", "aging", "stale", "never"}
    assert {m["status"] for m in marts} == {"ok", "awaiting_data"}
    assert {s["method"] for m in marts for s in m["sources"]} >= {"api", "manual", "ledger"}
    assert any(m["badges"] for m in marts)
    assert {"line", "stacked_bar", "bar", "table", "stat"} <= {m["kind"] for m in marts}


def test_fixture_observations_and_sources_are_valid():
    for line in (FIX / "observations.jsonl").read_text().splitlines():
        validate("observation", json.loads(line))
    for src in load(FIX / "sources.json"):
        validate("source", src)


@pytest.mark.parametrize("name", ["bull", "neutral"])
def test_fixture_ai_reports_are_valid(name):
    validate("ai_report", load(FIX / "ai" / f"{name}.json"))


def test_ai_citations_match_fixture_marts():
    """The Phase 4 number gate relies on this: every cited value exists in the cited chart."""
    marts = {p.stem: load(p) for p in MARTS}
    for name in ("bull", "neutral"):
        rep = load(FIX / "ai" / f"{name}.json")
        cites = [e for a in rep.get("arguments", []) for e in a["evidence"]] + [
            e for d in rep.get("debates", []) for e in d["evidence"]
        ]
        for e in cites:
            rows = marts[e["chart_id"]]["rows"]
            assert any(e["value"] in row.values() for row in rows), e


# --- the contracts must reject bad data, not just accept good data ---


def obs():
    return json.loads((FIX / "observations.jsonl").read_text().splitlines()[0])


@pytest.mark.parametrize("field", ["source", "source_url", "as_of", "retrieved_at", "tier", "value"])
def test_observation_without_provenance_is_rejected(field):
    row = obs()
    del row[field]
    with pytest.raises(ContractError):
        validate("observation", row)


def test_manual_row_without_entered_by_or_evidence_is_rejected():
    row = obs() | {"method": "manual", "evidence": "p.3"}
    with pytest.raises(ContractError, match="entered_by"):
        validate("observation", row)
    row = obs() | {"method": "ledger", "entered_by": "EwenCheung"}
    with pytest.raises(ContractError, match="evidence"):
        validate("observation", row)


def test_observation_rejects_bad_dates_and_text_values():
    with pytest.raises(ContractError):
        validate("observation", obs() | {"as_of": "05/10/2026"})
    with pytest.raises(ContractError):
        validate("observation", obs() | {"value": "640"})


def mart(name="sample.open_roles"):
    return copy.deepcopy(load(FIX / "marts" / f"{name}.json"))


def test_chart_row_missing_a_column_is_rejected():
    m = mart()
    del m["rows"][0]["value"]
    with pytest.raises(ContractError, match="missing"):
        validate("chart_spec", m)


def test_chart_status_must_match_rows():
    m = mart()
    m["status"] = "awaiting_data"
    with pytest.raises(ContractError, match="awaiting_data"):
        validate("chart_spec", m)


def test_manual_source_without_manual_block_is_rejected():
    m = mart("sample.consumer_spend")
    m["sources"][0]["manual"] = None
    with pytest.raises(ContractError, match="manual"):
        validate("chart_spec", m)


def test_never_freshness_requires_no_retrieval():
    m = mart()
    m["sources"][0]["freshness"] = "never"
    with pytest.raises(ContractError, match="never"):
        validate("chart_spec", m)


def test_encoding_must_point_at_a_column():
    m = mart()
    m["encoding"]["y"]["field"] = "nope"
    with pytest.raises(ContractError, match="encoding"):
        validate("chart_spec", m)


def test_case_report_needs_four_arguments_and_neutral_needs_debates():
    bull = load(FIX / "ai" / "bull.json")
    bull["arguments"] = bull["arguments"][:3]
    with pytest.raises(ContractError):
        validate("ai_report", bull)
    neutral = load(FIX / "ai" / "neutral.json")
    del neutral["debates"]
    with pytest.raises(ContractError):
        validate("ai_report", neutral)


def test_freshness_rule():
    now = datetime(2026, 10, 5, tzinfo=UTC)
    assert freshness(None, 2, now) == "never"
    assert freshness(now - timedelta(days=2), 2, now) == "fresh"
    assert freshness(now - timedelta(days=3), 2, now) == "aging"
    assert freshness(now - timedelta(days=4), 2, now) == "aging"
    assert freshness(now - timedelta(days=4, seconds=1), 2, now) == "stale"
