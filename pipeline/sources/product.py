"""Product, pricing and reliability: status pages, peer benchmarks, usage and cited ledgers."""

import re
import time
from datetime import UTC, date, datetime, timedelta

from pipeline.core import source
from pipeline.core.http import get
from pipeline.core.store import latest_as_of

PAGE = "product"
HISTORY_START = date(2023, 1, 1)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TIME = re.compile(r"(?:([A-Z][a-z]{2}) (\d{1,2}), )?(\d{2}):(\d{2})")
# Row links point at the endpoint's documentation: the API itself answers 401 to anyone without a key.


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


# ---- platform usage and benchmarks ----
