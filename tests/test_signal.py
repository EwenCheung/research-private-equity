import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline.core import registry
from pipeline.core.build import Ctx, build_mart
from pipeline.marts import signal as sig
from pipeline.sources import signal as src

NOW = datetime(2026, 10, 7, 9, tzinfo=UTC)
WEEKS = pd.date_range("2023-01-01", periods=190, freq="W-SUN")  # Sundays


def series(values, start="2023-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="W-SUN"))


# ---- the statistics ----


def test_weekly_keeps_whole_weeks_and_scales_a_week_missing_a_day_or_two():
    days = pd.date_range("2026-09-02", "2026-10-02")  # Wednesday to Friday: both ends are partial weeks
    daily = pd.Series(7.0, index=days)
    daily["2026-09-16"] = np.nan  # one clean day missing from the week of 14 Sep
    daily = daily.dropna()
    week = sig.weekly(daily)
    assert list(week.index) == [
        pd.Timestamp(d) for d in ("2026-09-13", "2026-09-20", "2026-09-27")
    ]  # no 6 Sep, no 4 Oct
    assert week.round(6).tolist() == [49.0, 49.0, 49.0]  # six days at 7 scaled to seven


def test_whitening_removes_a_series_own_momentum():
    rng = np.random.default_rng(0)
    g = np.zeros(300)
    for t in range(1, 300):
        g[t] = 0.7 * g[t - 1] + rng.standard_normal()
    raw, white = pd.Series(g), sig.whiten(series(g))
    assert raw.autocorr(1) > 0.5 and abs(white.autocorr(1)) < 0.1


def test_fdr_adjusts_p_values_in_the_order_given():
    assert sig.fdr([0.04, 0.01, 0.03, 0.005]) == pytest.approx([0.04, 0.02, 0.04, 0.02])


def test_a_planted_lead_is_found_at_the_right_lag_and_survives_the_correction():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(200)
    y = 0.7 * np.roll(x, 3) + rng.standard_normal(200)  # x leads y by 3 weeks
    t = sig.lead_test(series(x), series(y))
    assert t["lag"] == 3 and t["r"] > 0.4 and t["p"] < 0.01
    r1, r2, *_ = sig.halves(series(x), series(y), 3)
    assert sig.verdict(t["p"], t["r"], t["n"], r1, r2) == sig.FINDING


def test_independent_series_are_not_reported_as_leads():
    """Type I error: pairs with no relationship, one of them persistent, are significant about as often as chance says."""
    ps = []
    for seed in range(40):
        rng = np.random.default_rng(100 + seed)
        y = np.zeros(150)
        for t in range(1, 150):
            y[t] = 0.5 * y[t - 1] + rng.standard_normal()
        ps.append(sig.lead_test(sig.ranked(series(rng.standard_normal(150))), sig.ranked(series(y)), seed=seed)["p"])
    assert np.mean(np.array(ps) < 0.05) <= 0.15  # 5% expected, so 6 of 40 is already generous
    assert min(sig.fdr(ps)) >= 0.05  # and none survives the correction for forty tries


def test_one_extreme_week_cannot_make_a_lead():
    rng = np.random.default_rng(2)
    x, y = rng.standard_normal(150), rng.standard_normal(150)
    x[40], y[43] = 40.0, 40.0  # one huge week in each, three weeks apart
    plain = sig.lead_test(series(x), series(y))
    ranked = sig.lead_test(sig.ranked(series(x)), sig.ranked(series(y)))
    assert plain["lag"] == 3 and plain["r"] > 0.5 and ranked["r"] < 0.3


def test_the_lead_test_is_deterministic():
    rng = np.random.default_rng(3)
    x, y = series(rng.standard_normal(120)), series(rng.standard_normal(120))
    assert sig.lead_test(x, y) == sig.lead_test(x, y)


@pytest.mark.parametrize(
    "q, r, n, r1, r2, expected",
    [
        (0.2, 0.3, 200, 0.3, 0.3, sig.UNSUPPORTED),  # does not survive the correction
        (0.01, 0.3, 200, 0.35, 0.05, sig.HYPOTHESIS),  # fades in the second half
        (0.01, 0.3, 200, 0.35, -0.2, sig.HYPOTHESIS),  # reverses
        (0.01, 0.3, 80, 0.3, 0.3, sig.HYPOTHESIS),  # too few weeks to check the halves
        (0.01, 0.3, 200, 0.32, 0.28, sig.FINDING),
    ],
)
def test_verdict_needs_the_correction_and_both_halves(q, r, n, r1, r2, expected):
    assert sig.verdict(q, r, n, r1, r2) == expected


def event_series(jump: float):
    rng = np.random.default_rng(4)
    g = rng.normal(0.02, 0.05, 190)
    events = list(range(20, 180, 12))
    for e in events:
        g[e + 5 : e + 8] += jump
    return pd.Series(g, index=WEEKS), [WEEKS[e] for e in events]


def test_the_event_study_sees_a_planted_jump_a_month_after_and_not_when_there_is_none():
    g, events = event_series(0.4)
    hit = sig.event_profile(g, events)
    assert hit["p"] < 0.01 and hit["month"] > hit["month_random"] + 0.5
    assert (hit["obs"] > hit["high"]).sum() >= 2  # weeks +5 to +7 stand out
    g, events = event_series(0.0)
    assert sig.event_profile(g, events)["p"] > 0.05


def test_the_event_study_refuses_too_few_events():
    g, events = event_series(0.0)
    assert sig.event_profile(g, events[:3]) is None


def test_blocks_count_back_from_the_latest_week():
    level = series(range(1, 31))  # 30 weeks: two blocks of 13, the first four weeks are left out
    assert sig.blocks(level).tolist() == [sum(range(5, 18)), sum(range(18, 31))]


def test_a_release_is_a_new_numbered_model_line_not_a_variant():
    assert [
        sig.family(n)
        for n in (
            "GPT-5 Mini",
            "GPT-5.1-Codex",
            "o3 Pro",
            "o4 Mini High",
            "gpt-oss-120b",
            "GPT Audio",
            "Claude Opus 4.1",
        )
    ] == [
        "GPT-5",
        "GPT-5.1",
        "o3",
        "o4",
        "gpt-oss",
        None,
        "Opus 4.1",
    ]


def test_only_the_first_listing_of_a_line_counts_as_its_release():
    rows = [
        obs("model_releases", "model_release", d, 1, "openai", model=f"openai/{n}", name=n)
        for d, n in (
            ("2025-08-07", "GPT-5"),
            ("2025-08-07", "GPT-5 Mini"),
            ("2025-10-06", "GPT-5 Pro"),
            ("2025-11-13", "GPT-5.1"),
        )
    ]
    got = sig.release_weeks(Ctx(rows, {}), "openai")
    assert got == {pd.Timestamp("2025-08-10"): ["GPT-5"], pd.Timestamp("2025-11-16"): ["GPT-5.1"]}


# ---- the sources ----


def test_models_are_dated_by_listing_and_variants_are_left_out():
    payload = {
        "data": [
            {"id": "anthropic/claude-opus-4.1", "name": "Anthropic: Claude Opus 4.1", "created": 1754800000},
            {
                "id": "anthropic/claude-opus-4.1:batch",
                "name": "Anthropic: Claude Opus 4.1 (Batch)",
                "created": 1754800000,
            },
            {"id": "openai/gpt-5", "name": "OpenAI: GPT-5", "created": 1754000000},
            {"id": "anthropic/old", "name": "Old", "created": None},
        ]
    }
    rows = list(src.parse_models(payload, "anthropic", "anthropic"))
    assert [(r["dims"]["model"], r["dims"]["name"], r["as_of"]) for r in rows] == [
        ("anthropic/claude-opus-4.1", "Claude Opus 4.1", "2025-08-10")
    ]
    assert (
        rows[0]["source_url"] == "https://openrouter.ai/anthropic/claude-opus-4.1" and rows[0]["entity"] == "anthropic"
    )


# ---- the charts, end to end on a synthetic world ----


def obs(source, metric, as_of, value, entity, **dims):
    return {
        "source": source,
        "source_url": "https://x/i",
        "method": "ledger" if source == "signal_funding_rounds" else "api",
        "as_of": as_of,
        "retrieved_at": "2026-10-06T00:00:00Z",
        "tier": "company-stated",
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": "tester" if source == "signal_funding_rounds" else None,
        "evidence": "quote" if source == "signal_funding_rounds" else None,
    }


def world(jump=False) -> dict[str, list[dict]]:
    rng = np.random.default_rng(5)
    days = pd.date_range("2023-01-01", "2026-09-27")  # Sunday to Sunday: whole weeks
    rows: dict[str, list[dict]] = {s: [] for s in registry_sources()}
    for ent in ("anthropic", "openai"):
        for source, metric, extra in (
            ("pypi_downloads", "pypi_downloads", {"package": ent, "role": "sdk"}),
            ("npm_downloads", "npm_downloads", {"package": ent, "role": "sdk"}),
            ("npm_downloads", "npm_downloads", {"package": ent + "-cli", "role": "cli"}),
        ):
            level = np.exp(np.cumsum(rng.normal(0.004, 0.02, len(days)))) * 1000
            rows[source] += [
                obs(source, metric, d.date().isoformat(), float(v), ent, **extra)
                for d, v in zip(days, level, strict=True)
            ]
        for kind, title in (("company", ent.title()), ("product", ent.title() + " app")):
            level = np.exp(np.cumsum(rng.normal(0, 0.03, len(days)))) * 5000
            rows["wikipedia_pageviews"] += [
                obs(
                    "wikipedia_pageviews", "wiki_pageviews", d.date().isoformat(), float(v), ent, title=title, kind=kind
                )
                for d, v in zip(days, level, strict=True)
            ]
    rows["github_coauthored_commits"] = [
        obs(
            "github_coauthored_commits",
            "coauthored_commits",
            d.date().isoformat(),
            float(1000 + 40 * i + rng.normal(0, 80)),
            "anthropic",
            incomplete=False,
        )
        for i, d in enumerate(pd.date_range("2025-03-03", "2026-09-21", freq="7D"))
    ]
    for i, d in enumerate(
        pd.date_range("2024-02-04", periods=20, freq="40D")
    ):  # OpenAI: a line, its Mini variant, and an unversioned name
        for name in (f"GPT-{i}", f"GPT-{i} Mini", "GPT Audio"):
            rows["model_releases"].append(
                obs(
                    "model_releases",
                    "model_release",
                    d.date().isoformat(),
                    1,
                    "openai",
                    model=f"openai/{name}",
                    name=name,
                )
            )
    for d in pd.date_range("2025-05-25", periods=12, freq="40D"):
        rows["model_releases"].append(
            obs(
                "model_releases",
                "model_release",
                d.date().isoformat(),
                1,
                "anthropic",
                model=f"anthropic/claude-m{d.month}{d.day}",
                name=f"Anthropic: Claude Model {d.month}-{d.day}".split(": ", 1)[1],
            )
        )
    for d, name, value in (
        ("2025-03-03", "Series E", 61.5e9),
        ("2025-09-02", "Series F", 183e9),
        ("2026-02-12", "Series G", 380e9),
        ("2026-05-28", "Series H", 965e9),
    ):
        rows["signal_funding_rounds"].append(
            obs("signal_funding_rounds", "round_post_money_usd", d, value, "anthropic", round=name)
        )
    return rows


def registry_sources() -> list[str]:
    registry.discover()
    return list(registry.SOURCES)


@pytest.mark.parametrize(
    "mart_id", sorted(m for m in (registry.discover() or registry.MARTS) if m.startswith("signal."))
)
def test_every_signal_chart_is_a_valid_chart_spec_with_rows(mart_id):
    spec = build_mart(registry.MARTS[mart_id], world(), NOW, {"anthropic": "Anthropic", "openai": "OpenAI"})
    assert spec["status"] == "ok" and spec["rows"] and spec["kind"] == "combo"
    assert len(spec["takeaway"]) <= 2 and all(isinstance(t, str) and t for t in spec["takeaway"])
    json.dumps(spec)  # no NaN reaches the browser


def test_a_chart_without_its_data_says_it_is_waiting_not_that_nothing_was_found():
    for mart_id in (m for m in registry.MARTS if m.startswith("signal.")):
        spec = build_mart(registry.MARTS[mart_id], {s: [] for s in registry_sources()}, NOW)
        assert spec["status"] == "awaiting_data" and spec["rows"] == []


def test_the_release_chart_marks_each_release_week_once_and_names_it():
    spec = build_mart(registry.MARTS["signal.releases"], world(), NOW)
    marked = [r for r in spec["rows"] if r["release"]]
    mine = [r for r in marked if r["company"] == "Anthropic"]
    theirs = [r for r in marked if r["company"] == "OpenAI"]
    assert len(mine) == 12 and 0 < len(theirs) < 20  # the Mini variants and the unversioned name are not releases
    assert all(r["release"].startswith("GPT-") and "Mini" not in r["release"] for r in theirs)
    assert spec["rows"][0]["week"] >= "2025-03-01"  # starts 12 weeks before the first Anthropic release, not at launch
    growth = [r for r in spec["rows"] if not r["release"]]
    assert all(r["anthropic"] is not None or r["openai"] is not None for r in growth)


def test_a_planted_lead_reaches_the_tested_chart_and_the_nulls_stay_not_supported():
    w = world()
    rng = np.random.default_rng(6)
    # make npm follow PyPI by 3 weeks: npm's daily level is PyPI's, delayed, with a little noise
    py = {r["as_of"]: r["value"] for r in w["pypi_downloads"] if r["entity"] == "anthropic"}
    for r in w["npm_downloads"]:
        if r["entity"] == "anthropic" and r["dims"]["role"] == "sdk":
            day = (pd.Timestamp(r["as_of"]) - timedelta(days=21)).date().isoformat()
            r["value"] = float(py.get(day, 1000.0) * np.exp(rng.normal(0, 0.1)))
    spec = build_mart(registry.MARTS["signal.tested"], w, NOW)
    top = spec["rows"][0]
    assert (
        "PyPI downloads → npm downloads" in top["pair"]
        and "(3 weeks)" in top["pair"]
        and top["verdict"] != sig.UNSUPPORTED
    )
    assert sum(r["verdict"] == sig.UNSUPPORTED for r in spec["rows"]) >= len(spec["rows"]) // 2


def test_every_comparison_chart_carries_openai_so_a_move_reads_as_company_or_market():
    for mart_id, releaser in (("signal.around_release", "Anthropic"), ("signal.around_openai_release", "OpenAI")):
        spec = build_mart(registry.MARTS[mart_id], world(), NOW)
        drawn = {layer["name"] for layer in spec["layers"] if layer["mark"] in ("bar", "line")}
        assert drawn == {"Anthropic", "OpenAI"} and releaser in spec["title"]
        assert all(r["other"] is not None for r in spec["rows"])
        assert "over the same weeks" in spec["takeaway"][1]  # the other company, as the market
    for mart_id in ("signal.releases", "signal.valuation"):
        spec = build_mart(registry.MARTS[mart_id], world(), NOW)
        assert {layer["name"] for layer in spec["layers"] if layer["mark"] == "line"} == {"Anthropic", "OpenAI"}
    steps = build_mart(registry.MARTS["signal.valuation_steps"], world(), NOW)
    assert {"Anthropic", "OpenAI"} <= {r["signal"] for r in steps["rows"]}
    gap = build_mart(registry.MARTS["signal.vs_openai"], world(), NOW)
    assert "Anthropic's own" in gap["takeaway"][1] or "market outgrew" in gap["takeaway"][1]


def test_both_lead_charts_name_a_pair_and_say_what_a_lead_is():
    for mart_id, edge in (("signal.lead_lag", False), ("signal.lead_lag_edge", True)):
        spec = build_mart(registry.MARTS[mart_id], world(), NOW)
        assert ("(vs OpenAI)" in spec["subtitle"]) == edge
        assert any("A lead is one signal moving first" in a for a in spec["assumptions"])
