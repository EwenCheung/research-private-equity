from datetime import UTC, datetime

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.core.http import SourceUnavailable
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


# ---- the shipped ledgers ----


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


# ---- OpenRouter benchmarks API (shapes from its published OpenAPI example; no key was available to call it live) ----


def benchmark_feed():
    aa = lambda slug, name, i, c, a: {
        "source": "artificial-analysis",
        "model_permaslug": slug,
        "display_name": name,
        "intelligence_index": i,
        "coding_index": c,
        "agentic_index": a,
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
    }
    ev = lambda slug, name, kind, acc: {
        "source": "openrouter",
        "model_permaslug": slug,
        "display_name": name,
        "benchmark_type": kind,
        "accuracy": acc,
        "accuracy_stddev": 0.03,
        "avg_cost_per_task": 0.002,
        "total_tasks": 300,
        "last_run_timestamp": "2026-10-02T12:00:00Z",
    }
    return {
        "data": [
            aa("anthropic/claude-a", "Claude A", 60.0, 55.0, None),
            aa("anthropic/claude-b", "Claude B", 70.0, 65.0, 50.0),
            aa("openai/gpt-x", "GPT X", 75.0, 60.0, 52.0),
            aa("google-vertex/other", "Not Google's prefix", 99.0, 99.0, 99.0),
            ev("anthropic/claude-b", "Anthropic: Claude B", "gpqa_diamond", 0.8),
            ev("openai/gpt-x", "OpenAI: GPT X", "gpqa_diamond", 0.85),
            ev("openai/gpt-x", "GPT X", "search_browsecomp", 0.5),  # search shapes are not collected
            {
                "source": "design-arena",
                "model_permaslug": "anthropic/claude-b",
                "display_name": "Claude B",
                "elo": 1400,
            },
        ],
        "meta": {"as_of": "2026-10-03T08:00:00Z", "model_count": 4, "version": "v1"},
    }


def collected(monkeypatch, slug):
    monkeypatch.setattr(src, "_openrouter_benchmarks", lambda: benchmark_feed())
    return list(src.openrouter_benchmarks(load_companies(ROOT)[slug]))


def test_benchmark_collector_keeps_only_the_companys_models_and_documented_metrics(monkeypatch):
    rows = collected(monkeypatch, "anthropic")
    got = {(r["metric"], r["dims"]["model"], r["value"], r["as_of"]) for r in rows}
    assert got == {
        ("openrouter_aa_intelligence_index", "Claude A", 60.0, "2026-10-03"),
        ("openrouter_aa_coding_index", "Claude A", 55.0, "2026-10-03"),
        ("openrouter_aa_intelligence_index", "Claude B", 70.0, "2026-10-03"),
        ("openrouter_aa_coding_index", "Claude B", 65.0, "2026-10-03"),
        ("openrouter_aa_agentic_index", "Claude B", 50.0, "2026-10-03"),
        ("openrouter_eval_accuracy", "Anthropic: Claude B", 0.8, "2026-10-02"),  # the eval is dated by its own last run
    }
    for r in (
        rows
    ):  # a missing index is left out, never written as zero; every row links to the public docs, not the 401 API
        assert r["source_url"].startswith("https://openrouter.ai/docs/")
        validate(
            "observation",
            {
                **r,
                "source": "openrouter_benchmarks",
                "method": "api",
                "tier": "platform",
                "retrieved_at": "2026-10-05T08:00:00Z",
            },
        )


def test_benchmark_collector_is_skipped_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    src._openrouter_benchmarks.cache_clear()
    with pytest.raises(SourceUnavailable, match="OPENROUTER_API_KEY"):
        list(src.openrouter_benchmarks(load_companies(ROOT)["anthropic"]))


def test_every_peer_has_an_openrouter_provider_prefix():
    companies = load_companies(ROOT)
    assert all(c.ids("product").get("openrouter_slug") for c in companies.values())


def stamp(rows, source="openrouter_benchmarks"):
    return [
        {
            **r,
            "source": source,
            "method": "api",
            "tier": "platform",
            "retrieved_at": "2026-10-05T08:00:00Z",
            "entered_by": None,
            "evidence": None,
        }
        for r in rows
    ]


def test_index_chart_picks_each_companys_top_model_and_keeps_blanks_blank(monkeypatch):
    rows = stamp(collected(monkeypatch, "anthropic") + collected(monkeypatch, "openai"))
    spec = marts.openrouter_indexes(Ctx(rows, NAMES))
    assert [(r["company"], r["model"], r["intelligence"], r["coding"], r["agentic"]) for r in spec["rows"]] == [
        ("OpenAI", "GPT X", 75.0, 60.0, 52.0),
        ("Anthropic", "Claude B", 70.0, 65.0, 50.0),
    ]
    assert spec["takeaway"][1] == "Anthropic's best, Claude B, scores 70.0: 5.0 points behind."
    assert "Google DeepMind" in spec["assumptions"][2] and "OpenAI" not in spec["assumptions"][2]
    assert marts.openrouter_indexes(Ctx([], NAMES))["rows"] == []


def test_eval_chart_reports_accuracy_per_benchmark_and_company(monkeypatch):
    rows = stamp(collected(monkeypatch, "anthropic") + collected(monkeypatch, "openai"))
    spec = marts.openrouter_evals(Ctx(rows, NAMES))
    assert [(r["benchmark"], r["company"], r["model"], r["accuracy"], r["tasks"]) for r in spec["rows"]] == [
        ("GPQA Diamond", "OpenAI", "GPT X", 0.85, 300),  # the provider prefix the feed adds is dropped
        ("GPQA Diamond", "Anthropic", "Claude B", 0.8, 300),
    ]
    assert spec["takeaway"] == [
        "GPQA Diamond: OpenAI's GPT X leads at 85% accuracy. Anthropic's best, Claude B, scores 80%."
    ]


def test_benchmark_charts_satisfy_the_chart_contract_with_data_and_without(monkeypatch):
    from pipeline.core.build import build_mart

    registry.discover()
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    rows = stamp(collected(monkeypatch, "anthropic") + collected(monkeypatch, "openai"))
    for mid in ("product.openrouter_indexes", "product.openrouter_evals"):
        assert build_mart(registry.MARTS[mid], {"openrouter_benchmarks": rows}, now, NAMES)["status"] == "ok"
        assert build_mart(registry.MARTS[mid], {"openrouter_benchmarks": []}, now, NAMES)["status"] == "awaiting_data"
