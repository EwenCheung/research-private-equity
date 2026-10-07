"""Product, pricing and reliability charts. Every number is computed from collected incidents or from cited ledger rows."""

import calendar
from datetime import UTC, date, datetime

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, month_label

IMPACTS = ["minor", "major", "critical"]
MONTH = {"field": "month", "label": "Month", "format": "date"}
STATUS_NOTE = "Incidents are the ones the company chose to post, with its own impact rating, so a company that posts more freely looks worse."


def incidents(ctx, entity: str) -> pd.DataFrame:
    """One row per incident (newest collection wins, but a rated copy beats an unrated one), with its dims as columns."""
    df = ctx.obs(metric="incident", entity=entity)
    if df.empty:
        return df
    df = dims(df, "code", "name", "impact", "started", "minutes")
    rated = df[df["impact"] != "unrated"]
    df = latest(
        pd.concat([rated, df[(df["impact"] == "unrated") & ~df["code"].isin(rated["code"])]]), keys=("entity", "code")
    )
    return df.assign(month=df["as_of"].str[:7] + "-01")


def complete_months(df: pd.DataFrame, last_day: str) -> pd.DataFrame:
    """Drop the month still running at the last collection day, so a part month never reads as an improvement."""
    d = date.fromisoformat(last_day)
    return df if d.day == calendar.monthrange(d.year, d.month)[1] else df[df["month"] < d.replace(day=1).isoformat()]


def last_collected(ctx) -> str:
    return str(ctx.df["retrieved_at"].max())[:10] if len(ctx.df) else datetime.now(UTC).date().isoformat()


# ---- usage ----


# ---- reliability ----
COMPARE = ("anthropic", "openai")
WHO = {
    "anthropic": "Claude",
    "openai": "OpenAI",
}  # the status pages are named for the product, the series for the company
COMPARE_FROM = "2025-03-01"  # OpenAI's status page moved to a new system in Feb 2025; its incident history starts there


def posted(ctx, entity: str) -> pd.DataFrame:
    """Every incident a company posted, whatever its impact rating. Maintenance notices are not incidents."""
    df = incidents(ctx, entity)
    return df[df["impact"] != "maintenance"] if len(df) else df


@mart(id="product.incidents_monthly", sources=["status_incidents", "status_incidents_archive"])
def incidents_monthly(ctx):
    last_day = last_collected(ctx)
    posts = {e: complete_months(posted(ctx, e), last_day) for e in COMPARE}
    posts = {e: df[df["month"] >= COMPARE_FROM] if len(df) else df for e, df in posts.items()}
    months = sorted({m for df in posts.values() for m in df["month"]})
    if months:
        months = [d.date().isoformat() for d in pd.date_range(months[0], months[-1], freq="MS")]
    rows, counts = [], {}
    for m in months:
        for e in COMPARE:
            month = posts[e][posts[e]["month"] == m] if len(posts[e]) else posts[e]
            n = len(month)
            counts[e, m] = n
            # an incident's rating is known only where the company's own page gave one, so a month is rated only if every incident is
            severe = (
                None if (month["impact"] == "unrated").any() else int(month["impact"].isin(["major", "critical"]).sum())
            )
            rows.append({"month": m, "company": ctx.names.get(e, e), "incidents": n, "severe": severe})
    takeaway = []
    if months:
        last = months[-1]
        takeaway.append(
            f"In {month_label(last)}, Claude's status page posted {counts['anthropic', last]} incidents and OpenAI's posted {counts['openai', last]}."
        )
        total = {e: sum(counts[e, m] for m in months) for e in COMPARE}
        more = sum(counts["anthropic", m] > counts["openai", m] for m in months)
        takeaway.append(
            f"Since {month_label(months[0])}, Claude's page posted {total['anthropic']} incidents against OpenAI's {total['openai']}, "
            f"and posted more in {more} of {len(months)} months."
        )
    return {
        "title": "Incidents on the Claude and OpenAI status pages, per month",
        "subtitle": "Every incident each company posted on its own status page, by the month it started, complete months. Fewer is better.",
        "kind": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "incidents", "type": "quantitative", "label": "Incidents", "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            MONTH,
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "incidents", "label": "Incidents", "format": "int"},
            {"field": "severe", "label": "Rated major or critical", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            STATUS_NOTE,
            (
                "Both companies are counted the same way: every incident posted, whatever its impact rating, with maintenance notices left out. "
                "The Briefing counts only Claude incidents rated minor or above, so its figure is lower."
            ),
            (
                "OpenAI's history before July 2026 is read from monthly Internet Archive copies of its status feed. They overlap, so no month is missing, "
                "but they carry no impact rating, so the rated column is blank for OpenAI except where its own page gave a rating for every incident that month."
            ),
            "More incidents partly reflects more usage and how freely a company posts, not only more failures.",
        ],
        "badges": [],
    }
