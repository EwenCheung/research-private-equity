"""Consumer attention: Wikipedia pageviews, news volume (GDELT), Hacker News, Google News headlines, Google Trends.

The App Store rank snapshot lives in snapshots.py (it predates this page); its chart is built here.
"""

from datetime import UTC, date, datetime, timedelta
from functools import cache
from urllib.parse import quote

import httpx

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "attention"
HISTORY_START = date(2023, 1, 1)
WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}"
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


# ---- Hacker News ----


# ---- Google News headlines ----


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
