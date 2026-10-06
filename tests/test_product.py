from datetime import UTC, datetime

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import product as marts
from pipeline.sources import product as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


def response(body):
    return httpx.Response(
        200, request=httpx.Request("GET", "https://x"), **({"text": body} if isinstance(body, str) else {"json": body})
    )


# ---- incident parsing ----


@pytest.mark.parametrize(
    ("text", "year", "month", "start", "end"),
    [
        ("Oct 1, 16:20 - 22:36 UTC", 2026, 10, "2026-10-01T16:20", "2026-10-01T22:36"),
        (
            "Sep 30, 23:50 - Oct 1, 00:10 UTC",
            2025,
            10,
            "2025-09-30T23:50",
            "2025-10-01T00:10",
        ),  # listed under the month it ended in
        (
            "Dec 31, 23:00 - Jan 1, 01:00 UTC",
            2026,
            1,
            "2025-12-31T23:00",
            "2026-01-01T01:00",
        ),  # ...so the start is last year
        ("Nov 7, 22:47 - 22:47 UTC", 2025, 11, "2025-11-07T22:47", "2025-11-07T22:47"),
        ("Oct 1, 16:20 UTC", 2026, 10, "2026-10-01T16:20", None),  # no end time posted
    ],
)
def test_parse_span(text, year, month, start, end):
    s, e = src.parse_span(text, year, month)
    assert s.strftime("%Y-%m-%dT%H:%M") == start and (e.strftime("%Y-%m-%dT%H:%M") if e else None) == end


def test_unrecognised_time_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="unrecognised"):
        src.parse_span("Ongoing", 2026, 10)


def history_page(*months):
    return {"months": [{"name": n, "year": y, "incidents": inc} for n, y, inc in months]}


def inc(code, ts, impact="minor"):
    return {"code": code, "name": f"Incident {code}", "impact": impact, "timestamp": f"<var>{ts}</var>"}


def test_history_pages_until_the_cutoff_and_dedupes_nothing_it_does_not_need(monkeypatch):
    pages = {
        1: history_page(
            ("October", 2026, [inc("new", "Oct 1, 16:20 - 22:36 UTC", "major")]),
            ("September", 2026, []),
            ("August", 2026, []),
        ),
        2: history_page(
            ("February", 2023, [inc("old", "Feb 3, 10:00 - 11:00 UTC")]),
            ("January", 2023, [inc("older", "Jan 5, 10:00 - 11:00 UTC")]),
        ),
        3: {"months": []},
    }
    seen = []
    monkeypatch.setattr(
        src, "get", lambda url, params=None, **k: seen.append(params["page"]) or response(pages[params["page"]])
    )
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    rows = list(src.status_incidents(load_companies(ROOT)["anthropic"]))
    assert [r["dims"]["code"] for r in rows] == ["new", "old", "older"] and seen == [1, 2, 3]
    assert (
        rows[0]["dims"] | {"minutes": 376} == rows[0]["dims"]
        and rows[0]["source_url"] == "https://status.claude.com/incidents/new"
    )
    # incremental: with data to Oct 1, one page is enough and old incidents are skipped
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: "2026-10-01")
    seen.clear()
    assert [r["dims"]["code"] for r in src.status_incidents(load_companies(ROOT)["anthropic"])] == ["new"] and seen == [
        1
    ]


def test_incident_io_feed_keeps_unresolved_incidents(monkeypatch):
    feed = {
        "incidents": [
            {
                "id": "a",
                "name": "Errors",
                "impact": "major",
                "created_at": "2026-10-01T18:46:17Z",
                "resolved_at": "2026-10-01T19:39:30Z",
            },
            {"id": "b", "name": "Open", "impact": "minor", "created_at": "2026-10-05T07:03:53Z"},
        ]
    }
    monkeypatch.setattr(src, "get", lambda url, **k: response(feed))
    rows = list(src.status_incidents(load_companies(ROOT)["openai"]))
    assert [(r["dims"]["code"], r["dims"]["minutes"]) for r in rows] == [("a", 53), ("b", None)]
    for r in rows:
        validate(
            "observation",
            {
                **r,
                "source": "status_incidents",
                "method": "api",
                "tier": "company-stated",
                "retrieved_at": "2026-10-05T08:00:00Z",
            },
        )
    assert list(src.status_incidents(load_companies(ROOT)["xai"])) == []  # no status feed configured


# ---- peer-comparison parsing ----


def test_arena_parser_keeps_score_price_and_context_on_the_same_model():
    html = """
    <html><body><p>Oct 2, 2026</p><table>
      <thead><tr><th>Rank</th><th>Rank Spread</th><th>Model</th><th>Score</th><th>Votes</th><th>Price $/M</th><th>Context</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>11</td><td>gemini-4-argon-highGoogle · Proprietary</td><td>1525±9Preliminary</td><td>4932</td><td>$2 / $10</td><td>1M</td></tr>
        <tr><td>2</td><td>27</td><td>Anthropicclaude-opus-4-6-highAnthropic · Proprietary</td><td>1505±3</td><td>77636</td><td>$5 / $25</td><td>1M</td></tr>
      </tbody>
    </table></body></html>
    """
    as_of, rows = src.parse_arena_html(html)
    assert as_of == "2026-10-02"
    assert rows == [
        {
            "organization": "Anthropic",
            "model": "claude-opus-4-6-high",
            "rank": 2,
            "score": 1505.0,
            "score_margin": 3.0,
            "votes": 77636,
            "input_price": 5.0,
            "output_price": 25.0,
            "context_tokens": 1_000_000,
            "preliminary": False,
        },
        {
            "organization": "Google",
            "model": "gemini-4-argon-high",
            "rank": 1,
            "score": 1525.0,
            "score_margin": 9.0,
            "votes": 4932,
            "input_price": 2.0,
            "output_price": 10.0,
            "context_tokens": 1_000_000,
            "preliminary": True,
        },
    ]


def test_openrouter_parser_reads_request_share_not_token_share():
    html = """
    <html><body><p>Usage data through Oct 5, 2026</p><table>
      <thead><tr><th>Rank</th><th>Author</th><th>Share of requests</th><th>Change in requests</th></tr></thead>
      <tbody>
        <tr><td>2</td><td>Google</td><td>21.5%</td><td>+23%</td></tr>
        <tr><td>7</td><td>Anthropic</td><td>2.5%</td><td>+6%</td></tr>
        <tr><td>8</td><td>OpenAI</td><td>1.5%</td><td>new</td></tr>
        <tr><td></td><td>All other authors</td><td>8.3%</td><td>-12%</td></tr>
      </tbody>
    </table></body></html>
    """
    as_of, rows = src.parse_openrouter_html(html)
    assert as_of == "2026-10-05"
    assert rows == [
        {"rank": 2, "author": "Google", "share": 0.215, "change": 0.23},
        {"rank": 7, "author": "Anthropic", "share": 0.025, "change": 0.06},
        {"rank": 8, "author": "OpenAI", "share": 0.015, "change": None},
    ]


def test_artificial_analysis_parser_keeps_cost_latency_speed_and_quality_on_one_model_row():
    html = """
    <table>
      <thead><tr>
        <th>Model</th><th>Context Window</th><th>Creator</th>
        <th>Artificial Analysis Intelligence Index</th><th>Cost per TaskUSD</th>
        <th>MedianTokens/s</th><th>LatencyFirst Chunk (s)</th><th>TotalResponse (s)</th>
      </tr></thead>
      <tbody>
        <tr><td>Claude Example (high)</td><td>1M</td><td>Anthropic</td><td>54</td><td>$1.82</td><td>72</td><td>43.27</td><td>50.18</td></tr>
        <tr><td>Command Example</td><td>256k</td><td>Cohere</td><td>7*</td><td>--</td><td>63</td><td>1.72</td><td>9.59</td></tr>
      </tbody>
    </table>
    """
    assert src.parse_artificial_analysis_html(html, "2026-10-06") == [
        {
            "as_of": "2026-10-06",
            "model": "Claude Example (high)",
            "creator": "Anthropic",
            "context_tokens": 1_000_000,
            "intelligence_index": 54.0,
            "independently_evaluated": False,
            "cost_per_task": 1.82,
            "output_tokens_s": 72.0,
            "first_chunk_seconds": 43.27,
            "total_response_seconds": 50.18,
        },
        {
            "as_of": "2026-10-06",
            "model": "Command Example",
            "creator": "Cohere",
            "context_tokens": 256_000,
            "intelligence_index": 7.0,
            "independently_evaluated": True,
            "cost_per_task": None,
            "output_tokens_s": 63.0,
            "first_chunk_seconds": 1.72,
            "total_response_seconds": 9.59,
        },
    ]


def test_artificial_analysis_collector_applies_only_the_documented_model_alias(monkeypatch):
    monkeypatch.setattr(
        src,
        "_artificial_analysis_snapshot",
        lambda: [
            {
                "as_of": "2026-10-06",
                "model": "Command A",
                "creator": "Cohere",
                "context_tokens": 256_000,
                "intelligence_index": 7,
                "independently_evaluated": True,
                "cost_per_task": None,
                "output_tokens_s": 63,
                "first_chunk_seconds": 1.72,
                "total_response_seconds": 9.59,
            }
        ],
    )
    rows = list(src.artificial_analysis_leaderboard(load_companies(ROOT)["cohere"]))
    assert {row["metric"] for row in rows} == {
        "context_window_tokens",
        "artificial_analysis_intelligence_index",
        "artificial_analysis_output_tokens_s",
        "artificial_analysis_first_chunk_seconds",
        "artificial_analysis_total_response_seconds",
    }
    assert all(row["dims"]["arena_model"] == "command-a-03-2025" for row in rows)


def test_livebench_discovers_only_the_newest_release_with_scores_categories_and_costs():
    files = [
        {"name": "table_2026_01_08.csv"},
        {"name": "categories_2026_01_08.json"},
        {"name": "cost_2026_01_08.csv"},
        {"name": "table_2026_06_25.csv"},
        {"name": "categories_2026_06_25.json"},
        {"name": "cost_2026_06_25.csv"},
        {"name": "table_2026_12_01.csv"},
    ]
    assert src.latest_livebench_release(files) == "2026_06_25"


def test_livebench_parser_reproduces_category_overall_and_published_success_cost():
    table = """model,reason_a,reason_b,code_a,code_b
claude-example-high,80,100,50,70
"""
    cost = """model,cost_per_question,cost_per_successful_task,avg_input_tokens,avg_output_tokens,input_price_per_million,output_price_per_million
claude-example-high,0.12,0.16,1000,2000,3,15
"""
    rows = src.parse_livebench_files(
        table,
        cost,
        {"Reasoning": ["reason_a", "reason_b"], "Coding": ["code_a", "code_b"]},
        "2026_06_25",
    )
    assert rows == [
        {
            "release": "2026-06-25",
            "model": "claude-example-high",
            "overall": 75.0,
            "categories": {"Reasoning": 90.0, "Coding": 60.0},
            "cost_per_question": 0.12,
            "cost_per_successful_task": 0.16,
            "avg_input_tokens": 1000.0,
            "avg_output_tokens": 2000.0,
            "input_price": 3.0,
            "output_price": 15.0,
        }
    ]


# ---- the shipped ledgers ----


@pytest.fixture(scope="module")
def ledgers():
    registry.discover()
    return {
        sid: read_observations(ROOT, registry.SOURCES[sid].meta)
        for sid in (
            "product_model_releases",
            "product_api_prices",
            "product_plan_prices",
            "product_peer_plan_prices",
            "product_adoption_claims",
        )
    }


def test_every_ledger_row_is_cited_and_valid(ledgers):
    for sid, rows in ledgers.items():
        assert rows, sid
        for r in rows:  # read_observations already validated the contract; check the human fields are real
            assert r["source_url"].startswith("https://") and len(r["evidence"]) > 20 and r["entered_by"], (
                sid,
                r["dims"],
            )
            # retrieved_at is when the row was typed in, so it can never be later than now
            assert datetime.fromisoformat(r["retrieved_at"]) <= datetime.now(UTC), (sid, r["dims"])


def test_every_priced_model_has_a_release_date_and_prices_come_in_pairs(ledgers):
    released = {r["dims"]["model"] for r in ledgers["product_model_releases"]}
    priced = {(r["dims"]["model"], r["metric"]) for r in ledgers["product_api_prices"]}
    models = {m for m, _ in priced}
    assert models <= released, (
        models - released
    )  # a price we cannot place on the timeline would silently vanish from the chart
    assert all(
        ((m, "api_price_input_usd_mtok") in priced) and ((m, "api_price_output_usd_mtok") in priced) for m in models
    )


def test_release_ledger_is_unique_and_prices_are_sane(ledgers):
    keys = [(r["as_of"], r["dims"]["model"]) for r in ledgers["product_model_releases"]]
    assert len(keys) == len(set(keys)) and min(k[0] for k in keys) == "2023-03-14"
    by_model = {}
    for r in ledgers["product_api_prices"]:
        by_model.setdefault(r["dims"]["model"], {})[r["metric"]] = r["value"]
    assert all(
        0 < v["api_price_input_usd_mtok"] < v["api_price_output_usd_mtok"] for v in by_model.values()
    )  # output always costs more


# ---- marts ----


def obs(metric, as_of, value, entity="anthropic", retrieved="2026-10-31T00:00:00Z", source="x", **dims):
    return {
        "source": source,
        "source_url": "https://x/i",
        "method": "api",
        "as_of": as_of,
        "retrieved_at": retrieved,
        "tier": "company-stated",
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": None,
        "evidence": None,
    }


def incident(code, day, impact, minutes, entity="anthropic", retrieved="2026-10-31T00:00:00Z"):
    return obs(
        "incident",
        day,
        1,
        entity,
        retrieved,
        code=code,
        name=f"N {code}",
        impact=impact,
        started=f"{day}T10:00:00Z",
        resolved=None,
        minutes=minutes,
    )


def test_incidents_by_month_counts_each_incident_once_and_skips_none_and_the_running_month():
    rows = [
        incident("a", "2026-08-03", "minor", 30),
        incident("a", "2026-08-03", "minor", 30, retrieved="2026-10-30T00:00:00Z"),  # a re-collection
        incident("b", "2026-08-09", "major", 60),
        incident("c", "2026-08-20", "none", 5),
        incident("d", "2025-08-09", "minor", 10),
    ]
    ctx = Ctx(rows, NAMES)
    spec = marts.incidents_monthly(ctx)
    got = {(r["month"], r["impact"]): r["incidents"] for r in spec["rows"]}
    assert got == {("2026-08-01", "Minor"): 1, ("2026-08-01", "Major"): 1, ("2025-08-01", "Minor"): 1}
    assert spec["takeaway"] == [
        "Claude's status page logged 2 incidents in Aug 2026 (1 major or critical), against 1 in Aug 2025."
    ]


def test_incident_hours_sums_only_major_and_critical_with_a_resolved_time():
    rows = [
        incident("a", "2026-09-03", "major", 90),
        incident("b", "2026-09-09", "critical", 30),
        incident("c", "2026-09-10", "major", None),
        incident("d", "2026-09-11", "minor", 600),
    ]
    rows += [
        obs(
            "incident",
            "2026-10-31",
            1,
            code="z",
            name="z",
            impact="minor",
            started="2026-10-31T00:00:00Z",
            resolved=None,
            minutes=1,
        )
    ]  # makes September complete
    spec = marts.incident_hours(Ctx(rows, NAMES))
    assert [(r["month"], r["hours"], r["incidents"]) for r in spec["rows"]] == [("2026-09-01", 2.0, 2)]
    assert spec["badges"] == ["arithmetic"]


def test_release_cadence_compares_the_last_twelve_months_with_the_twelve_before():
    def rel(day, model):
        return {**obs("model_release", day, 1, model=model, tier="Opus"), "evidence": "q", "source_url": "https://x"}

    rows = [rel("2026-09-01", "A"), rel("2026-03-01", "B"), rel("2025-12-01", "C"), rel("2025-04-01", "D")]
    spec = marts.release_cadence(Ctx(rows, NAMES))
    assert spec["takeaway"] == [
        "Anthropic announced 3 models in the 12 months to 2026-09-01, against 1 in the 12 months before."
    ]
    assert next(r["models"] for r in spec["rows"]) == 1
    assert sum(r["models"] for r in spec["rows"]) == 4  # empty quarters appear as zero


def test_price_chart_joins_price_to_release_date_and_drops_unreleased_models():
    rel = lambda m, d, t: {**obs("model_release", d, 1, model=m, tier=t), "evidence": "q", "source_url": "https://x"}
    px = lambda m, v, metric: obs(metric, "2026-10-05", v, model=m, basis="current list price")
    rows = [
        rel("Claude Opus A", "2025-01-01", "Opus"),
        rel("Claude Opus B", "2026-01-01", "Opus"),
        px("Claude Opus A", 15, "api_price_input_usd_mtok"),
        px("Claude Opus A", 75, "api_price_output_usd_mtok"),
        px("Claude Opus B", 5, "api_price_input_usd_mtok"),
        px("Claude Opus B", 25, "api_price_output_usd_mtok"),
        px("Claude Ghost", 1, "api_price_input_usd_mtok"),
        px("Claude Ghost", 5, "api_price_output_usd_mtok"),
    ]
    spec = marts.api_prices(Ctx(rows, NAMES))
    assert [(r["model"], r["input"], r["output"]) for r in spec["rows"]] == [
        ("Claude Opus A", 15.0, 75.0),
        ("Claude Opus B", 5.0, 25.0),
    ]
    assert spec["takeaway"] == [
        "Opus input pricing went from $15 per million tokens (Claude Opus A) to $5 (Claude Opus B)."
    ]


def arena_model(entity, model, rank, score, input_price=None, output_price=None, context=None):
    common = {"model": model, "rank": rank, "score_margin": 3, "preliminary": False}
    rows = [
        obs("arena_text_score", "2026-10-02", score, entity, source="arena_text_leaderboard", **common),
        obs("arena_text_votes", "2026-10-02", 10_000, entity, source="arena_text_leaderboard", **common),
    ]
    for metric, value in (
        ("api_price_input_usd_mtok", input_price),
        ("api_price_output_usd_mtok", output_price),
        ("context_window_tokens", context),
    ):
        if value is not None:
            rows.append(obs(metric, "2026-10-02", value, entity, source="arena_text_leaderboard", **common))
    return rows


def artificial_analysis_model(
    entity,
    model,
    intelligence,
    cost=None,
    speed=None,
    first_chunk=None,
    total=None,
    context=1_000_000,
    arena_model=None,
):
    common = {
        "model": model,
        "creator": NAMES[entity],
        "arena_model": arena_model,
        "independently_evaluated": False,
    }
    rows = []
    for metric, value in (
        ("context_window_tokens", context),
        ("artificial_analysis_intelligence_index", intelligence),
        ("artificial_analysis_cost_per_task_usd", cost),
        ("artificial_analysis_output_tokens_s", speed),
        ("artificial_analysis_first_chunk_seconds", first_chunk),
        ("artificial_analysis_total_response_seconds", total),
    ):
        if value is not None:
            rows.append(
                obs(
                    metric,
                    "2026-10-06",
                    value,
                    entity,
                    source="artificial_analysis_leaderboard",
                    **common,
                )
            )
    return rows


def livebench_model(entity, model, overall, success_cost, **categories):
    common = {"model": model, "release": "2026-06-25"}
    measures = {
        "livebench_overall_score": overall,
        "livebench_cost_per_successful_task_usd": success_cost,
        "livebench_cost_per_question_usd": success_cost * overall / 100,
        "livebench_avg_input_tokens": 1000,
        "livebench_avg_output_tokens": 2000,
        "api_price_input_usd_mtok": 2,
        "api_price_output_usd_mtok": 10,
    } | categories
    return [
        obs(metric, "2026-06-25", value, entity, source="livebench_leaderboard", **common)
        for metric, value in measures.items()
    ]


def test_frontier_scorecard_skips_a_higher_ranked_row_with_missing_fields_instead_of_mixing_models():
    rows = arena_model("openai", "incomplete", 1, 1510)
    rows += arena_model("openai", "complete", 2, 1500, 4, 20, 1_000_000)
    rows += arena_model("anthropic", "claude", 3, 1490, 5, 25, 500_000)
    spec = marts.frontier_scorecard(Ctx(rows, NAMES))
    assert [(r["company"], r["model"], r["rank"]) for r in spec["rows"]] == [
        ("Anthropic", "claude", 3),
        ("OpenAI", "complete", 2),
    ]


def test_task_costs_are_fixed_token_arithmetic_not_claimed_success_adjusted_costs():
    rows = arena_model("anthropic", "claude", 1, 1500, 5, 25, 1_000_000)
    spec = marts.task_costs(Ctx(rows, NAMES))
    assert spec["rows"] == [
        {
            "company": "Anthropic",
            "model": "claude",
            "quick_cents": 2.25,
            "coding_cents": 22.5,
            "long_document_cents": 62.5,
        }
    ]
    assert spec["badges"] == ["arithmetic"] and "not measured cost" in spec["assumptions"][0]


def test_combined_signal_keeps_one_model_and_leaves_missing_benchmark_fields_blank():
    rows = arena_model("google-deepmind", "gemini-top-high", 1, 1525, 2, 10, 1_000_000)
    rows += arena_model("google-deepmind", "gemini-lower-high", 2, 1500, 0.5, 3, 1_000_000)
    rows += artificial_analysis_model("google-deepmind", "Gemini Top (high)", 53, cost=1.99)
    rows += artificial_analysis_model(
        "google-deepmind",
        "Gemini Lower (high)",
        41,
        cost=1.24,
        speed=243,
        first_chunk=12.56,
        total=14.62,
    )
    rows += arena_model("cohere", "command-a-03-2025", 10, 1300, 2.5, 10, 256_000)
    rows += artificial_analysis_model(
        "cohere",
        "Command A",
        7,
        speed=63,
        first_chunk=1.72,
        total=9.59,
        context=256_000,
        arena_model="command-a-03-2025",
    )
    rows.append(
        obs(
            "openrouter_request_share",
            "2026-10-05",
            0.215,
            "google-deepmind",
            source="openrouter_rankings",
            rank=2,
        )
    )
    spec = marts.combined_model_signal(Ctx(rows, NAMES))
    assert spec["rows"] == [
        {
            "company": "Google DeepMind",
            "model": "gemini-top-high",
            "arena_rank": 1,
            "arena_score": 1525.0,
            "input_price": 2.0,
            "output_price": 10.0,
            "price_source": "Arena",
            "coding_cents": 9.0,
            "aa_index": 53.0,
            "aa_task_cost": 1.99,
            "livebench_overall": None,
            "livebench_success_cost": None,
            "first_chunk": None,
            "output_speed": None,
            "total_response": None,
            "context": 1_000_000,
            "openrouter_share": 0.215,
        },
        {
            "company": "Cohere",
            "model": "command-a-03-2025",
            "arena_rank": 10,
            "arena_score": 1300.0,
            "input_price": 2.5,
            "output_price": 10.0,
            "price_source": "Arena",
            "coding_cents": 10.0,
            "aa_index": 7.0,
            "aa_task_cost": None,
            "livebench_overall": None,
            "livebench_success_cost": None,
            "first_chunk": 1.72,
            "output_speed": 63.0,
            "total_response": 9.59,
            "context": 256_000,
            "openrouter_share": None,
        },
    ]
    assert "No field is borrowed from another variant" in spec["assumptions"][0]


def test_combined_signal_prefers_a_three_source_same_model_match_when_livebench_covers_the_company():
    rows = arena_model("openai", "gpt-new-high", 1, 1530, 2, 10, 1_000_000)
    rows += artificial_analysis_model("openai", "GPT New (high)", 55, cost=1, speed=100, first_chunk=2, total=8)
    rows += arena_model("openai", "gpt-shared-max", 2, 1520, 1, 5, 1_000_000)
    rows += artificial_analysis_model("openai", "GPT Shared (max)", 54, cost=0.8, speed=120, first_chunk=1, total=7)
    rows += livebench_model(
        "openai",
        "gpt-shared-max",
        82,
        0.2,
        livebench_reasoning_score=90,
        livebench_coding_score=85,
    )
    spec = marts.combined_model_signal(Ctx(rows, NAMES))
    assert len(spec["rows"]) == 1
    assert spec["rows"][0]["model"] == "gpt-shared-max"
    assert spec["rows"][0]["livebench_overall"] == 82.0
    assert spec["rows"][0]["livebench_success_cost"] == 0.2


def test_livebench_peer_table_uses_each_companys_highest_overall_model():
    rows = livebench_model(
        "anthropic",
        "claude-a",
        80,
        0.2,
        livebench_reasoning_score=90,
        livebench_coding_score=70,
    )
    rows += livebench_model(
        "anthropic",
        "claude-b",
        82,
        0.4,
        livebench_reasoning_score=88,
        livebench_coding_score=76,
    )
    spec = marts.livebench_leaderboard(Ctx(rows, NAMES))
    assert len(spec["rows"]) == 1
    assert spec["rows"][0]["model"] == "claude-b"
    assert spec["rows"][0]["overall"] == 82.0
    assert spec["rows"][0]["success_cost"] == 0.4


def test_openrouter_chart_does_not_turn_missing_authors_into_zero_share():
    common = {"author": "Anthropic", "rank": 7, "window": "trailing 7 days"}
    rows = [
        obs("openrouter_request_share", "2026-10-05", 0.025, source="openrouter_rankings", **common),
        obs("openrouter_request_change", "2026-10-05", 0.06, source="openrouter_rankings", **common),
    ]
    spec = marts.openrouter_share(Ctx(rows, NAMES))
    assert spec["rows"] == [
        {
            "company": "Anthropic",
            "author": "Anthropic",
            "rank": 7,
            "share": 0.025,
            "change": 0.06,
            "link": "https://x/i",
        }
    ]
    assert "unknown/below the display cutoff" in spec["assumptions"][1]


def test_real_charts_validate_against_the_shipped_ledgers(ledgers):
    from pipeline.core.build import build_mart

    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    all_rows = {sid: ledgers[sid] for sid in ledgers} | {"status_incidents": []}
    for mid in (
        "product.model_releases",
        "product.release_cadence",
        "product.api_prices",
        "product.plans",
        "product.peer_plans",
        "product.public_adoption",
    ):
        spec = build_mart(registry.MARTS[mid], all_rows, now, NAMES)
        assert spec["status"] == "ok" and spec["rows"], mid
        assert all(
            s["manual"] and s["manual"]["evidence"] for s in spec["sources"]
        )  # ledger charts show who entered what, from which quote
