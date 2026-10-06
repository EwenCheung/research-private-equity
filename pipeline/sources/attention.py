"""Consumer attention: Wikipedia pageviews."""

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "attention"
HISTORY_START = date(2023, 1, 1)
WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}"


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
