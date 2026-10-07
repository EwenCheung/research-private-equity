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


def test_the_other_company_is_measured_over_the_same_weeks_as_a_market_check():
    g, events = event_series(0.4)
    market = pd.Series(np.random.default_rng(9).normal(0.02, 0.05, len(g)), index=g.index)  # no release effect
    hit = sig.event_profile(g, events, other=market)
    assert hit["other"]["p"] > 0.05 and hit["p"] < 0.01  # the release moved only the releasing company
    assert len(hit["other"]["obs"]) == len(hit["obs"]) == len(list(sig.OFFSETS))


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
    for i, d in enumerate(
        pd.date_range("2025-05-25", periods=12, freq="40D")
    ):  # Anthropic: Opus 4.1, Sonnet 4.2, Opus 4.3 ...
        tier, ver = ("Opus", "Sonnet")[i % 2], f"{4 + i // 4}.{i % 4 + 1}"
        rows["model_releases"].append(
            obs(
                "model_releases",
                "model_release",
                d.date().isoformat(),
                1,
                "anthropic",
                model=f"anthropic/claude-{tier.lower()}-{ver}",
                name=f"Claude {tier} {ver}",
            )
        )
        slug = f"anthropic/claude-{tier.lower()}-{ver}-20260101"
        scores(
            rows,
            "anthropic",
            slug,
            f"Claude {tier} {ver} (Max)",
            f"claude-{tier.lower()}-{ver.replace('.', '-')}-high",
            20 + 2 * i,
            1400 + 5 * i,
            0.5 - 0.02 * i,
        )
    for i in range(10, 20):  # OpenAI's GPT-10 ... GPT-19, each scored; GPT-19 has no price
        scores(
            rows,
            "openai",
            f"openai/gpt-{i}-2025-01-01",
            f"GPT-{i} (High)",
            f"gpt-{i}-high",
            15 + i,
            1380 + 4 * i,
            None if i == 19 else 0.3,
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


def scores(rows, ent, slug, name, arena_name, index, arena, cost):
    """One model's Intelligence Index, GPQA Diamond accuracy and cost, and Arena score, as the two benchmark sources hold them."""
    rows["openrouter_benchmarks"].append(
        obs(
            "openrouter_benchmarks",
            "openrouter_aa_intelligence_index",
            "2026-10-06",
            index,
            ent,
            model=name,
            permaslug=slug,
        )
    )
    if cost is not None:
        rows["openrouter_benchmarks"].append(
            obs(
                "openrouter_benchmarks",
                "openrouter_eval_accuracy",
                "2026-10-03",
                0.8,
                ent,
                model=name,
                permaslug=slug,
                benchmark="gpqa_diamond",
                cost_per_task_usd=cost,
                tasks=198,
                stddev=0.02,
            )
        )
    rows["arena_text_leaderboard"].append(
        obs("arena_text_leaderboard", "arena_text_score", "2026-10-02", arena, ent, model=arena_name)
    )


def registry_sources() -> list[str]:
    registry.discover()
    return list(registry.SOURCES)


@pytest.mark.parametrize(
    "mart_id", sorted(m for m in (registry.discover() or registry.MARTS) if m.startswith("signal."))
)
def test_every_signal_chart_is_a_valid_chart_spec_with_rows(mart_id):
    spec = build_mart(registry.MARTS[mart_id], world(), NOW, {"anthropic": "Anthropic", "openai": "OpenAI"})
    assert (
        spec["status"] == "ok"
        and spec["rows"]
        and spec["kind"] == ("table" if mart_id == "signal.model_table" else "combo")
    )
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
    assert any("(" in r["release"] for r in mine + theirs)  # a scored model's caption carries its Intelligence Index
    assert spec["rows"][0]["week"] >= "2025-03-01"  # starts 12 weeks before the first Anthropic release, not at launch
    growth = [r for r in spec["rows"] if not r["release"] and not r["model"]]
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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("anthropic/claude-4.1-opus-20250805", ("opus", (4, 1), "Opus 4.1")),
        ("anthropic/claude-opus-5.5-20260921", ("opus", (5, 5), "Opus 5.5")),
        ("anthropic/claude-5-fable-20260609", ("fable", (5, 0), "Fable 5")),
        ("claude-opus-4-1-20250805-thinking-16k", ("opus", (4, 1), "Opus 4.1")),
        ("claude-sonnet-4-20250514", ("sonnet", (4, 0), "Sonnet 4")),  # the date is not a minor version
        ("claude-3-5-sonnet-20241022", ("sonnet", (3, 5), "Sonnet 3.5")),
        ("openai/gpt-5.2-20251211", ("GPT", (5, 2), "GPT-5.2")),
        ("gpt-5.2-chat-latest-20260210", ("GPT", (5, 2), "GPT-5.2")),
        ("gpt-4.1-mini-2025-04-14", ("GPT", (4, 1), "GPT-4.1")),
        ("gpt-5-high", ("GPT", (5, 0), "GPT-5")),
        ("openai/o3-2025-04-16", ("o", (3, 0), "o3")),
        ("openai/gpt-4o-2024-05-13", ("GPT-4o", (4, 0), "GPT-4o")),
        ("openai/gpt-oss-120b", ("gpt-oss", (0, 0), "gpt-oss")),
        ("anthropic/claude-instant-1", None),
    ],
)
def test_benchmark_and_arena_names_are_matched_to_the_model_line_they_belong_to(text, expected):
    assert sig.line_of(text) == expected


def test_a_line_takes_its_best_model_with_that_models_own_cost_and_the_best_arena_variant():
    rows = [
        obs(
            "openrouter_benchmarks",
            "openrouter_aa_intelligence_index",
            "2026-10-06",
            20.0,
            "openai",
            model="GPT-5",
            permaslug="openai/gpt-5-2025-08-07",
        ),
        obs(
            "openrouter_benchmarks",
            "openrouter_aa_intelligence_index",
            "2026-10-06",
            25.0,
            "openai",
            model="GPT-5 Codex",
            permaslug="openai/gpt-5-codex",
        ),
        obs(
            "openrouter_benchmarks",
            "openrouter_eval_accuracy",
            "2026-10-03",
            0.85,
            "openai",
            model="GPT-5",
            permaslug="openai/gpt-5-2025-08-07",
            benchmark="gpqa_diamond",
            cost_per_task_usd=0.21,
        ),
        obs(
            "openrouter_benchmarks",
            "openrouter_eval_accuracy",
            "2026-10-03",
            0.9,
            "openai",
            model="GPT-5 Codex",
            permaslug="openai/gpt-5-codex",
            benchmark="gpqa_diamond",
            cost_per_task_usd=0.5,
        ),
        obs(
            "openrouter_benchmarks",
            "openrouter_eval_accuracy",
            "2026-10-03",
            0.5,
            "openai",
            model="GPT-5 Codex",
            permaslug="openai/gpt-5-codex",
            benchmark="tau_bench_verified_airline",
            cost_per_task_usd=9.0,
        ),
        obs("arena_text_leaderboard", "arena_text_score", "2026-10-02", 1400.0, "openai", model="gpt-5-chat"),
        obs("arena_text_leaderboard", "arena_text_score", "2026-10-02", 1435.0, "openai", model="gpt-5-high"),
    ]
    card = sig.scorecard(Ctx(rows, {}), "openai")["gpt-5"]
    assert (card["index"], card["accuracy"], card["cost"], card["arena"]) == (
        25.0,
        0.9,
        0.5,
        1435.0,
    )  # the GPQA row, not the airline one


def models(*specs):
    """Anthropic lines as (name, index, cost); each is listed in its own week."""
    rows = []
    for i, (name, index, cost) in enumerate(specs):
        d = (pd.Timestamp("2025-06-01") + pd.Timedelta(weeks=3 * i)).date().isoformat()
        tier, ver = name.split()
        rows.append(
            obs(
                "model_releases",
                "model_release",
                d,
                1,
                "anthropic",
                model=f"anthropic/claude-{tier.lower()}-{ver}",
                name=f"Claude {name}",
            )
        )
        scores(
            {"openrouter_benchmarks": rows, "arena_text_leaderboard": rows},
            "anthropic",
            f"anthropic/claude-{tier.lower()}-{ver}-20260101",
            f"Claude {name}",
            f"claude-{tier.lower()}-{ver}",
            index,
            1400,
            cost,
        )
    return Ctx(rows, {})


def test_each_model_is_compared_with_the_previous_line_of_its_own_kind_and_called_plainly():
    ctx = models(
        ("Opus 4.1", 20, 0.50),
        ("Sonnet 4.2", 10, 0.10),
        ("Opus 4.3", 25, 0.30),
        ("Sonnet 4.4", 9.8, 0.30),
        ("Opus 4.5", 25.4, 0.31),
    )
    got = {m["release"]: (m["after"], m["verdict"]) for m in sig.new_models(ctx, pd.Timestamp("2025-01-01"))}
    assert got == {
        "Opus 4.1": (None, "First in its line"),
        "Sonnet 4.2": (None, "First in its line"),
        "Opus 4.3": ("Opus 4.1", "Higher score, cheaper"),  # +25% on the score, -40% on price
        "Sonnet 4.4": ("Sonnet 4.2", "Same score, pricier"),  # -2%, +200%
        "Opus 4.5": ("Opus 4.3", "Same score, same price"),  # +1.6%, +3.3%
    }


def test_a_mini_model_is_not_compared_with_a_full_size_one():
    rows = [
        obs("model_releases", "model_release", "2025-06-01", 1, "openai", model="openai/o3", name="o3"),
        obs("model_releases", "model_release", "2025-07-01", 1, "openai", model="openai/o4-mini", name="o4 Mini"),
    ]
    scores(
        {"openrouter_benchmarks": rows, "arena_text_leaderboard": rows},
        "openai",
        "openai/o3-2025-04-16",
        "o3",
        "o3-2025-04-16",
        20.0,
        1390,
        0.2,
    )
    scores(
        {"openrouter_benchmarks": rows, "arena_text_leaderboard": rows},
        "openai",
        "openai/o4-mini-2025-04-16",
        "o4-mini (High)",
        "o4-mini-high",
        16.0,
        1380,
        0.1,
    )
    got = {m["release"]: m["verdict"] for m in sig.new_models(Ctx(rows, {}), pd.Timestamp("2025-01-01"))}
    assert got == {"o3": "First in its line", "o4": "No like-for-like comparison"}


def test_the_release_chart_carries_each_models_score_and_change_in_panels_under_the_growth_lines():
    spec = build_mart(registry.MARTS["signal.releases"], world(), NOW)
    panels = {layer["name"]: layer.get("panel", 0) for layer in spec["layers"]}
    assert [p["label"] for p in spec["panels"]] == ["Intelligence Index", "Change on previous model"]
    assert all(p["x"]["field"] == "model" for p in spec["panels"])  # one column per model, not a share of the time axis
    assert panels == {
        "Anthropic": 0,
        "OpenAI": 0,
        "Release": 0,
        "Intelligence Index of the model": 1,
        "Score change on previous model": 2,
        "Price per task change": 2,
    }
    models = [r for r in spec["rows"] if r["model"]]
    assert models and all(r["index"] is not None and r["company"] in ("Anthropic", "OpenAI") for r in models)
    assert any(r["d_index"] is not None for r in models) and any(r["d_price"] is not None for r in models)
    assert "scored higher" in spec["takeaway"][0] and "cost less per task" in spec["takeaway"][0]
    assert "signal.model_change" not in registry.MARTS  # no separate chart for it
    table = build_mart(registry.MARTS["signal.model_table"], world(), NOW)
    assert table["rows"][0]["listed"] > table["rows"][-1]["listed"]  # newest first
    assert any(r["verdict"] == "First in its line" for r in table["rows"])


def kinds(spec):
    return {layer["name"]: layer["mark"] for layer in spec["layers"]}


def test_the_market_or_own_chart_compares_four_dimensions_and_splits_the_gap():
    spec = build_mart(registry.MARTS["signal.market_or_own"], world(), NOW)
    assert {r["dimension"] for r in spec["rows"]} == set(sig.DIMENSIONS) and {r["company"] for r in spec["rows"]} == {
        "Anthropic",
        "OpenAI",
    }
    assert kinds(spec) == {
        "Anthropic's own (gap to OpenAI)": "bar",
        "Growth over 13 weeks": "point",
    }  # companies share one mark, the gap has its own
    by = {(r["dimension"], r["company"]): r for r in spec["rows"]}
    for dim in sig.DIMENSIONS:
        a, o = by[dim, "Anthropic"], by[dim, "OpenAI"]
        assert a["gap"] == pytest.approx(a["growth"] - o["growth"]) and o["gap"] is None  # the gap is drawn once


def test_the_release_effect_chart_has_both_companies_around_both_sets_of_releases_with_one_mark_for_each():
    spec = build_mart(registry.MARTS["signal.release_effect"], world(), NOW)
    assert kinds(spec) == {
        "Normal week": "band",
        "Around Anthropic's releases": "line",
        "Around OpenAI's releases": "point",
    }
    assert len(spec["rows"]) == 2 * len(list(sig.OFFSETS)) and {r["company"] for r in spec["rows"]} == {
        "Anthropic",
        "OpenAI",
    }
    assert all(r["around_a"] is not None and r["around_o"] is not None for r in spec["rows"])
    assert len(spec["takeaway"]) == 2 and "Anthropic" in spec["takeaway"][0] and "OpenAI" in spec["takeaway"][1]
    assert sum(r["low"] is not None for r in spec["rows"]) == len(
        list(sig.OFFSETS)
    )  # the grey range is drawn once per week


def test_the_lead_chart_shows_own_signals_and_the_edge_over_openai_and_says_what_a_lead_is():
    spec = build_mart(registry.MARTS["signal.lead_lag"], world(), NOW)
    assert kinds(spec) == {"Luck": "band", "Anthropic's own signals": "bar", "Beyond OpenAI": "line"}
    assert "(vs OpenAI)" in spec["subtitle"] and any(
        "A lead is one signal moving first" in a for a in spec["assumptions"]
    )
    assert (
        len(spec["takeaway"]) == 2
        and spec["takeaway"][0].startswith("Own signals")
        and spec["takeaway"][1].startswith("Beyond OpenAI")
    )


def test_the_valuation_chart_carries_both_companies_and_the_round_to_round_multiples():
    spec = build_mart(registry.MARTS["signal.valuation"], world(), NOW)
    assert {n for n, mark in kinds(spec).items() if mark == "line"} == {"Anthropic", "OpenAI"}
    assert "Round to round" in spec["takeaway"][1] and "x" in spec["takeaway"][1]
