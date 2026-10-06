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
ARENA_URL = "https://arena.ai/leaderboard/text?styleControl=off"
ARTIFICIAL_ANALYSIS_URL = "https://artificialanalysis.ai/leaderboards/models/"
LIVEBENCH_URL = "https://livebench.ai/#/"
LIVEBENCH_FILES_API = "https://api.github.com/repos/LiveBench/new-livebench/contents/public?ref=main"
LIVEBENCH_FILES_BASE = "https://livebench.ai"
OPENROUTER_URL = "https://openrouter.ai/rankings?view=week"


def today() -> date:
    return datetime.now(UTC).date()


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


def _context_tokens(text: object) -> int | None:
    match = re.fullmatch(r"\s*([\d.]+)\s*([KMB]?)\s*", str(text), flags=re.IGNORECASE)
    if not match:
        return None
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
    return round(float(match.group(1)) * scale)


def _arena_model(text: object, organization: str) -> str:
    """Remove the organization/license text pandas concatenates into Arena's model cell."""
    value = str(text).split(f"{organization} ·", 1)[0]
    return value.removeprefix(organization)


def parse_arena_html(html: str) -> tuple[str, list[dict]]:
    """Return Arena's data date and normalized text-leaderboard rows."""
    page_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    dates = re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}\b", page_text)
    if not dates:
        raise ValueError("Arena leaderboard has no visible data date")
    as_of = datetime.strptime(dates[0], "%b %d, %Y").replace(tzinfo=UTC).date().isoformat()
    tables = pd.read_html(StringIO(html))
    required = {"Rank", "Model", "Score", "Votes", "Price $/M", "Context"}
    table = next((candidate for candidate in tables if required <= set(candidate.columns)), None)
    if table is None:
        raise ValueError("Arena text leaderboard table was not found")
    rows = []
    for organization in ("Anthropic", "OpenAI", "Google", "SpaceXAI", "Mistral", "Cohere"):
        matched = table[table["Model"].astype(str).str.contains(organization, case=False, na=False)]
        for row in matched.to_dict("records"):
            score_text = str(row["Score"])
            price = re.search(r"\$([\d.]+)\s*/\s*\$([\d.]+)", str(row["Price $/M"]))
            rows.append(
                {
                    "organization": organization,
                    "model": _arena_model(row["Model"], organization),
                    "rank": int(float(row["Rank"])),
                    "score": _number(score_text),
                    "score_margin": _number(score_text.split("±", 1)[1]) if "±" in score_text else None,
                    "votes": int(float(row["Votes"])),
                    "input_price": float(price.group(1)) if price else None,
                    "output_price": float(price.group(2)) if price else None,
                    "context_tokens": _context_tokens(row["Context"]),
                    "preliminary": "preliminary" in score_text.lower(),
                }
            )
    return as_of, rows


@lru_cache(maxsize=1)
def _arena_snapshot() -> tuple[str, list[dict]]:
    return parse_arena_html(get(ARENA_URL).text)


@source(
    id="arena_text_leaderboard",
    page=PAGE,
    label="Arena text leaderboard",
    url=ARENA_URL,
    method="scrape",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=False,
    caveats="Automated weekly HTTP GET of the server-rendered leaderboard table; no POST, login, API key or browser automation. "
    "Arena scores are human preference votes, not objective task accuracy. Reasoning levels and model variants appear "
    "separately; preliminary scores can move. Price and context are Arena's reported fields, not independently audited.",
)
def arena_text_leaderboard(company):
    organization = company.ids(PAGE).get("arena_org")
    if not organization:
        return
    as_of, rows = _arena_snapshot()
    for row in rows:
        if row["organization"] != organization:
            continue
        common = {
            "source_url": ARENA_URL,
            "as_of": as_of,
            "entity": company.slug,
            "dims": {
                "model": row["model"],
                "organization": organization,
                "rank": row["rank"],
                "score_margin": row["score_margin"],
                "preliminary": row["preliminary"],
            },
        }
        measures = {
            "arena_text_score": row["score"],
            "arena_text_votes": row["votes"],
            "api_price_input_usd_mtok": row["input_price"],
            "api_price_output_usd_mtok": row["output_price"],
            "context_window_tokens": row["context_tokens"],
        }
        for metric, value in measures.items():
            if value is not None:
                yield {**common, "metric": metric, "value": value}


def parse_artificial_analysis_html(html: str, as_of: str) -> list[dict]:
    """Normalize Artificial Analysis's public, server-rendered model leaderboard."""
    tables = pd.read_html(StringIO(html))
    table = tables[0] if tables else None
    if table is None:
        raise ValueError("Artificial Analysis model leaderboard table was not found")
    labels = {
        "model": "Model",
        "context": "Context Window",
        "creator": "Creator",
        "intelligence": "Artificial Analysis Intelligence Index",
        "cost": "Cost per Task",
        "speed": "MedianTokens/s",
        "latency": "LatencyFirst Chunk (s)",
        "total": "TotalResponse (s)",
    }

    def flattened(column: object) -> str:
        values = column if isinstance(column, tuple) else (column,)
        return "".join(str(value) for value in values if not str(value).startswith("Unnamed:"))

    columns = {
        key: next((column for column in table.columns if label in flattened(column)), None)
        for key, label in labels.items()
    }
    if any(column is None for column in columns.values()):
        missing = [key for key, column in columns.items() if column is None]
        raise ValueError(f"Artificial Analysis leaderboard is missing columns {missing}")
    rows = []
    for record in table.to_dict("records"):
        model = str(record[columns["model"]]).strip()
        creator = str(record[columns["creator"]]).strip()
        if not model or model == "nan" or not creator or creator == "nan":
            continue
        index_text = str(record[columns["intelligence"]])
        rows.append(
            {
                "as_of": as_of,
                "model": model,
                "creator": creator,
                "context_tokens": _context_tokens(record[columns["context"]]),
                "intelligence_index": _number(index_text),
                "independently_evaluated": "*" in index_text,
                "cost_per_task": _number(record[columns["cost"]]),
                "output_tokens_s": _number(record[columns["speed"]]),
                "first_chunk_seconds": _number(record[columns["latency"]]),
                "total_response_seconds": _number(record[columns["total"]]),
            }
        )
    return rows


@lru_cache(maxsize=1)
def _artificial_analysis_snapshot() -> list[dict]:
    return parse_artificial_analysis_html(get(ARTIFICIAL_ANALYSIS_URL).text, today().isoformat())


@source(
    id="artificial_analysis_leaderboard",
    page=PAGE,
    label="Artificial Analysis model leaderboard",
    url=ARTIFICIAL_ANALYSIS_URL,
    method="scrape",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=False,
    caveats="Automated weekly HTTP GET of the public server-rendered leaderboard table; no POST, login, API key or browser automation. "
    "Artificial Analysis offers a keyed Data API, but this collector does not use it. Cost per task is the weighted-average "
    "cost of one task in the Artificial Analysis Intelligence Index workload, not a generic business task. First-chunk latency, "
    "output speed and total response time are cross-provider measurements and can vary by endpoint and load. The data date is the retrieval date because the live table exposes no separate snapshot date.",
)
def artificial_analysis_leaderboard(company):
    creator = company.ids(PAGE).get("artificial_analysis_creator")
    if not creator:
        return
    aliases = company.ids(PAGE).get("artificial_analysis_model_aliases") or {}
    for row in _artificial_analysis_snapshot():
        if row["creator"] != creator:
            continue
        common = {
            "source_url": ARTIFICIAL_ANALYSIS_URL,
            "as_of": row["as_of"],
            "entity": company.slug,
            "dims": {
                "model": row["model"],
                "creator": creator,
                "arena_model": aliases.get(row["model"]),
                "independently_evaluated": row["independently_evaluated"],
            },
        }
        measures = {
            "context_window_tokens": row["context_tokens"],
            "artificial_analysis_intelligence_index": row["intelligence_index"],
            "artificial_analysis_cost_per_task_usd": row["cost_per_task"],
            "artificial_analysis_output_tokens_s": row["output_tokens_s"],
            "artificial_analysis_first_chunk_seconds": row["first_chunk_seconds"],
            "artificial_analysis_total_response_seconds": row["total_response_seconds"],
        }
        for metric, value in measures.items():
            if value is not None:
                yield {**common, "metric": metric, "value": value}


def latest_livebench_release(files: list[dict]) -> str:
    """Newest release with score, category and cost files all present."""
    names = {item.get("name") for item in files}
    releases = {
        match.group(1)
        for name in names
        if isinstance(name, str) and (match := re.fullmatch(r"table_(\d{4}_\d{2}_\d{2})\.csv", name))
    }
    complete = [
        release for release in releases if f"categories_{release}.json" in names and f"cost_{release}.csv" in names
    ]
    if not complete:
        raise ValueError("LiveBench has no release with table, category and cost files")
    return max(complete)


def parse_livebench_files(table_csv: str, cost_csv: str, categories: dict, release: str) -> list[dict]:
    """Reproduce LiveBench's category/overall means and join its published cost fields."""
    scores = pd.read_csv(StringIO(table_csv))
    costs = pd.read_csv(StringIO(cost_csv))
    if "model" not in scores or "model" not in costs:
        raise ValueError("LiveBench score or cost file has no model column")
    category_columns = {
        name: tasks for name, tasks in categories.items() if tasks and all(task in scores for task in tasks)
    }
    if set(category_columns) != set(categories):
        missing = sorted(set(categories) - set(category_columns))
        raise ValueError(f"LiveBench score file is missing task columns for {missing}")
    required_costs = {
        "cost_per_question",
        "cost_per_successful_task",
        "avg_input_tokens",
        "avg_output_tokens",
        "input_price_per_million",
        "output_price_per_million",
    }
    if missing := required_costs - set(costs):
        raise ValueError(f"LiveBench cost file is missing columns {sorted(missing)}")
    costs = costs.set_index("model")
    rows = []
    for record in scores.to_dict("records"):
        model = str(record["model"])
        category_scores = {
            category: float(pd.Series([record[task] for task in tasks], dtype=float).mean())
            for category, tasks in category_columns.items()
        }
        cost = costs.loc[model] if model in costs.index else None
        rows.append(
            {
                "release": release.replace("_", "-"),
                "model": model,
                "overall": float(pd.Series(category_scores.values(), dtype=float).mean()),
                "categories": category_scores,
                "cost_per_question": _number(cost["cost_per_question"]) if cost is not None else None,
                "cost_per_successful_task": _number(cost["cost_per_successful_task"]) if cost is not None else None,
                "avg_input_tokens": _number(cost["avg_input_tokens"]) if cost is not None else None,
                "avg_output_tokens": _number(cost["avg_output_tokens"]) if cost is not None else None,
                "input_price": _number(cost["input_price_per_million"]) if cost is not None else None,
                "output_price": _number(cost["output_price_per_million"]) if cost is not None else None,
            }
        )
    return rows


@lru_cache(maxsize=1)
def _livebench_snapshot() -> tuple[str, list[dict], str, str]:
    release_slug = latest_livebench_release(get(LIVEBENCH_FILES_API).json())
    table_url = f"{LIVEBENCH_FILES_BASE}/table_{release_slug}.csv"
    cost_url = f"{LIVEBENCH_FILES_BASE}/cost_{release_slug}.csv"
    categories_url = f"{LIVEBENCH_FILES_BASE}/categories_{release_slug}.json"
    rows = parse_livebench_files(
        get(table_url).text,
        get(cost_url).text,
        get(categories_url).json(),
        release_slug,
    )
    return release_slug.replace("_", "-"), rows, table_url, cost_url


@source(
    id="livebench_leaderboard",
    page=PAGE,
    label="LiveBench objective benchmark and cost",
    url=LIVEBENCH_URL,
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=True,
    caveats="Automated HTTP GET: GitHub's public contents API discovers the newest release with table, categories and cost files, then versioned CSV/JSON files are fetched from livebench.ai. No POST, login, API key or browser automation. Overall is the mean of category averages. Cost per successful task is LiveBench's cost per question divided by score fraction; it depends on the release workload, recorded token use and model pricing. Releases are not directly comparable when tasks change. The current release has no Mistral or Cohere model.",
)
def livebench_leaderboard(company):
    prefixes = tuple(company.ids(PAGE).get("livebench_prefixes") or ())
    if not prefixes:
        return
    release, rows, table_url, cost_url = _livebench_snapshot()
    category_metrics = {
        "Reasoning": "livebench_reasoning_score",
        "Coding": "livebench_coding_score",
        "Agentic Coding": "livebench_agentic_coding_score",
        "Mathematics": "livebench_mathematics_score",
        "Data Analysis": "livebench_data_analysis_score",
        "Language": "livebench_language_score",
        "IF": "livebench_instruction_following_score",
    }
    for row in rows:
        if not row["model"].startswith(prefixes):
            continue
        common = {
            "as_of": release,
            "entity": company.slug,
            "dims": {"model": row["model"], "release": release},
        }
        score_measures = {"livebench_overall_score": row["overall"]} | {
            metric: row["categories"].get(category) for category, metric in category_metrics.items()
        }
        cost_measures = {
            "livebench_cost_per_question_usd": row["cost_per_question"],
            "livebench_cost_per_successful_task_usd": row["cost_per_successful_task"],
            "livebench_avg_input_tokens": row["avg_input_tokens"],
            "livebench_avg_output_tokens": row["avg_output_tokens"],
            "api_price_input_usd_mtok": row["input_price"],
            "api_price_output_usd_mtok": row["output_price"],
        }
        for metric, value in score_measures.items():
            if value is not None:
                yield {**common, "source_url": table_url, "metric": metric, "value": value}
        for metric, value in cost_measures.items():
            if value is not None:
                yield {**common, "source_url": cost_url, "metric": metric, "value": value}


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
    id="product_plan_prices",
    label="Claude consumer and team plan prices (cited ledger)",
    url="https://claude.com/pricing",
    cadence="monthly",
    sla_days=35,
    caveats="Plan prices as published on claude.com/pricing on the day retrieved, with the page text quoted. "
    "No stable public price-history API is used. The page shows annual-billing prices; monthly-billing prices are not read. "
    "Update data/ledgers/product_plan_prices.csv from the official page and quote, then rebuild.",
)
def product_plan_prices(company):
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
