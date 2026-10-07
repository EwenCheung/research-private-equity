"""Signal page inputs: when each company released a model, and the funding rounds that give Anthropic a valuation."""

from datetime import UTC, datetime

from pipeline.core import source
from pipeline.core.http import get

PAGE = "signal"
MODELS = "https://openrouter.ai/api/v1/models"


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
