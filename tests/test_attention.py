import json
from datetime import UTC, date, datetime

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.marts import attention as marts
from pipeline.sources import attention as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}
STAMP = {"method": "api", "tier": "platform", "retrieved_at": "2026-10-05T04:00:00Z"}


@pytest.fixture
def cos(monkeypatch):
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    monkeypatch.setattr(src, "today", lambda: date(2026, 10, 5))
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    return load_companies(ROOT)


def response(body, status=200):
    kw = {"text": body} if isinstance(body, str) else {"json": body}
    return httpx.Response(status, request=httpx.Request("GET", "https://x"), **kw)


def valid(rows, source):
    for r in rows:
        validate("observation", {**r, **STAMP, "source": source})
    return rows


def test_identifiers_are_complete_and_trends_fit_the_five_term_limit():
    ids = {c.slug: c.ids("attention") for c in load_companies(ROOT).values()}
    assert all(i.get("news_query") and i.get("hn_query") and i.get("wiki_company") for i in ids.values())
    assert sum(1 for i in ids.values() if i.get("trends_term")) <= 5
    assert "Claude (AI)" in ids["anthropic"]["wiki_product"] and "SpaceXAI" in ids["xai"]["wiki_company"]


def test_wikipedia_sums_titles_by_kind_and_skips_pages_with_no_views(cos, monkeypatch):
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        if "Claude_%28AI%29" in url:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", url), response=httpx.Response(404))
        return response({"items": [{"timestamp": "2026100100", "views": 7}]})

    monkeypatch.setattr(src, "get", fake_get)
    rows = valid(list(src.wikipedia_pageviews(cos["anthropic"])), "wikipedia_pageviews")
    assert {(r["dims"]["title"], r["dims"]["kind"]) for r in rows} == {
        ("Claude (language model)", "product"),
        ("Anthropic", "company"),
    }
    assert (
        rows[0]["as_of"] == "2026-10-01" and "20230101/20261004" in urls[0]
    )  # whole history on a first run, to yesterday
    assert any("Claude_%28language_model%29" in u for u in urls)  # titles are underscored and percent-encoded


def test_gdelt_retries_when_pushed_and_keeps_the_day_total(cos, monkeypatch):
    replies = iter(
        [
            "Please limit requests to one every 5 seconds.",
            json.dumps({"timeline": [{"data": [{"date": "20260930T000000Z", "value": 3, "norm": 40000}]}]}),
        ]
    )
    monkeypatch.setattr(src, "get", lambda url, **kw: response(next(replies)))
    monkeypatch.setattr(src, "resume", lambda *a, **k: date(2026, 9, 30))
    rows = valid(list(src.gdelt_news_volume(cos["anthropic"])), "gdelt_news_volume")
    assert [(r["as_of"], r["value"], r["dims"]["total_articles"]) for r in rows] == [("2026-09-30", 3, 40000)]


def test_gdelt_waits_out_a_429_block_with_growing_pauses(monkeypatch):
    waits, replies = [], iter([429, 429, "ok"])

    def fake_get(url, **kw):
        r = next(replies)
        if r == 429:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", url), response=httpx.Response(429))
        return response(json.dumps({"timeline": []}))

    monkeypatch.setattr(src, "get", fake_get)
    monkeypatch.setattr(src.time, "sleep", waits.append)
    assert src.gdelt_json({"query": "x"}) == {"timeline": []}
    assert waits == [6, 180, 360]  # a short first pause, then three, then six minutes


def test_gdelt_gives_up_after_repeated_refusals(cos, monkeypatch):
    monkeypatch.setattr(src, "get", lambda url, **kw: response("Please limit requests"))
    with pytest.raises(RuntimeError, match="kept refusing"):
        src.gdelt_json({"query": "x"})


def test_hackernews_counts_by_month_and_flags_the_running_month(cos, monkeypatch):
    seen = []

    def fake_get(url, params, **kw):
        seen.append(params["numericFilters"])
        return response({"nbHits": 5 if "points" in params["numericFilters"] else 50})

    monkeypatch.setattr(src, "get", fake_get)
    monkeypatch.setattr(src, "resume", lambda *a, **k: date(2026, 9, 1))
    rows = valid(list(src.hackernews_stories(cos["anthropic"])), "hackernews_stories")
    assert [(r["as_of"], r["metric"], r["value"], r["dims"]["partial_month"]) for r in rows] == [
        ("2026-09-01", "hn_stories", 50, False),
        ("2026-09-01", "hn_stories_50plus", 5, False),
        ("2026-10-01", "hn_stories", 50, True),
        ("2026-10-01", "hn_stories_50plus", 5, True),
    ]
    assert seen[1].endswith(",points>=50")


RSS = """<rss><channel>
<item><title>Big news - Politico</title><link>https://news.google.com/rss/articles/abc</link><pubDate>Sun, 04 Oct 2026 20:55:00 GMT</pubDate><source url="https://www.politico.com">Politico</source></item>
<item><title></title><link>https://news.google.com/rss/articles/skip</link><pubDate>Sun, 04 Oct 2026 20:55:00 GMT</pubDate></item>
</channel></rss>"""


def test_google_news_rows_carry_outlet_and_link_and_skip_incomplete_items(cos, monkeypatch):
    monkeypatch.setattr(src, "get", lambda url, **kw: response(RSS))
    rows = valid(list(src.google_news_headlines(cos["anthropic"])), "google_news_headlines")
    assert [(r["as_of"], r["dims"]["outlet"], r["source_url"]) for r in rows] == [
        ("2026-10-04", "Politico", "https://news.google.com/rss/articles/abc")
    ]


def test_trends_is_skipped_without_a_key_and_scaled_together_with_one_key(cos, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with pytest.raises(src.SourceUnavailable, match="SERPAPI_KEY"):
        list(src.google_trends(cos["anthropic"]))
    monkeypatch.setenv("SERPAPI_KEY", "k")
    src.trends.cache_clear()
    calls = []
    body = {
        "interest_over_time": {
            "timeline_data": [
                {
                    "timestamp": str(int(datetime(2026, 9, 27, tzinfo=UTC).timestamp())),
                    "values": [
                        {"query": "Claude AI", "value": "60", "extracted_value": 60},
                        {"query": "ChatGPT", "extracted_value": 100},
                        {"query": "Google Gemini", "extracted_value": 40},
                        {"query": "Grok AI", "extracted_value": 20},
                        {"query": "Mistral AI", "extracted_value": 5},
                    ],
                }
            ]
        }
    }
    monkeypatch.setattr(src, "get", lambda url, **kw: calls.append(kw["params"]) or response(body))
    a = valid(list(src.google_trends(cos["anthropic"])), "google_trends")
    o = valid(list(src.google_trends(cos["openai"])), "google_trends")
    assert (a[0]["value"], o[0]["value"], a[0]["as_of"]) == (60, 100, "2026-09-27")
    assert len(calls) == 1  # one request, so every term shares one 0-100 scale
    assert set(calls[0]["q"].split(",")) == {"Claude AI", "ChatGPT", "Google Gemini", "Grok AI", "Mistral AI"}
    assert list(src.google_trends(cos["cohere"])) == []  # no Trends term: Cohere is left out
    src.trends.cache_clear()


# ---- marts ----


def obs(source, metric, entity, as_of, value, retrieved="2026-10-05T04:00:00Z", **dims):
    return {
        "source": source,
        "source_url": "https://x",
        "method": "api",
        "as_of": as_of,
        "retrieved_at": retrieved,
        "tier": "platform",
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": None,
        "evidence": None,
    }


def wiki(entity, day, views, title, kind="product"):
    return obs("wikipedia_pageviews", "wiki_pageviews", entity, day, views, title=title, kind=kind)


def test_wikipedia_chart_sums_renamed_titles_and_drops_the_running_month():
    rows = [
        wiki("anthropic", "2026-08-10", 100, "Claude (language model)"),
        wiki("anthropic", "2026-08-11", 50, "Claude (AI)"),
        wiki("openai", "2026-08-10", 900, "ChatGPT"),
        wiki("anthropic", "2025-08-10", 50, "Claude (AI)"),
        wiki("anthropic", "2026-09-10", 300, "Claude (AI)"),
        wiki("openai", "2026-09-10", 700, "ChatGPT"),
        wiki("anthropic", "2026-10-02", 999, "Claude (AI)"),
    ]  # October is still running: left out
    spec = marts.wiki_products(Ctx(rows, NAMES))
    got = {(r["month"], r["company"]): r["views"] for r in spec["rows"]}
    assert got[("2026-08-01", "Anthropic")] == 150 and ("2026-10-01", "Anthropic") not in got
    assert spec["takeaway"][0].startswith("In Sep 2026, Anthropic's assistant article drew 300 Wikipedia views")
    assert "against OpenAI at 700 (2.3× Anthropic)" in spec["takeaway"][0]


def test_news_share_is_a_ratio_of_sums_not_a_raw_count():
    rows = []
    for day in ("2026-08-10", "2026-08-11"):
        rows.append(obs("gdelt_news_volume", "news_articles", "anthropic", day, 10, total_articles=10000))
    rows.append(
        obs("gdelt_news_volume", "news_articles", "anthropic", "2026-09-30", 30, total_articles=10000)
    )  # data to month end
    spec = marts.news_share(Ctx(rows, NAMES))
    assert {r["month"]: r["per_10k"] for r in spec["rows"]} == {
        "2026-08-01": 10.0,
        "2026-09-01": 30.0,
    }  # 20/20000 vs 30/10000
    assert spec["badges"] == ["arithmetic"] and "30.0 of every 10,000" in spec["takeaway"][0]


def test_hn_chart_excludes_the_partial_month():
    rows = []
    for m, partial in (("2026-09-01", False), ("2026-10-01", True)):
        rows += [
            obs("hackernews_stories", "hn_stories", "anthropic", m, 100, partial_month=partial),
            obs("hackernews_stories", "hn_stories_50plus", "anthropic", m, 9, partial_month=partial),
        ]
    spec = marts.hn_stories(Ctx(rows, NAMES))
    assert [(r["month"], r["front"], r["stories"]) for r in spec["rows"]] == [("2026-09-01", 9, 100)]


def test_appstore_rank_reports_places_climbed_and_never_invents_a_rank():
    rows = [
        obs("appstore_top_charts", "appstore_rank", "anthropic", "2026-10-04", 25, app_name="Claude"),
        obs("appstore_top_charts", "appstore_rank", "anthropic", "2026-10-05", 19, app_name="Claude"),
        obs("appstore_top_charts", "appstore_rank", "openai", "2026-10-05", 3, app_name="ChatGPT"),
        obs("appstore_top_charts", "appstore_rank", "xai", "2026-10-04", 30, app_name="Grok"),
    ]  # out of the chart today
    spec = marts.appstore_rank(Ctx(rows, NAMES))
    assert [(r["company"], r["rank"], r["climbed"]) for r in spec["rows"]] == [
        ("OpenAI", 3, None),
        ("Anthropic", 19, 6),
    ]
    assert spec["takeaway"] == ["Claude is #19 on the US App Store free chart, behind ChatGPT #3."]


def test_trends_chart_waits_for_a_key_then_uses_only_the_newest_run():
    waiting = marts.search_interest(Ctx([], NAMES))
    assert waiting["rows"] == [] and "SerpAPI" in waiting["takeaway"][0]
    rows = [
        obs(
            "google_trends", "search_interest", "anthropic", "2026-09-06", 10, "2026-09-01T00:00:00Z"
        ),  # an older run, other scale
        obs("google_trends", "search_interest", "anthropic", "2026-09-06", 40, "2026-10-05T00:00:00Z"),
        obs("google_trends", "search_interest", "anthropic", "2026-09-13", 60, "2026-10-05T00:00:00Z"),
    ]
    spec = marts.search_interest(Ctx(rows, NAMES))
    assert [r["interest"] for r in spec["rows"]] == [50.0]


def test_recent_coverage_dedupes_and_keeps_the_link():
    h = lambda title, day, retrieved: {
        **obs("google_news_headlines", "news_headline", "anthropic", day, 1, retrieved, title=title, outlet="Politico"),
        "source_url": "https://news.google.com/rss/articles/x",
    }
    rows = [
        h("A", "2026-10-03", "2026-10-04T00:00:00Z"),
        h("A", "2026-10-03", "2026-10-05T00:00:00Z"),
        h("B", "2026-10-04", "2026-10-05T00:00:00Z"),
    ]
    spec = marts.recent_coverage(Ctx(rows, NAMES))
    assert [(r["date"], r["headline"]) for r in spec["rows"]] == [("2026-10-04", "B"), ("2026-10-03", "A")]
    assert spec["rows"][0]["link"].startswith("https://news.google.com/")
