"""Hot Pick: the news, blog, discussion, community and repository items about Anthropic that the Hot Pick is chosen from."""

import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from pipeline.core import source
from pipeline.core.http import get, github_get

PAGE = "hot_pick"
NEWS = "https://news.google.com/rss/search"
ALGOLIA = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://news.ycombinator.com/item?id={id}"
DEVTO = "https://dev.to/api/articles"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
HN_DAYS = (
    30  # each run reads the last month of stories, so a story's points are refreshed while it is still collecting votes
)
FEED_DAYS = 60  # older feed items are not read: a big archive (Hugging Face keeps years) is not a month of news
REPO_DAYS = 30
# Names Anthropic, or names Claude next to an AI word: a bare "Claude" is mostly a first name (Claude Monet, Claude Shannon).
ANTHROPIC = re.compile(r"\banthropic\b", re.IGNORECASE)
CLAUDE = re.compile(r"\bclaude\b", re.IGNORECASE)
AI_WORD = re.compile(
    r"\b(ai|llm|llms|model|models|opus|sonnet|haiku|chatbot|assistant|agent|agents|code|openai|gemini|gpt|mcp)\b",
    re.IGNORECASE,
)


def mentions(text: str) -> bool:
    return bool(ANTHROPIC.search(text) or (CLAUDE.search(text) and AI_WORD.search(text)))


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


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(element, *names):
    """The first child of an RSS or Atom element with one of these tag names (namespaces ignored)."""
    for name in names:
        for c in element:
            if local(c.tag) == name:
                return c
    return None


def feed_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text.strip()).astimezone(UTC)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(text.strip()).astimezone(UTC)
        except ValueError:
            return None


def parse_feed(text: str, outlet: str, kind: str, entity: str, now: datetime):
    """One row per item of an RSS or Atom feed that names Anthropic or Claude.

    Only the title, link and date are stored; the item's summary is read to decide whether it is about Anthropic and then dropped.
    """
    root = ET.fromstring(text)
    for item in root.iter():
        if local(item.tag) not in ("item", "entry"):
            continue
        title_el, link_el = child(item, "title"), child(item, "link")
        link = (link_el.get("href") or link_el.text or "").strip() if link_el is not None else ""
        date_el = child(item, "pubDate", "published", "updated", "date")
        when = feed_time(date_el.text if date_el is not None else None)
        title = ((title_el.text or "") if title_el is not None else "").strip()
        if not (title and link and when) or when < now - timedelta(days=FEED_DAYS):
            continue
        body = child(item, "description", "summary", "encoded", "content")
        summary = re.sub(r"<[^>]+>", " ", (body.text or "") if body is not None else "")
        if not mentions(f"{title} {summary}"):
            continue
        yield {
            "source_url": link,
            "as_of": when.date().isoformat(),
            "entity": entity,
            "metric": "feed_item",
            "value": 1,
            "dims": {
                "title": title,
                "outlet": outlet,
                "kind": kind,
                "published": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "in_title": mentions(title),
            },
        }


def parse_posts(payload: list, tag: str, entity: str):
    """One row per dev.to post, valued at its reactions."""
    for post in payload:
        if not (post.get("url") and post.get("title") and post.get("published_at")):
            continue
        yield {
            "source_url": post["url"],
            "as_of": post["published_at"][:10],
            "entity": entity,
            "metric": "post_reactions",
            "value": post.get("public_reactions_count") or 0,
            "dims": {
                "id": str(post["id"]),
                "title": post["title"],
                "comments": post.get("comments_count") or 0,
                "author": (post.get("user") or {}).get("username"),
                "published": post["published_at"],
                "tag": tag,
            },
        }


def parse_repos(payload: dict, entity: str):
    """One row per repository in a GitHub topic search, valued at its stars."""
    for repo in payload["items"]:
        yield {
            "source_url": repo["html_url"],
            "as_of": repo["created_at"][:10],
            "entity": entity,
            "metric": "repo_stars",
            "value": repo["stargazers_count"],
            "dims": {
                "name": repo["full_name"],
                "description": repo.get("description"),
                "language": repo.get("language"),
                "created": repo["created_at"],
            },
        }


@source(
    id="news_headlines",
    page=PAGE,
    label="Google News headlines about Anthropic",
    url="https://news.google.com/",
    method="api",
    tier="press",
    cadence="weekly",
    sla_days=9,
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
    label="Hacker News stories about Anthropic and Claude",
    url="https://news.ycombinator.com/",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=True,
    caveats="Automated HTTP GET of the Algolia Hacker News search for each word in config/identifiers/hot_pick.yaml (Anthropic and Claude); no login. "
    "Stories from the last 30 days whose title names Anthropic, or Claude beside an AI word, valued at their points when read. Points count "
    "upvotes, not importance, and a story's points rise for a day or two after it is posted.",
)
def hn_stories(company):
    since = int(time.time()) - HN_DAYS * 86400
    seen = set()
    for query in company.ids(PAGE).get("hn_queries") or []:
        params = {"query": query, "tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 100}
        for row in parse_stories(get(ALGOLIA, params=params).json(), company.slug):
            if row["dims"]["id"] not in seen and mentions(row["dims"]["title"]):
                seen.add(row["dims"]["id"])
                yield row


@source(
    id="ai_feeds",
    page=PAGE,
    label="AI news sites and blogs: items naming Anthropic or Claude",
    url="https://en.wikipedia.org/wiki/RSS",
    method="api",
    tier="press",
    cadence="weekly",
    sla_days=9,
    backfillable=False,
    caveats="Automated HTTP GET of each publisher's or author's public RSS or Atom feed, listed in config/identifiers/hot_pick.yaml; no POST, login or "
    "browser automation. Each feed holds only its latest items (a few days for busy sites), so the month fills up only if we collect at least weekly. Only "
    "items whose title or summary names Anthropic, or Claude beside an AI word, are kept, and only their title, link and date. A feed that fails is skipped "
    "and named on stderr; if every feed fails the run fails.",
)
def ai_feeds(company):
    feeds = company.ids(PAGE).get("feeds") or []
    now, ok = datetime.now(UTC), 0
    for feed in feeds:
        try:
            rows = list(
                parse_feed(get(feed["url"], attempts=2, timeout=30).text, feed["name"], feed["kind"], company.slug, now)
            )
        except Exception as e:  # noqa: BLE001 - one broken feed must not lose the others
            print(f"ai_feeds: skipped {feed['name']}: {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            continue
        ok += 1
        yield from rows
        time.sleep(0.3)
    if feeds and not ok:
        raise RuntimeError("no feed could be read")


@source(
    id="community_posts",
    page=PAGE,
    label="dev.to posts about Claude and Anthropic",
    url="https://dev.to/",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=True,
    caveats="Automated HTTP GET of dev.to's public API: each tag's most popular posts of the last 30 days, valued at their reactions when read. "
    "Reactions count likes, not readers, and dev.to posts are developers' own writing, so they show what people are building and discussing, not what is true.",
)
def community_posts(company):
    seen = set()
    for tag in company.ids(PAGE).get("devto_tags") or []:
        payload = get(DEVTO, params={"tag": tag, "top": 30, "per_page": 30}).json()
        for row in parse_posts(payload, tag, company.slug):
            if row["dims"]["id"] not in seen:
                seen.add(row["dims"]["id"])
                yield row


@source(
    id="github_repos",
    page=PAGE,
    label="New GitHub repositories about Claude and Anthropic",
    url="https://github.com/search",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=True,
    caveats="Automated GitHub search API call per search in config/identifiers/hot_pick.yaml: repositories created in the last 30 days, the ten with the "
    "most stars for each search, valued at their stars when read. Searches are GitHub topics (claude-code, claude, anthropic), so a repository is "
    "included because its author tagged it. "
    "Stars can be bought or gamed, so a repository's stars show attention, not quality.",
)
def github_repos(company):
    since = (datetime.now(UTC) - timedelta(days=REPO_DAYS)).date().isoformat()
    seen = set()
    for query in company.ids(PAGE).get("github_queries") or []:
        params = {"q": f"{query} created:>={since}", "sort": "stars", "order": "desc", "per_page": 10}
        for row in parse_repos(github_get(GITHUB_SEARCH, params=params).json(), company.slug):
            if row["dims"]["name"] not in seen:
                seen.add(row["dims"]["name"])
                yield row
