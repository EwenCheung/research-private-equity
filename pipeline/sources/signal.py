"""Signal page inputs: when each company released a model, and the funding rounds that give Anthropic a valuation."""

import re
from datetime import UTC, datetime
from functools import lru_cache
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

from pipeline.core import source
from pipeline.core.http import env_key, get

PAGE = "signal"
MODELS = "https://openrouter.ai/api/v1/models"
BENCHMARKS_API = "https://openrouter.ai/api/v1/benchmarks"
# Row links point at the endpoint's documentation: the API itself answers 401 to anyone without a key.
BENCHMARKS_DOCS = "https://openrouter.ai/docs/api/api-reference/benchmarks/list-benchmarks"
EVALS = ("gpqa_diamond",)
ARENA_URL = "https://arena.ai/leaderboard/text?styleControl=off"


def parse_models(payload: dict, org: str, entity: str):
    """One release per model the organisation has on the list, dated by when OpenRouter listed it.

    Variants of a model (ids with ':', such as ':batch' or ':free') are not new models, so they are left out.
    """
    for m in payload["data"]:
        prefix, _, slug = m["id"].partition("/")
        if prefix != org or ":" in slug or not m.get("created"):
            continue
        yield {
            "source_url": f"https://openrouter.ai/{m['id']}",
            "as_of": datetime.fromtimestamp(m["created"], UTC).date().isoformat(),
            "entity": entity,
            "metric": "model_release",
            "value": 1,
            "dims": {"model": m["id"], "name": (m.get("name") or slug).split(": ", 1)[-1]},
        }


@source(
    id="model_releases",
    page=PAGE,
    label="OpenRouter's public list of models",
    url=MODELS,
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=True,
    caveats="Automated HTTP GET of OpenRouter's public models list (no key, no login). A release is the date OpenRouter listed a model, not the "
    "company's announcement, so it can run a few days late, and Anthropic's models start in May 2025, so earlier releases are missing. "
    "Variants of a model (batch, free) are left out. Prices and other model details are not kept.",
)
def model_releases(company):
    org = company.ids(PAGE).get("openrouter")
    if org:
        yield from parse_models(get(MODELS).json(), org, company.slug)


@source(
    id="signal_funding_rounds",
    page=PAGE,
    label="Anthropic post-money valuations, from Anthropic's newsroom (cited ledger)",
    url="https://www.anthropic.com/news",
    method="ledger",
    tier="company-stated",
    cadence="quarterly",
    sla_days=100,
    backfillable=True,
    caveats="The post-money valuation Anthropic stated in each funding announcement, with the sentence quoted. Series A to C announcements state "
    "none, and Series D was never announced, so those are gaps, not zeros. Hand-maintained: add a row when a round is announced. It is a yardstick "
    "on the Signal page only; no Briefing verdict reads it.",
)
def signal_funding_rounds(company):
    return iter(())


# ---- how good, and how costly, each model is ----


@lru_cache(maxsize=1)
def _benchmarks() -> dict:
    key = env_key("OPENROUTER_API_KEY", "OpenRouter's benchmarks endpoint needs a free API key")
    return get(BENCHMARKS_API, headers={"Authorization": f"Bearer {key}"}).json()


@source(
    id="openrouter_benchmarks",
    page=PAGE,
    label="OpenRouter benchmark feed (Artificial Analysis Intelligence Index and OpenRouter's own evaluation)",
    url=BENCHMARKS_DOCS,
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=9,
    backfillable=False,
    caveats="Automated weekly HTTP GET of OpenRouter's /api/v1/benchmarks with a free API key (OPENROUTER_API_KEY); no POST or browser automation. "
    "One request returns every model. The Intelligence Index is Artificial Analysis's composite model-quality score, relayed unchanged "
    "(attribute it to Artificial Analysis); accuracy and cost per task on GPQA Diamond, a set of graduate-level science questions, are "
    "OpenRouter's own evaluation, so a cost per task is what one such question cost to run, reasoning tokens included, not a general business task. "
    "A model is matched to a company by OpenRouter's provider prefix. Without the key the source is skipped, and the last snapshot stays on the page.",
)
def openrouter_benchmarks(company):
    org = company.ids(PAGE).get("openrouter")
    if not org:
        return
    payload = _benchmarks()
    feed_date = payload["meta"]["as_of"][:10]
    for item in payload["data"]:
        if not item["model_permaslug"].startswith(f"{org}/"):
            continue
        common = {"source_url": BENCHMARKS_DOCS, "entity": company.slug}
        dims = {"model": item["display_name"], "permaslug": item["model_permaslug"]}
        if item["source"] == "artificial-analysis" and item.get("intelligence_index") is not None:
            yield {
                **common,
                "as_of": feed_date,
                "metric": "openrouter_aa_intelligence_index",
                "value": item["intelligence_index"],
                "dims": dims,
            }
        elif item["source"] == "openrouter" and item.get("benchmark_type") in EVALS:
            yield {
                **common,
                "as_of": item["last_run_timestamp"][:10],
                "metric": "openrouter_eval_accuracy",
                "value": item["accuracy"],
                "dims": dims
                | {
                    "benchmark": item["benchmark_type"],
                    "stddev": item["accuracy_stddev"],
                    "tasks": item["total_tasks"],
                    "cost_per_task_usd": item["avg_cost_per_task"],
                },
            }


def _number(text: object) -> float | None:
    match = re.search(r"-?[\d,.]+", str(text))
    return float(match.group().replace(",", "")) if match else None


def _arena_model(text: object, organization: str) -> str:
    """Remove the organization and licence text pandas concatenates into Arena's model cell."""
    return str(text).split(f"{organization} ·", 1)[0].removeprefix(organization)


def parse_arena_html(html: str, organizations: tuple[str, ...]) -> tuple[str, list[dict]]:
    """Arena's data date and the text-leaderboard rows of the named organizations."""
    page_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    dates = re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}\b", page_text)
    if not dates:
        raise ValueError("Arena leaderboard has no visible data date")
    as_of = datetime.strptime(dates[0], "%b %d, %Y").replace(tzinfo=UTC).date().isoformat()
    table = next(
        (t for t in pd.read_html(StringIO(html)) if {"Rank", "Model", "Score", "Votes"} <= set(t.columns)), None
    )
    if table is None:
        raise ValueError("Arena text leaderboard table was not found")
    rows = []
    for organization in organizations:
        matched = table[table["Model"].astype(str).str.contains(organization, case=False, na=False)]
        for row in matched.to_dict("records"):
            score_text = str(row["Score"])
            rows.append(
                {
                    "organization": organization,
                    "model": _arena_model(row["Model"], organization),
                    "rank": int(float(row["Rank"])),
                    "score": _number(score_text),
                    "score_margin": _number(score_text.split("±", 1)[1]) if "±" in score_text else None,
                    "votes": int(float(row["Votes"])),
                    "preliminary": "preliminary" in score_text.lower(),
                }
            )
    return as_of, rows


@lru_cache(maxsize=1)
def _arena() -> tuple[str, list[dict]]:
    return parse_arena_html(get(ARENA_URL).text, ("Anthropic", "OpenAI"))


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
    "separately; preliminary scores can move.",
)
def arena_text_leaderboard(company):
    organization = company.ids(PAGE).get("arena_org")
    if not organization:
        return
    as_of, rows = _arena()
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
        yield {**common, "metric": "arena_text_score", "value": row["score"]}
        yield {**common, "metric": "arena_text_votes", "value": row["votes"]}
