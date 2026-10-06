from datetime import UTC, datetime

import pandas as pd
import pytest
import yaml

from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import briefing as b
from pipeline.marts import dev_adoption

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


def monthly(values, start="2025-01", agg="sum", fmt="num"):
    idx = pd.period_range(start, periods=len(values), freq="M")
    return {"freq": "M", "agg": agg, "fmt": fmt, "s": pd.Series(values, index=idx, dtype=float)}


CFG = {"label": "Signal", "band": 0.10}


# ---- windows and readings ----


def test_a_signal_compares_the_latest_three_months_with_the_three_before_and_a_year_earlier():
    values = [10] * 12 + [20, 20, 20] + [30, 30, 30]  # 18 months
    r = b.read("x", CFG, monthly(values))
    assert r["recent"] == 90 and r["chg"] == pytest.approx(0.5) and r["prev"] == pytest.approx(1.0)
    assert r["yoy"] == pytest.approx(30 * 3 / (10 * 3) - 1)
    assert r["direction"] == "up" and r["based"] == "Apr to Jun 2026"


def test_a_short_history_leaves_the_longer_comparisons_blank_not_zero():
    r = b.read("x", CFG, monthly([10, 10, 10, 20, 20, 20]))
    assert r["chg"] == pytest.approx(1.0) and r["prev"] is None and r["yoy"] is None
    assert b.read("x", CFG, monthly([10, 20])) is None  # fewer than three months: no reading


def test_a_change_inside_the_band_is_flat_and_the_band_can_be_set_per_signal():
    assert b.read("x", CFG, monthly([100] * 3 + [105] * 3))["direction"] == "flat"
    assert b.read("x", {**CFG, "band": 0.02}, monthly([100] * 3 + [105] * 3))["direction"] == "up"


def test_fewer_incidents_is_good_news_so_a_fall_reads_as_good_when_better_is_down():
    r = b.read("incidents", {**CFG, "better": "down"}, monthly([10] * 3 + [5] * 3))
    assert r["direction"] == "down" and r["tone"] == "good"
    assert b.read("x", CFG, monthly([10] * 3 + [5] * 3))["tone"] == "bad"


def test_a_mean_series_ignores_missing_months_and_needs_half_the_window():
    s = monthly([100, None, 100, 200, 200, None], agg="mean")
    assert b.read("x", CFG, s)["chg"] == pytest.approx(1.0)  # 100 -> 200 over the months that exist
    assert b.read("x", CFG, monthly([100, 100, 100, None, None, None], agg="mean")) is None


def test_fund_marks_arrive_as_a_change_and_read_the_same_way():
    r = b.read("fund_marks", {"label": "Marks", "band": 0.02}, {"direct": 0.5, "n": 12, "as_of": "2026-07-31"})
    assert r["chg"] == 0.5 and r["direction"] == "up" and r["yoy"] is None and "12 funds" in r["text"]


# ---- families ----


def tones(*t):
    return [{"tone": x} for x in t]


@pytest.mark.parametrize(
    ("members", "words", "verdict"),
    [
        (("good", "good", "good"), "volume", "Rising"),
        (("good", "good", "flat"), "volume", "Rising"),
        (("good", "flat"), "volume", "Mixed"),  # one flat signal is not agreement
        (("good", "good", "bad"), "volume", "Mixed"),
        (("good", "bad"), "volume", "Mixed"),
        (("bad", "bad"), "volume", "Falling"),
        (("flat", "flat"), "volume", "Flat"),
        (("good",), "quality", "Improving"),
        (("bad",), "quality", "Worsening"),
        (("flat",), "quality", "Steady"),
    ],
)
def test_family_verdicts(members, words, verdict):
    assert b.family_verdict(tones(*members), words) == verdict


def test_a_divergence_names_the_falling_family_against_the_rising_ones():
    cfg = {"a": {"short": "consumer attention"}, "b": {"short": "developer usage"}, "c": {"short": "build-out"}}
    assert b.divergences(cfg, {"a": "Falling", "b": "Rising", "c": "Rising"}) == [
        "Consumer attention is falling while developer usage and build-out are rising."
    ]
    assert b.divergences(cfg, {"a": "Rising", "b": "Rising", "c": "Mixed"}) == []


# ---- against OpenAI ----


def test_versus_compares_the_change_and_how_big_anthropic_is_next_to_openai():
    a = b.read("x", CFG, monthly([100] * 3 + [150] * 3))  # +50%, 450 in the window
    o = b.read("x", CFG, monthly([100] * 3 + [120] * 3))  # +20%, 360 in the window
    v = b.versus(a, o, "up")
    assert v["edge"] == "ahead" and v["gap"] == pytest.approx(30) and v["size"] == pytest.approx(450 / 360)
    assert b.versus_text(v) == "Ahead by 30 pts, 1.2× OpenAI's level"
    assert b.versus(a, None, "up") is None and b.versus_text(None) == "No OpenAI series"


def test_a_gap_inside_five_points_is_level_and_for_incidents_a_bigger_fall_is_ahead():
    a = b.read("x", CFG, monthly([100] * 3 + [110] * 3))
    assert b.versus(a, b.read("x", CFG, monthly([100] * 3 + [107] * 3)), "up")["edge"] == "level"
    fewer = b.read("x", {**CFG, "better": "down"}, monthly([100] * 3 + [60] * 3))  # -40%
    more = b.read("x", {**CFG, "better": "down"}, monthly([100] * 3 + [90] * 3))  # -10%
    assert b.versus(fewer, more, "down")["edge"] == "ahead"
    assert b.versus(more, fewer, "down")["edge"] == "behind"


def test_compare_counts_ahead_level_and_behind_per_family_and_overall():
    mk = lambda sid, chg: {"id": sid, "chg": chg, "recent": 100.0}
    cfg = {"f": {"signals": {"a": {}, "b": {}, "c": {}, "d": {}}}}
    reads = {"f": [mk("a", 0.5), mk("b", 0.1), mk("c", -0.2), mk("d", 0.3)]}
    peer = {"a": mk("a", 0.1), "b": mk("b", 0.09), "c": mk("c", 0.2), "d": None}
    edges = b.compare(cfg, reads, peer)
    assert edges["f"] == {"ahead": 1, "level": 1, "behind": 1, "n": 3}
    assert b.oai_text(edges["f"]) == "Ahead on 1, level on 1, behind on 1 of 3"
    assert b.overall(edges) == [
        "Against OpenAI, Anthropic's change is ahead on 1 of 3 comparable signals and behind on 1."
    ]
    assert b.oai_text({"n": 0}) == "No OpenAI series" and b.overall({"f": {"n": 0, "ahead": 0, "behind": 0}}) == []


def test_openai_has_a_series_for_the_signals_both_companies_share_and_none_for_the_rest(shipped):
    rows, _ = shipped
    peer = b.read_peer(Ctx([r for sid in b.SOURCES for r in rows[sid]], NAMES))
    for sid in (
        "python_sdk",
        "js_sdk",
        "claude_code_downloads",
        "hn_job_posts",
        "sec_filers",
        "wiki_assistant",
        "wiki_company",
        "open_roles",
    ):
        assert peer[sid] is not None, sid
    for sid in ("claude_code_commits", "incidents", "fund_marks"):
        assert peer[sid] is None, sid


def test_the_moves_chart_puts_anthropic_and_openai_side_by_side_where_both_exist(shipped):
    rows, now = shipped
    spec = build_mart(registry.MARTS["briefing.moves"], rows, now, NAMES)
    assert spec["status"] == "ok" and spec["kind"] == "bar" and spec["encoding"]["color"]["field"] == "company"
    by = {}
    for r in spec["rows"]:
        by.setdefault(r["signal"], {})[r["company"]] = r["chg"]
    assert set(by["Python SDK"]) == {"Anthropic", "OpenAI"}
    assert set(by["Incidents"]) == {"Anthropic"}  # no OpenAI series: one bar, never a zero
    table = {r["signal"]: r for r in build_mart(registry.MARTS["briefing.signals"], rows, now, NAMES)["rows"]}
    assert by["Python SDK"]["Anthropic"] == pytest.approx(
        table["Python SDK downloads"]["chg"]
    )  # same number as the table
    assert spec["rows"][0]["company"] == "Anthropic"  # the first series keeps the first colour


# ---- tripwires ----


def read_of(values, **kw):
    return b.read("x", {**CFG, **kw}, monthly(values))


def test_consecutive_declines_counts_the_trailing_run_only():
    rule = {"type": "consecutive_declines", "n": 3}
    assert b.check(rule, read_of([1, 5, 9, 8, 7, 6]))[0] == "Fired"
    status, now = b.check(rule, read_of([9, 8, 7, 8, 7, 6]))
    assert status == "Clear" and now == "down 2 months in a row"
    assert b.check(rule, read_of([5, 6, 7, 8, 9, 10]))[1] == "not down last month"


def test_change_rules_compare_the_three_month_change_with_the_line():
    r = read_of([100] * 3 + [80] * 3)
    assert b.check({"type": "change_below", "value": -0.15}, r)[0] == "Fired"
    assert b.check({"type": "change_below", "value": -0.25}, r)[0] == "Clear"
    assert b.check({"type": "change_above", "value": 0.5}, read_of([100] * 3 + [160] * 3))[0] == "Fired"


def test_below_peak_measures_the_gap_to_the_best_period_in_the_window():
    r = read_of([10, 40, 30, 20, 20, 20])
    status, now = b.check({"type": "below_peak", "value": 0.5, "periods": 6}, r)
    assert status == "Fired" and now == "50% below the 6-month peak"


def test_a_tripwire_on_a_missing_signal_says_no_data_and_an_unknown_rule_is_an_error():
    assert b.check({"type": "change_below", "value": 0}, None) == ("No data", "n/a")
    with pytest.raises(ValueError, match="unknown"):
        b.check({"type": "nope"}, read_of([1] * 6))


def test_the_shipped_config_is_consistent():
    fams = b.config()
    signal_ids = {s for f in fams.values() for s in f["signals"]}
    assert all({"label", "short", "page", "signals"} <= set(f) for f in fams.values())
    for t in b.tripwire_rules():
        assert t["signal"] in signal_ids, t["id"]
        assert t["rule"]["type"] in {"consecutive_declines", "change_below", "change_above", "below_peak"}
    assert len({t["id"] for t in b.tripwire_rules()}) == len(b.tripwire_rules())
    yaml.safe_load((ROOT / "config/briefing.yaml").read_text())


# ---- the charts ----


@pytest.fixture(scope="module")
def shipped():
    registry.discover()
    rows = {sid: read_observations(ROOT, registry.SOURCES[sid].meta) for sid in b.SOURCES}
    return rows, datetime(2026, 10, 6, 12, tzinfo=UTC)


def test_the_three_charts_build_from_the_shipped_data(shipped):
    rows, now = shipped
    for mid in ("briefing.verdicts", "briefing.signals", "briefing.tripwires"):
        spec = build_mart(registry.MARTS[mid], rows, now, NAMES)
        assert spec["status"] == "ok" and spec["rows"], mid
    verdicts = build_mart(registry.MARTS["briefing.verdicts"], rows, now, NAMES)["rows"]
    assert [r["family"] for r in verdicts][:3] == ["Developer usage", "Enterprise adoption", "Consumer attention"]


def test_the_briefing_reads_the_same_numbers_as_the_chart_on_the_page(shipped):
    rows, now = shipped
    chart = build_mart(
        registry.MARTS["dev_adoption.js_sdk_monthly"], {"npm_downloads": rows["npm_downloads"]}, now, NAMES
    )
    mine = sorted(r["month"] for r in chart["rows"] if r["company"] == "Anthropic")[-3:]
    expected = sum(r["downloads"] for r in chart["rows"] if r["company"] == "Anthropic" and r["month"] in mine)
    ctx = Ctx([r for sid in b.SOURCES for r in rows[sid]], NAMES)
    sig = b.signals(ctx)["js_sdk"]
    assert b.window(sig["s"], 3, 0, "sum") == pytest.approx(expected)
    assert dev_adoption.monthly(ctx, "npm_downloads", "sdk") is not None


def test_no_data_means_awaiting_data_not_an_invented_verdict():
    for mid in ("briefing.verdicts", "briefing.signals"):
        assert (
            build_mart(registry.MARTS[mid], {sid: [] for sid in b.SOURCES}, datetime(2026, 10, 6, tzinfo=UTC), NAMES)[
                "status"
            ]
            == "awaiting_data"
        )
