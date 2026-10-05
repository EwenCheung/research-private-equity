import csv
import json
from datetime import UTC, datetime

import pytest

from contracts import ContractError
from pipeline import licensed
from pipeline.core import ROOT, registry
from pipeline.core.build import build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations

NOW = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


@pytest.fixture
def root(tmp_path):
    return tmp_path


def rec(**kw):
    base = {
        "as_of": "2026-09-28",
        "entity": "anthropic",
        "metric": "panel_spend_usd",
        "value": "44200000",
        "dims": {"product": "Claude Pro"},
        "url": None,
        "evidence": "Weekly report 2026-10-05, p.3",
    }
    return base | kw


def rows_in(root, sid="yipit_consumer"):
    return list(csv.DictReader((root / "data" / "manual" / f"{sid}.csv").open()))


def test_both_vendors_are_declared_as_manual_vendor_sources():
    metas = licensed.manual_sources()
    assert {"yipit_consumer", "mscience_panel"} <= set(metas)
    for sid in ("yipit_consumer", "mscience_panel"):
        assert (metas[sid]["method"], metas[sid]["tier"], metas[sid]["cadence"]) == ("manual", "vendor", "weekly")


def test_shipped_csvs_are_empty_and_never_entered():
    status = {s["source"]: s for s in licensed.status(ROOT)}
    for sid in ("yipit_consumer", "mscience_panel"):
        assert status[sid]["rows"] == 0 and status[sid]["last_entered"] is None


def test_add_writes_a_stamped_valid_row(root):
    added, skipped = licensed.add(root, "yipit_consumer", [rec()], entered_by="EwenCheung", now=NOW)
    assert (added, skipped) == (1, [])
    [row] = rows_in(root)
    assert (row["entered_by"], row["retrieved_at"], row["evidence"]) == (
        "EwenCheung",
        "2026-10-05T09:30:00Z",
        "Weekly report 2026-10-05, p.3",
    )
    assert row["source_url"] == "urn:yipitdata:weekly-report" and json.loads(row["dims"]) == {"product": "Claude Pro"}
    [obs] = read_observations(
        root, licensed.manual_sources()["yipit_consumer"]
    )  # the core reads it back as a valid observation
    assert (obs["value"], obs["method"], obs["tier"]) == (44200000, "manual", "vendor")


@pytest.mark.parametrize(
    "bad",
    [
        {"evidence": ""},  # evidence is required
        {"evidence": None},
        {"evidence": "   "},  # whitespace is not evidence
        {"as_of": "28/09/2026"},
        {"metric": "revenue"},
        {"entity": "Anthropic Inc"},
        {"value": "lots"},
    ],
)
def test_invalid_rows_are_rejected_and_nothing_is_written(root, bad):
    with pytest.raises((ContractError, ValueError)):
        licensed.add(root, "yipit_consumer", [rec(**bad)], entered_by="EwenCheung", now=NOW)
    assert not (root / "data" / "manual" / "yipit_consumer.csv").exists()


def test_one_bad_row_blocks_the_whole_batch(root):
    with pytest.raises(ContractError):
        licensed.add(
            root, "yipit_consumer", [rec(), rec(as_of="2026-10-05", evidence="")], entered_by="EwenCheung", now=NOW
        )
    assert not (root / "data" / "manual" / "yipit_consumer.csv").exists()


def test_an_entered_week_is_never_overwritten(root):
    licensed.add(root, "yipit_consumer", [rec()], entered_by="EwenCheung", now=NOW)
    added, skipped = licensed.add(
        root, "yipit_consumer", [rec(value="1"), rec(as_of="2026-10-05")], entered_by="EwenCheung", now=NOW
    )
    assert added == 1 and skipped == ["2026-09-28 anthropic panel_spend_usd already entered"]
    assert [r["value"] for r in rows_in(root)] == [
        "44200000",
        "44200000",
    ]  # the original stays; only the new week was added
    # a different product in the same week is a different series, not a duplicate
    assert licensed.add(root, "yipit_consumer", [rec(dims={"product": "Claude Max"})], entered_by="E", now=NOW)[0] == 1


def test_entering_without_a_name_fails_instead_of_stamping_nobody(root, monkeypatch):
    monkeypatch.setattr(licensed, "git_user", lambda: "")
    with pytest.raises(ContractError, match="entered_by"):
        licensed.add(root, "yipit_consumer", [rec()], now=NOW)


def test_import_csv_cleans_vendor_formatting_and_reports_the_bad_line(root, tmp_path):
    f = tmp_path / "report.csv"
    f.write_text(
        'as_of,entity,metric,value,dims\n2026-09-21,anthropic,panel_spend_usd,"$43,500,000","{""product"": ""Claude""}"\n2026-09-28,anthropic,panel_users,"1,200,000",\n'
    )
    records = licensed.records_from_csv(f, "Weekly report 2026-10-05", None)
    assert [r["value"] for r in records] == ["43500000", "1200000"] and records[0]["dims"] == {"product": "Claude"}
    assert licensed.add(root, "yipit_consumer", records, entered_by="E", now=NOW)[0] == 2
    f.write_text("as_of,entity,metric\n2026-09-21,anthropic,panel_users\n")
    with pytest.raises(ContractError, match="line 2"):
        licensed.records_from_csv(f, "x", None)


def build(root, sid, chart):
    meta = registry.SOURCES[sid].meta
    return build_mart(registry.MARTS[f"licensed_data.{sid}_{chart}"], {sid: read_observations(root, meta)}, NOW, NAMES)


def test_every_chart_waits_for_data_and_says_how_to_add_it(root):
    registry.discover()
    for sid in ("yipit_consumer", "mscience_panel"):
        for chart in ("spend", "users", "growth"):
            spec = build(root, sid, chart)
            assert spec["status"] == "awaiting_data" and spec["sources"][0]["freshness"] == "never"
            assert "/add-manual-data" in spec["takeaway"][0]


def test_entered_data_shows_who_when_and_where_and_the_change(root):
    registry.discover()
    records = [rec(as_of=d, value=v) for d, v in (("2026-08-31", "40000000"), ("2026-09-28", "44000000"))]
    licensed.add(root, "yipit_consumer", records, entered_by="EwenCheung", now=NOW)
    spec = build(root, "yipit_consumer", "spend")
    assert spec["status"] == "ok" and spec["as_of"] == "2026-09-28"
    assert spec["takeaway"] == [
        "YipitData's panel puts Anthropic · Claude Pro's weekly spend at $44M in the week of 2026-09-28, +10% on the week of 2026-08-31. Last entered 2026-10-05 by EwenCheung."
    ]
    [src] = spec["sources"]
    assert (
        src["manual"]
        == {
            "entered_by": "EwenCheung",
            "entered_at": "2026-10-05T09:30:00Z",
            "evidence": "Weekly report 2026-10-05, p.3",
        }
        and src["freshness"] == "fresh"
    )
    assert {r["entered_by"] for r in spec["rows"]} == {"EwenCheung"} and spec["rows"][0][
        "series"
    ] == "Anthropic · Claude Pro"


def test_the_chart_turns_stale_when_nobody_keeps_entering_data(root):
    registry.discover()
    licensed.add(root, "yipit_consumer", [rec()], entered_by="EwenCheung", now=NOW)
    meta = registry.SOURCES["yipit_consumer"].meta
    spec = build_mart(
        registry.MARTS["licensed_data.yipit_consumer_spend"],
        {"yipit_consumer": read_observations(root, meta)},
        datetime(2026, 10, 25, tzinfo=UTC),
        NAMES,
    )
    assert spec["sources"][0]["freshness"] == "stale"  # 20 days on an 8-day SLA


def test_growth_is_entered_as_a_ratio_and_shown_as_a_percent(root):
    registry.discover()
    licensed.add(
        root, "mscience_panel", [rec(metric="panel_growth_yoy", value="0.25", dims={})], entered_by="E", now=NOW
    )
    spec = build(root, "mscience_panel", "growth")
    assert spec["takeaway"][0].startswith(
        "M Science's panel puts Anthropic's year-on-year growth at 25% in the week of 2026-09-28"
    )


def test_the_cli_reports_a_clear_error_and_writes_nothing(root, capsys, monkeypatch):
    monkeypatch.setattr(licensed, "ROOT", root)
    code = licensed.main(
        [
            "add",
            "yipit_consumer",
            "--as-of",
            "2026-09-28",
            "--entity",
            "anthropic",
            "--metric",
            "panel_users",
            "--value",
            "5",
            "--evidence",
            " ",
            "--entered-by",
            "E",
        ]
    )
    assert code == 1 and "nothing written" in capsys.readouterr().err
    assert (
        licensed.main(
            [
                "add",
                "nope",
                "--as-of",
                "2026-09-28",
                "--entity",
                "anthropic",
                "--metric",
                "panel_users",
                "--value",
                "5",
                "--evidence",
                "x",
            ]
        )
        == 2
    )
