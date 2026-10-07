"""Product reliability: the public status pages of Claude and OpenAI."""

import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "product"
HISTORY_START = date(2023, 1, 1)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TIME = re.compile(r"(?:([A-Z][a-z]{2}) (\d{1,2}), )?(\d{2}):(\d{2})")
CDX = "https://web.archive.org/cdx/search/cdx"
ULID_DIGITS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID = re.compile(rf"^[{ULID_DIGITS}]{{26}}$")
ARCHIVE_FROM = "202502"  # OpenAI's incident.io status page began in Feb 2025; its earlier history is a different system
ARCHIVE_OVERLAP = timedelta(days=100)  # a feed copy holds about three months, so a re-run starts this far back


def parse_span(text: str, container_year: int, container_month: int) -> tuple[datetime, datetime | None]:
    """'Sep 30, 23:50 - Oct 1, 00:10 UTC' or 'Oct 1, 16:20 - 22:36 UTC' -> (start, end) in UTC.

    The history groups an incident under the month it ended in, so a start month after the container's month is last year's.
    """
    found = TIME.findall(text)
    if not found or not found[0][0]:
        raise ValueError(f"unrecognised incident time {text!r}")
    mon, day, hh, mm = found[0]
    start_month = MONTHS.index(mon) + 1
    year = container_year - (1 if start_month > container_month else 0)
    start = datetime(year, start_month, int(day), int(hh), int(mm), tzinfo=UTC)
    if len(found) < 2:
        return start, None
    mon2, day2, hh2, mm2 = found[1]
    end_month, end_day = (MONTHS.index(mon2) + 1, int(day2)) if mon2 else (start_month, int(day))
    end = datetime(year + (1 if end_month < start_month else 0), end_month, end_day, int(hh2), int(mm2), tzinfo=UTC)
    return start, end


def incident(company, host: str, code: str, name: str, impact: str, start: datetime, end: datetime | None) -> dict:
    return {
        "source_url": f"https://{host}/incidents/{code}",
        "as_of": start.date().isoformat(),
        "entity": company.slug,
        "metric": "incident",
        "value": 1,
        "dims": {
            "code": code,
            "name": name,
            "impact": impact,
            "started": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolved": end.strftime("%Y-%m-%dT%H:%M:%SZ") if end else None,
            "minutes": int((end - start).total_seconds() // 60) if end else None,
        },
    }


def ulid_time(code: str) -> datetime:
    """When an incident.io id was minted: the first ten characters of a ULID are its creation time in milliseconds."""
    n = 0
    for c in code[:10]:
        n = n * 32 + ULID_DIGITS.index(c)
    return datetime.fromtimestamp(n / 1000, UTC)


def parse_feed(text: str, company, host: str):
    """One incident per item of an incident.io status feed. The feed carries no impact rating, so these rows are 'unrated'.

    The start is the earlier of the id's creation time and the item's pubDate (its last update). Normally the id is the earlier, so an
    incident is dated when it was posted. OpenAI's move to this page in Feb 2025 gave its older incidents new ids, all minted that day,
    and for those the pubDate holds the real date.
    """
    for item in ET.fromstring(text).iter("item"):
        code = (item.findtext("link") or "").rstrip("/").rsplit("/", 1)[-1]
        name = (item.findtext("title") or "").strip()
        if not (ULID.match(code) and name):
            continue
        start = ulid_time(code)
        if updated := item.findtext("pubDate"):
            start = min(start, parsedate_to_datetime(updated).astimezone(UTC))
        yield incident(company, host, code, name, "unrated", start, None)


def archive_captures(host: str, since: str | None) -> list[str]:
    """The first Internet Archive copy of the feed in each month, from `since` (YYYYMMDD) or the page's start."""
    params = {
        "url": f"{host}/feed.rss",
        "output": "json",
        "fl": "timestamp",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
    }
    stamps = [r[0] for r in get(CDX, params=params, timeout=120).json()[1:]]
    return [ts for ts in stamps if ts >= (since or ARCHIVE_FROM + "01")]


@source(
    id="status_incidents",
    page=PAGE,
    label="Claude and OpenAI status pages",
    url="https://{host}/history",
    method="api",
    tier="company-stated",
    cadence="daily",
    sla_days=2,
    backfillable=True,
    caveats="Automated HTTP GET of status.claude.com/history.json?page=N, and of status.openai.com/api/v2/incidents.json and feed.rss; no POST, "
    "authentication or browser automation. Incidents are the ones each company chose to post, so a company that posts more freely looks worse. "
    "Claude's page gives full history back to 2023 with Anthropic's impact rating. OpenAI's JSON feed holds only its latest 25 incidents, with its impact rating; "
    "its RSS feed holds about three months of incidents without a rating. Incident length runs from first post to resolved, and unresolved incidents have none.",
)
def status_incidents(company):
    status = company.ids(PAGE).get("status")
    if not status:
        return
    host = status["host"]
    if status["kind"] == "incident_io":
        seen = set()
        for i in get(f"https://{host}/api/v2/incidents.json").json()["incidents"]:
            seen.add(i["id"])
            start = datetime.fromisoformat(i["created_at"])
            end = datetime.fromisoformat(i["resolved_at"]) if i.get("resolved_at") else None
            yield incident(company, host, i["id"], i["name"], i.get("impact") or "none", start, end)
        for row in parse_feed(get(f"https://{host}/feed.rss").text, company, host):
            if row["dims"]["code"] not in seen:
                yield row
        return
    last = latest_as_of(company.root, "status_incidents", company.slug)
    cutoff = max(HISTORY_START, date.fromisoformat(last) - timedelta(days=21)) if last else HISTORY_START
    page = 1
    while True:
        months = get(f"https://{host}/history.json", params={"page": page}).json().get("months", [])
        if not months:
            return
        for m in months:
            month_no = MONTHS.index(m["name"][:3]) + 1
            for i in m["incidents"]:
                start, end = parse_span(re.sub(r"<[^>]+>", "", i["timestamp"]), m["year"], month_no)
                if start.date() >= cutoff:
                    yield incident(company, host, i["code"], i["name"], i["impact"], start, end)
        if date(months[-1]["year"], MONTHS.index(months[-1]["name"][:3]) + 1, 1) < cutoff.replace(day=1):
            return  # the page reaches back past what we need
        page += 1
        time.sleep(0.5)


@source(
    id="status_incidents_archive",
    page=PAGE,
    label="Internet Archive copies of the OpenAI status feed",
    url="https://web.archive.org/cdx/search/cdx?url={host}/feed.rss",
    method="scrape",
    tier="company-stated",
    cadence="quarterly",
    sla_days=100,
    backfillable=True,
    caveats="One copy per month of status.openai.com/feed.rss from the Internet Archive, from March 2025 (when OpenAI's status page moved to a new system). "
    "Each copy holds about three months of incidents, so neighbouring copies overlap and no month falls between them; a month the Archive "
    "did not copy is covered by the next copy. Rows carry no impact rating. The live feed (status_incidents) keeps the history up to date, "
    "so this only needs to run again if collection lapses for more than three months.",
)
def status_incidents_archive(company):
    status = company.ids(PAGE).get("status")
    if not status or status["kind"] != "incident_io":
        return
    host = status["host"]
    last = latest_as_of(company.root, "status_incidents_archive", company.slug)
    since = (date.fromisoformat(last) - ARCHIVE_OVERLAP).strftime("%Y%m%d") if last else None
    feed = f"https://{host}/feed.rss"
    for ts in archive_captures(host, since):
        time.sleep(5)  # the Archive refuses connections when crawled faster than a few requests a minute
        page = f"https://web.archive.org/web/{ts}/{feed}"
        text = get(f"https://web.archive.org/web/{ts}id_/{feed}", attempts=5, timeout=120).text
        for row in parse_feed(text, company, host):
            yield {**row, "source_url": page, "dims": {**row["dims"], "capture": ts}}


# ---- platform usage and benchmarks ----
