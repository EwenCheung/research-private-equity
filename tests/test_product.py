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


# ---- the shipped ledgers ----


@pytest.fixture(scope="module")
def ledgers():
    registry.discover()
    return {
        sid: read_observations(ROOT, registry.SOURCES[sid].meta)
        for sid in ("product_model_releases", "product_api_prices", "product_plan_prices")
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


def obs(metric, as_of, value, entity="anthropic", retrieved="2026-10-31T00:00:00Z", **dims):
    return {
        "source": "x",
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


def test_real_charts_validate_against_the_shipped_ledgers(ledgers):
    from pipeline.core.build import build_mart

    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    all_rows = {sid: ledgers[sid] for sid in ledgers} | {"status_incidents": []}
    for mid in ("product.model_releases", "product.release_cadence", "product.api_prices", "product.plans"):
        spec = build_mart(registry.MARTS[mid], all_rows, now, NAMES)
        assert spec["status"] == "ok" and spec["rows"], mid
        assert all(
            s["manual"] and s["manual"]["evidence"] for s in spec["sources"]
        )  # ledger charts show who entered what, from which quote
