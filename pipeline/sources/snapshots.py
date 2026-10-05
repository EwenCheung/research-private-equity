"""Snapshot-only captures that can't be backfilled: their history starts the day collection starts. Raw only; no marts."""

from datetime import UTC, datetime
from functools import cache

import httpx

from pipeline.core import source

CHART_URL = "https://itunes.apple.com/us/rss/topfreeapplications/limit=100/json"


@cache
def _chart() -> dict[str, int]:
    """{app id: rank} for today's US top-free iPhone apps. One fetch per run, shared by every company."""
    r = httpx.get(CHART_URL, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(r.json()["feed"]["entry"], 1)}


@source(
    id="appstore_top_charts",
    page="attention",
    label="Apple App Store top-free chart (US)",
    url=CHART_URL,
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="Snapshot of Apple's public top-100 RSS, US iPhone storefront. An app outside the top 100 has no row (not rank 101+). "
    "Apple publishes no history, so a missed day is lost.",
)
def appstore_top_charts(company):
    app = company.ids("attention").get("appstore")
    if not app:
        return
    rank = _chart().get(app["id"])
    if rank is None:
        return
    yield {
        "source_url": CHART_URL,
        "as_of": datetime.now(UTC).date().isoformat(),
        "entity": company.slug,
        "metric": "appstore_rank",
        "value": rank,
        "dims": {"chart": "top_free", "country": "us", "app_id": app["id"], "app_name": app["name"]},
    }
