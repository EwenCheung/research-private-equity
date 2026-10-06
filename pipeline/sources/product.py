"""Product, pricing and reliability: status pages, peer benchmarks, usage and cited ledgers."""

import re
import time
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "product"
HISTORY_START = date(2023, 1, 1)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TIME = re.compile(r"(?:([A-Z][a-z]{2}) (\d{1,2}), )?(\d{2}):(\d{2})")
OPENROUTER_URL = "https://openrouter.ai/rankings?view=week"


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


@source(
    id="status_incidents",
    page=PAGE,
    label="Public status pages (Anthropic and OpenAI)",
    url="https://{host}/history.json",
    method="api",
    tier="company-stated",
    cadence="daily",
    sla_days=2,
    backfillable=True,
    caveats="Automated HTTP GET: Anthropic /history.json?page=N and OpenAI /api/v2/incidents.json; no POST, authentication "
    "or browser automation. Incidents are the ones the company chose to post, with its own impact rating, so a company that posts more freely looks worse. "
    "Claude's page gives full history back to 2023; OpenAI's feed holds only its most recent incidents, so its history starts "
    "when we began collecting. Incident length runs from first post to resolved, and unresolved incidents have none.",
)
def status_incidents(company):
    status = company.ids(PAGE).get("status")
    if not status:
        return
    host = status["host"]
    if status["kind"] == "incident_io":
        for i in get(f"https://{host}/api/v2/incidents.json").json()["incidents"]:
            start = datetime.fromisoformat(i["created_at"])
            end = datetime.fromisoformat(i["resolved_at"]) if i.get("resolved_at") else None
            yield incident(company, host, i["id"], i["name"], i.get("impact") or "none", start, end)
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


# ---- peer model comparison and platform usage ----


def _number(text: object) -> float | None:
    match = re.search(r"-?[\d,.]+", str(text))
    return float(match.group().replace(",", "")) if match else None


def _percent(text: object) -> float | None:
    value = _number(text)
    return value / 100 if value is not None else None


def parse_openrouter_html(html: str) -> tuple[str, list[dict]]:
    """Return the ranking data date and OpenRouter's author request-share table."""
    page_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    match = re.search(r"Usage data through ([A-Z][a-z]+ \d{1,2}, \d{4})", page_text)
    if not match:
        raise ValueError("OpenRouter rankings page has no usage-through date")
    as_of = datetime.strptime(match.group(1), "%b %d, %Y").replace(tzinfo=UTC).date().isoformat()
    tables = pd.read_html(StringIO(html))
    required = {"Rank", "Author", "Share of requests", "Change in requests"}
    table = next((candidate for candidate in tables if required <= set(candidate.columns)), None)
    if table is None:
        raise ValueError("OpenRouter author-share table was not found")
    rows = []
    for row in table.to_dict("records"):
        if pd.isna(row["Rank"]):
            continue
        share = _percent(row["Share of requests"])
        if share is None:
            continue
        rows.append(
            {
                "rank": int(float(row["Rank"])),
                "author": str(row["Author"]),
                "share": share,
                "change": _percent(row["Change in requests"]),
            }
        )
    return as_of, rows


@lru_cache(maxsize=1)
def _openrouter_snapshot() -> tuple[str, list[dict]]:
    return parse_openrouter_html(get(OPENROUTER_URL).text)


@source(
    id="openrouter_rankings",
    page=PAGE,
    label="OpenRouter author request share",
    url=OPENROUTER_URL,
    method="scrape",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=False,
    caveats="Automated weekly HTTP GET of the public rankings page; no POST, login, API key or browser automation. "
    "Share of requests on OpenRouter only: not users, tokens, revenue or total model-market share. The public table "
    "shows only leading authors, so an absent company is unknown rather than zero; private requests may be excluded.",
)
def openrouter_rankings(company):
    author = company.ids(PAGE).get("openrouter_author")
    if not author:
        return
    as_of, rows = _openrouter_snapshot()
    for row in rows:
        if row["author"] != author:
            continue
        common = {
            "source_url": OPENROUTER_URL,
            "as_of": as_of,
            "entity": company.slug,
            "dims": {"author": author, "rank": row["rank"], "window": "trailing 7 days"},
        }
        yield {**common, "metric": "openrouter_request_share", "value": row["share"]}
        if row["change"] is not None:
            yield {**common, "metric": "openrouter_request_change", "value": row["change"]}


# ---- cited ledgers: rows live in data/ledgers/<id>.csv, each with a source link, a verbatim quote and who entered it ----


def ledger(**meta):
    """A ledger source collects nothing: the core reads its CSV. Declared so it has an SLA, a label and caveats."""

    def register(fn):
        return source(**{"page": PAGE, "method": "ledger", "tier": "company-stated", "backfillable": True, **meta})(fn)

    return register


@ledger(
    id="product_model_releases",
    label="Anthropic model announcements (cited ledger)",
    url="https://platform.claude.com/docs/en/release-notes/overview",
    cadence="monthly",
    sla_days=35,
    caveats="One row per model, dated by Anthropic's own announcement or API release note, with the sentence quoted. "
    "No API is used because the history spans release notes and news pages. Models limited to Project Glasswing participants "
    "(Mythos) are included and marked. Update by verifying the linked announcement, appending a cited row to "
    "data/ledgers/product_model_releases.csv and rebuilding when a model ships.",
)
def product_model_releases(company):
    return iter(())


@ledger(
    id="product_api_prices",
    label="Anthropic API prices (cited ledger)",
    url="https://platform.claude.com/docs/en/about-claude/pricing",
    cadence="monthly",
    sla_days=35,
    caveats="Current list price per million tokens from Anthropic's pricing table (retrieved 2026-10-05) for models still listed; "
    "older models carry the price in their launch announcement. No versioned price-history API is available. List prices exclude "
    "batch, caching and long-context rates. Update data/ledgers/product_api_prices.csv from the official page and quote, then rebuild.",
)
def product_api_prices(company):
    return iter(())


@ledger(
    id="product_peer_plan_prices",
    label="Standard individual AI plan prices (cited ledger)",
    url="https://claude.com/pricing",
    cadence="monthly",
    sla_days=35,
    caveats="One representative standard paid individual plan per company, using US monthly list price before tax. "
    "No common API exists across the companies. Features and usage allowances differ, annual discounts are excluded, and "
    "Cohere has no comparable consumer plan. Check each row's official link monthly, append newly dated cited rows to "
    "data/ledgers/product_peer_plan_prices.csv and rebuild.",
)
def product_peer_plan_prices(company):
    return iter(())


@ledger(
    id="product_adoption_claims",
    label="Company-stated public adoption disclosures (cited ledger)",
    url="https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation",
    cadence="quarterly",
    sla_days=100,
    caveats="Company-stated snapshots with different scopes and periods: weekly users, monthly users, blended product reach, "
    "or high-spend customers. No comparable API exists and automatic numeric scraping would erase the scope. They must not "
    "be ranked as if equivalent; companies without a usable disclosure are omitted. Review official announcements quarterly, "
    "append clearly scoped cited rows to data/ledgers/product_adoption_claims.csv and rebuild.",
)
def product_adoption_claims(company):
    return iter(())
