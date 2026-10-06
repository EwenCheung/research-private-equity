"""Hiring: live job boards (counts daily, every posting weekly) and Wayback history of the same boards."""

import html
import json
import re
import time
from datetime import UTC, datetime
from functools import cache

import httpx

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "hiring"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{board}/departments"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{board}"
CDX = "https://web.archive.org/cdx/search/cdx"


def today() -> str:
    return datetime.now(UTC).date().isoformat()


# ---- live boards ----


@cache
def board(provider: str, name: str) -> tuple[str, tuple[dict, ...]]:
    """(url, postings) for one board, fetched once per run and shared by the daily and weekly sources."""
    if provider == "greenhouse":
        url = GREENHOUSE.format(board=name)
        postings = [
            {
                "id": str(j["id"]),
                "title": j["title"],
                "department": d["name"],
                "location": (j.get("location") or {}).get("name", ""),
                "country": "",
                "first_published": (j.get("first_published") or "")[:10],
            }
            for d in get(url).json()["departments"]
            for j in d["jobs"]
        ]
    elif provider == "ashby":
        url = ASHBY.format(board=name)
        postings = [
            {
                "id": j["id"],
                "title": j["title"],
                "department": j.get("department") or "",
                "location": " | ".join(
                    [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
                ),
                "country": ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "",
                "first_published": (j.get("publishedAt") or "")[:10],
            }
            for j in get(url).json()["jobs"]
            if j.get("isListed", True)
        ]
    else:
        raise ValueError(f"unknown ATS provider {provider!r}")
    unique: dict[str, dict] = {}
    for p in postings:  # Greenhouse can list a job under several departments; the first listing wins
        unique.setdefault(p["id"], p)
    return url, tuple(unique.values())


def company_board(company):
    ats = company.ids(PAGE).get("ats")
    return board(ats["provider"], ats["board"]) if ats else (None, ())


@source(
    id="ats_open_roles",
    page=PAGE,
    label="Company job board API (Greenhouse / Ashby)",
    url="https://boards-api.greenhouse.io/v1/boards/{board}/departments",
    method="api",
    tier="company-stated",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="Roles live on the company's own public board on the day collected. Boards drop closed roles, so history "
    "before collection comes from Wayback captures. Google DeepMind posts only on Google's careers site (no public API).",
)
def ats_open_roles(company):
    url, postings = company_board(company)
    if url:
        yield {
            "source_url": url,
            "as_of": today(),
            "entity": company.slug,
            "metric": "open_roles",
            "value": len(postings),
            "dims": {"board": company.ids(PAGE)["ats"]["board"]},
        }


@source(
    id="ats_postings",
    page=PAGE,
    label="Company job board API: every open posting",
    url="https://boards-api.greenhouse.io/v1/boards/{board}/departments",
    method="api",
    tier="company-stated",
    cadence="weekly",
    sla_days=8,
    backfillable=False,
    caveats="Every listed posting's title, department and location, weekly. Function and region are classified from "
    "these words by rules shown on each chart, so they can be re-run if the rules improve.",
)
def ats_postings(company):
    url, postings = company_board(company)
    for p in postings:
        yield {
            "source_url": url,
            "as_of": today(),
            "entity": company.slug,
            "metric": "job_posting",
            "value": 1,
            "dims": {k: p[k] for k in ("id", "title", "department", "location", "country", "first_published")},
        }


# ---- Wayback history ----


def patient_get(url: str, tries: int = 4):
    """GET that waits out the Internet Archive refusing connections when crawled too fast (minutes, not seconds)."""
    for attempt in range(tries):
        try:
            return get(url, timeout=90)
        except httpx.TransportError:
            if attempt == tries - 1:
                raise
            time.sleep(60 * (attempt + 1))
    raise AssertionError("unreachable")


def httpx_json(url: str, params: dict) -> list:
    """The CDX index can also refuse connections under load: same patience as capture pages."""
    full = str(httpx.URL(url, params=params))
    return json.loads(patient_get(full).text)


def captures(pattern: str, since: str | None) -> list[tuple[str, str]]:
    """(timestamp, original url) of monthly 200-OK captures of a board URL, newer than `since`."""
    params = {
        "url": pattern,
        "output": "json",
        "fl": "timestamp,original",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
    }
    rows = httpx_json(CDX, params)[1:]
    return [(ts, url) for ts, url in rows if not since or ts[:8] > since.replace("-", "")]


def parse_capture(url: str, body: str) -> tuple[int | None, list[dict]]:
    """(open-role count, postings) from one archived board page. Postings only when the page lists every role."""
    host = url.split("/")[2]
    if host == "boards-api.greenhouse.io":
        jobs = json.loads(body).get("jobs", [])
        return len(jobs), [
            {"title": j["title"], "department": "", "location": (j.get("location") or {}).get("name", "")} for j in jobs
        ]
    if host == "boards.greenhouse.io":
        openings = re.findall(r'<div class="opening"[^>]*>(.*?)</div>', body, re.DOTALL)
        posts = []
        for o in openings:
            title = re.search(r"<a[^>]*>\s*(.*?)\s*</a>", o, re.DOTALL)
            loc = re.search(r'<span class="location"[^>]*>\s*(.*?)\s*</span>', o, re.DOTALL)
            posts.append(
                {
                    "title": html.unescape(title.group(1)) if title else "",
                    "department": "",
                    "location": html.unescape(loc.group(1)) if loc else "",
                }
            )
        return (len(openings), posts) if openings else (None, [])
    if host == "job-boards.greenhouse.io":  # paginated 50 a page: the count is embedded, the titles are not complete
        m = re.search(r'"total"\s*:\s*(\d+)', body)
        return (int(m.group(1)), []) if m else (None, [])
    if host == "jobs.ashbyhq.com":
        start = body.find("window.__appData")
        if start < 0:
            return None, []
        data, _ = json.JSONDecoder().raw_decode(body[body.index("{", start) :])
        jobs = [j for j in (data.get("jobBoard") or {}).get("jobPostings", []) if j.get("isListed", True)]
        return len(jobs), [
            {
                "title": j.get("title", ""),
                "department": j.get("departmentName", ""),
                "location": j.get("locationName", ""),
            }
            for j in jobs
        ]
    if host == "jobs.lever.co":
        titles = re.findall(r'data-qa="posting-name"[^>]*>(.*?)</h5>', body, re.DOTALL)
        return (
            (len(titles), [{"title": html.unescape(t), "department": "", "location": ""} for t in titles])
            if titles
            else (None, [])
        )
    return None, []


@source(
    id="wayback_job_boards",
    page=PAGE,
    label="Internet Archive captures of company job boards",
    url="https://web.archive.org/cdx/search/cdx?url={board}",
    method="scrape",
    tier="company-stated",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="Monthly captures of each company's board, so history is only as dense as the Archive's crawls. "
    "Newer Greenhouse pages show 50 roles a page: they give the count but not every title. Unparseable captures are skipped.",
)
def wayback_job_boards(company):
    for pattern in company.ids(PAGE).get("wayback") or []:
        # resume per board URL: an interrupted run must not skip another board's earlier captures
        since = latest_as_of(company.root, "wayback_job_boards", company.slug, pattern=pattern)
        for ts, original in captures(pattern, since):
            page = f"https://web.archive.org/web/{ts}/{original}"
            time.sleep(5)  # the Archive refuses connections when crawled faster than a few requests a minute
            try:
                count, posts = parse_capture(original, patient_get(page).text)
            except ValueError:  # malformed archived JSON: skip this capture
                continue
            if count is None:
                continue
            as_of = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            stamp = {"source_url": page, "as_of": as_of, "entity": company.slug}
            where = {"capture": ts, "pattern": pattern}
            yield {**stamp, "metric": "open_roles", "value": count, "dims": {**where, "board_url": original}}
            for p in posts:
                yield {**stamp, "metric": "job_posting", "value": 1, "dims": {**p, **where}}
