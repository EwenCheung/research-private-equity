"""Licensed alt-data sources (YipitData, M Science): data the team has paid for and enters by hand.

There is no API, so these collect nothing. Figures are entered through pipeline/licensed.py (`python -m pipeline.licensed`), which
validates them and stamps who entered them and when; the core reads data/manual/<source>.csv. Entry code lives outside this folder
because every module here is auto-imported by the registry.
"""

from pipeline.core import source
from pipeline.core.store import CSV_FIELDS

PAGE = "licensed_data"
HEADER = [
    "as_of",
    "entity",
    "metric",
    "value",
    "dims",
    *[f for f in CSV_FIELDS if f not in ("as_of", "entity", "metric")],
]
METRICS = ("panel_spend_usd", "panel_users", "panel_growth_yoy")


def vendor(**meta):
    """A manual source: collect never runs it; the core reads data/manual/<id>.csv."""

    def register(fn):
        return source(
            **{
                "page": PAGE,
                "method": "manual",
                "tier": "vendor",
                "cadence": "weekly",
                "sla_days": 8,
                "backfillable": True,
                **meta,
            }
        )(fn)

    return register


@vendor(
    id="yipit_consumer",
    label="YipitData panel (entered by hand)",
    url="urn:yipitdata:weekly-report",
    caveats="Licensed. Figures are read from YipitData's report and entered by hand, so they are as fresh as the last entry. "
    "A panel projection is an estimate of spend, not the company's revenue; each row quotes its report and page.",
)
def yipit_consumer(company):
    return iter(())


@vendor(
    id="mscience_panel",
    label="M Science panel (entered by hand)",
    url="urn:mscience:panel-report",
    caveats="Licensed. Figures are read from M Science's report and entered by hand, so they are as fresh as the last entry. "
    "A panel projection is an estimate of spend, not the company's revenue; each row quotes its report and page.",
)
def mscience_panel(company):
    return iter(())
