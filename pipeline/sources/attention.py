"""Consumer attention: Wikipedia pageviews, news volume (GDELT), Hacker News, Google News headlines, Google Trends.

The App Store rank snapshot lives in snapshots.py (it predates this page); its chart is built here.
"""

import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import cache
from urllib.parse import quote

import httpx

from pipeline.core import source
from pipeline.core.companies import load_companies
from pipeline.core.http import SourceUnavailable, env_key, get
from pipeline.core.store import latest_as_of

PAGE = "attention"
HISTORY_START = date(2023, 1, 1)
WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
HN = "https://hn.algolia.com/api/v1/search"
NEWS_RSS = "https://news.google.com/rss/search"
SERPAPI = "https://serpapi.com/search.json"


def today() -> date:
    return datetime.now(UTC).date()


def resume(company, source_id: str, start: date, overlap_days: int, **dims) -> date:
    last = latest_as_of(company.root, source_id, company.slug, **dims)
    return max(start, date.fromisoformat(last) - timedelta(days=overlap_days)) if last else start


# ---- Wikipedia ----


@source(
    id="wikipedia_pageviews",
    page=PAGE,
    label="Wikimedia pageviews (English Wikipedia)",
    url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}",
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=3,
    backfillable=True,
    caveats="Human (non-bot) views of each company's and product's English Wikipedia articles. Pages are renamed over time, "
    "so each topic sums every title it has had. Reader interest in the topic, not use of the product.",
)
def wikipedia_pageviews(company):
    ids, end = company.ids(PAGE), today() - timedelta(days=1)
    for kind in ("product", "company"):
        for title in ids.get(f"wiki_{kind}") or []:
            start = resume(company, "wikipedia_pageviews", HISTORY_START, 7, title=title)
            url = WIKI.format(
                title=quote(title.replace(" ", "_"), safe=""),
                start=start.strftime("%Y%m%d"),
                end=end.strftime("%Y%m%d"),
            )
            try:
                items = get(url).json()["items"]
            except httpx.HTTPStatusError as e:
                if (
                    e.response.status_code == 404
                ):  # no views recorded in the range (page not created yet, or renamed away)
                    continue
                raise
            for item in items:
                t = item["timestamp"]
                yield {
                    "source_url": url,
                    "as_of": f"{t[:4]}-{t[4:6]}-{t[6:8]}",
                    "entity": company.slug,
                    "metric": "wiki_pageviews",
                    "value": item["views"],
                    "dims": {"title": title, "kind": kind},
                }


# ---- GDELT news volume ----


def gdelt_json(params: dict) -> dict:
    """GDELT asks for one request every 5 seconds and answers with plain text when pushed; wait and retry."""
    for attempt in range(4):
        time.sleep(6 if attempt == 0 else 30)
        r = get(GDELT, params=params, timeout=120)
        if r.text.lstrip().startswith("{"):
            return r.json()
    raise RuntimeError(f"GDELT kept refusing: {r.text[:120]!r}")


@source(
    id="gdelt_news_volume",
    page=PAGE,
    label="GDELT news volume (English-language articles)",
    url="https://api.gdeltproject.org/api/v2/doc/doc?mode=timelinevolraw",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="Daily count of English-language articles in GDELT's monitored set matching each company's search terms, "
    "next to the total monitored that day. GDELT's coverage has shrunk and shifted since 2023, so charts use the share "
    "of all monitored articles, not the raw count. Search terms match by words, so some unrelated articles are included.",
)
def gdelt_news_volume(company):
    query = company.ids(PAGE).get("news_query")
    if not query:
        return
    start, end = resume(company, "gdelt_news_volume", HISTORY_START, 4), today() - timedelta(days=1)
    while start <= end:
        chunk_end = min(end, start + timedelta(days=364))
        params = {
            "query": f"{query} sourcelang:english",
            "mode": "timelinevolraw",
            "format": "json",
            "startdatetime": start.strftime("%Y%m%d000000"),
            "enddatetime": chunk_end.strftime("%Y%m%d235959"),
        }
        for series in gdelt_json(params).get("timeline", []):
            for point in series["data"]:
                d = point["date"]
                yield {
                    "source_url": "https://api.gdeltproject.org/api/v2/doc/doc?" + str(httpx.QueryParams(params)),
                    "as_of": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                    "entity": company.slug,
                    "metric": "news_articles",
                    "value": point["value"],
                    "dims": {"query": query, "total_articles": point["norm"]},
                }
        start = chunk_end + timedelta(days=1)


# ---- Hacker News ----


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


@source(
    id="hackernews_stories",
    page=PAGE,
    label="Hacker News stories (Algolia search)",
    url="https://hn.algolia.com/api/v1/search",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="Stories submitted to Hacker News whose text matches the company's search words, counted by submission month; "
    "'front page' means 50 or more points. Matching is by words, so a common word brings in unrelated stories. "
    "A developer-community signal, not a general-public one.",
)
def hackernews_stories(company):
    query = company.ids(PAGE).get("hn_query")
    if not query:
        return
    month = month_start(resume(company, "hackernews_stories", HISTORY_START, 35))  # re-count the last two months
    while month <= today():
        lo, hi = epoch(month), epoch(next_month(month))
        for metric, extra in (("hn_stories", ""), ("hn_stories_50plus", ",points>=50")):
            params = {
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>={lo},created_at_i<{hi}{extra}",
                "hitsPerPage": 0,
            }
            hits = get(HN, params=params).json()["nbHits"]
            yield {
                "source_url": "https://hn.algolia.com/?"
                + str(
                    httpx.QueryParams(
                        {"query": query, "type": "story", "dateRange": "custom", "dateStart": lo, "dateEnd": hi}
                    )
                ),
                "as_of": month.isoformat(),
                "entity": company.slug,
                "metric": metric,
                "value": hits,
                "dims": {"query": query, "partial_month": month == month_start(today())},
            }
        month = next_month(month)


# ---- Google News headlines ----


@source(
    id="google_news_headlines",
    page=PAGE,
    label="Google News RSS: latest headlines",
    url="https://news.google.com/rss/search?q={query}",
    method="api",
    tier="press",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="About the 100 most recent headlines Google News returns for each company's search terms, with outlet and link. "
    "The feed keeps no history, so the list builds from our daily snapshots. Headlines are shown with their link, not reproduced.",
)
def google_news_headlines(company):
    query = company.ids(PAGE).get("news_query")
    if not query:
        return
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    root = ET.fromstring(get(NEWS_RSS, params=params).text)
    for item in root.findall(".//item"):
        pub = item.findtext("pubDate")
        link, title = item.findtext("link"), (item.findtext("title") or "").strip()
        if not (pub and link and title):
            continue
        outlet = item.find("source")
        yield {
            "source_url": link,
            "as_of": parsedate_to_datetime(pub).astimezone(UTC).date().isoformat(),
            "entity": company.slug,
            "metric": "news_headline",
            "value": 1,
            "dims": {"title": title, "outlet": (outlet.text or "") if outlet is not None else "", "query": query},
        }


# ---- Google Trends (SerpAPI) ----


@cache
def trends(terms: tuple[str, ...], key: str) -> dict[str, list[tuple[date, int]]]:
    """One comparison request for all terms, so their values share a scale (Trends caps a comparison at 5 terms)."""
    params = {
        "engine": "google_trends",
        "q": ",".join(terms),
        "data_type": "TIMESERIES",
        "date": "today 5-y",
        "api_key": key,
    }
    body = get(SERPAPI, params=params, timeout=120).json()
    if "interest_over_time" not in body:
        raise RuntimeError(f"SerpAPI returned no interest_over_time: {str(body.get('error', body))[:150]}")
    out: dict[str, list[tuple[date, int]]] = {t: [] for t in terms}
    for point in body["interest_over_time"]["timeline_data"]:
        when = datetime.fromtimestamp(int(point["timestamp"]), UTC).date()
        for v in point["values"]:
            if v["query"] in out:
                out[v["query"]].append((when, int(v.get("extracted_value", v.get("value", 0)))))
    return out


@source(
    id="google_trends",
    page=PAGE,
    label="Google Trends via SerpAPI (interest over time)",
    url=SERPAPI,
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="Relative search interest, 0-100, with 100 the highest point of any compared term in the 5-year window. "
    "Needs a SerpAPI key (SERPAPI_KEY); Cohere is left out because Trends compares at most five terms. "
    "Scaling can shift between runs when the 5-year window moves, so charts use the newest run only.",
)
def google_trends(company):
    key = env_key("SERPAPI_KEY", "Google Trends has no public API; SerpAPI has a free plan")
    terms = {c.slug: c.ids(PAGE).get("trends_term") for c in load_companies(company.root).values()}
    terms = {slug: t for slug, t in terms.items() if t}
    if len(terms) > 5 or company.slug not in terms:
        if company.slug in terms:
            raise SourceUnavailable("Google Trends compares at most 5 terms; remove one trends_term")
        return
    series = trends(tuple(terms.values()), key)
    for when, value in series[terms[company.slug]]:
        yield {
            "source_url": SERPAPI,
            "as_of": when.isoformat(),
            "entity": company.slug,
            "metric": "search_interest",
            "value": value,
            "dims": {"term": terms[company.slug], "window": "today 5-y", "compared": "|".join(terms.values())},
        }
