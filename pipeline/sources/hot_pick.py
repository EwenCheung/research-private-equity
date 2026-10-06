"""Hot Pick: the headlines and Hacker News stories about Anthropic that the weekly pick list is chosen from."""

import time
import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime

from pipeline.core import source
from pipeline.core.http import get

PAGE = "hot_pick"
NEWS = "https://news.google.com/rss/search"
ALGOLIA = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://news.ycombinator.com/item?id={id}"
HN_DAYS = (
    30  # each run reads the last month of stories, so a story's points are refreshed while it is still collecting votes
)


def parse_news(text: str, entity: str):
    """One row per headline in a Google News RSS feed; items missing a title, link, date or outlet are skipped."""
    for item in ET.fromstring(text).iterfind("./channel/item"):
        title, link, published = item.findtext("title"), item.findtext("link"), item.findtext("pubDate")
        outlet = item.find("source")
        if not (title and link and published and outlet is not None and outlet.text):
            continue
        when = parsedate_to_datetime(published).astimezone(UTC)
        yield {
            "source_url": link,
            "as_of": when.date().isoformat(),
            "entity": entity,
            "metric": "news_headline",
            "value": 1,
            "dims": {
                "title": title.removesuffix(f" - {outlet.text}"),
                "outlet": outlet.text,
                "published": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }


def parse_stories(payload: dict, entity: str):
    """One row per Hacker News story, valued at its points; stories without points or a title are skipped."""
    for hit in payload["hits"]:
        if hit.get("points") is None or not hit.get("title"):
            continue
        yield {
            "source_url": HN_ITEM.format(id=hit["objectID"]),
            "as_of": hit["created_at"][:10],
            "entity": entity,
            "metric": "hn_story_points",
            "value": hit["points"],
            "dims": {
                "id": hit["objectID"],
                "title": hit["title"],
                "comments": hit.get("num_comments") or 0,
                "url": hit.get("url"),
                "created": hit["created_at"],
            },
        }


@source(
    id="news_headlines",
    page=PAGE,
    label="Google News headlines about Anthropic",
    url="https://news.google.com/",
    method="api",
    tier="press",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="Automated HTTP GET of Google News's public RSS search; no POST, login or browser automation. The feed returns about the "
    "100 most recent results, ranked by Google, and keeps no history, so history starts when we began collecting. Each headline "
    "is stored with its outlet and its Google News link, not its text. Search words match by word, so some results are about "
    "the AI industry rather than Anthropic.",
)
def news_headlines(company):
    query = company.ids(PAGE).get("news_query")
    if not query:
        return
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    yield from parse_news(get(NEWS, params=params).text, company.slug)


@source(
    id="hn_stories",
    page=PAGE,
    label="Hacker News stories about Anthropic",
    url="https://news.ycombinator.com/",
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=2,
    backfillable=True,
    caveats="Automated HTTP GET of the Algolia Hacker News search; no login. Stories from the last 30 days whose text matches "
    "the search word, valued at their points when read. Points count upvotes, not importance, and a story's points rise "
    "for a day or two after it is posted.",
)
def hn_stories(company):
    query = company.ids(PAGE).get("hn_query")
    if not query:
        return
    since = int(time.time()) - HN_DAYS * 86400
    params = {"query": query, "tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 100}
    yield from parse_stories(get(ALGOLIA, params=params).json(), company.slug)
