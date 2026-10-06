from datetime import date

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
    return load_companies(ROOT)


def response(body, status=200):
    kw = {"text": body} if isinstance(body, str) else {"json": body}
    return httpx.Response(status, request=httpx.Request("GET", "https://x"), **kw)


def valid(rows, source):
    for r in rows:
        validate("observation", {**r, **STAMP, "source": source})
    return rows


def test_every_company_has_a_wikipedia_title_and_renamed_pages_are_all_listed():
    ids = {c.slug: c.ids("attention") for c in load_companies(ROOT).values()}
    assert all(i.get("wiki_company") for i in ids.values())
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
