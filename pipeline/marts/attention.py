"""Attention charts: how much the public and developers are looking at each company. Takeaways are computed from the rows."""

import calendar
from datetime import date

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import change, dims, latest, num, roll

MONTH = {"field": "month", "label": "Month", "format": "date"}
COMPANY = {"field": "company", "label": "Company", "format": "text"}


def month_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{calendar.month_abbr[d.month]} {d.year}"


def complete_months(df: pd.DataFrame, last_day: str, value_col: str = "value") -> pd.DataFrame:
    """Drop the month still running at the newest data day, so a part month never reads as a fall."""
    d = date.fromisoformat(last_day)
    if d.day != calendar.monthrange(d.year, d.month)[1]:
        df = df[df["as_of"] < d.replace(day=1).isoformat()]
    return df


def rank_by_latest(df: pd.DataFrame, col: str = "as_of") -> list[str]:
    last = df[df[col] == df[col].max()]
    return last.sort_values("value", ascending=False)["entity"].tolist()


def line_chart(
    title, subtitle, y_label, y_format, rows, takeaway, assumptions, *, value="value", extra_columns=(), badges=()
):
    return {
        "title": title,
        "subtitle": subtitle,
        "kind": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": value, "type": "quantitative", "label": y_label, "format": y_format},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [MONTH, COMPANY, {"field": value, "label": y_label, "format": y_format}, *extra_columns],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": assumptions,
        "badges": list(badges),
    }


def year_ago(df: pd.DataFrame, entity: str, month: str) -> float | None:
    prior = (date.fromisoformat(month).replace(year=date.fromisoformat(month).year - 1)).isoformat()
    row = df[(df["entity"] == entity) & (df["as_of"] == prior)]
    return float(row["value"].iloc[0]) if len(row) else None


# ---- Wikipedia ----
WIKI_NOTES = [
    "Human (non-bot) views of English Wikipedia articles: reader curiosity about the topic, not use of the product.",
    "Pages get renamed, so each topic sums every title it has had; a reader lands on one title, so nothing is double counted.",
    "Spikes follow launches, outages and news, so read the level over several months.",
]


def wiki_monthly(ctx, kind: str) -> tuple[pd.DataFrame, list[str]]:
    df = dims(ctx.obs(metric="wiki_pageviews"), "title", "kind")
    df = latest(df[df["kind"] == kind], keys=("entity", "title", "as_of"))
    if df.empty:
        return pd.DataFrame(columns=["entity", "as_of", "value"]), []
    titles = sorted(df["title"].unique())
    return complete_months(roll(df, "month"), df["as_of"].max()), titles


def wiki_chart(ctx, kind: str, title: str, subtitle: str, notes: list[str]):
    m, titles = wiki_monthly(ctx, kind)
    order = {e: i for i, e in enumerate(rank_by_latest(m))} if len(m) else {}
    m = m.assign(rank=m["entity"].map(order)).sort_values(["as_of", "rank"]) if len(m) else m
    rows = [
        {"month": r.as_of, "company": ctx.names.get(r.entity, r.entity), "views": int(r.value)} for r in m.itertuples()
    ]
    takeaway = []
    if len(m) and "anthropic" in set(m["entity"]):
        last = m["as_of"].max()
        now = m[m["as_of"] == last].set_index("entity")["value"]
        lead = now.idxmax()
        a = now["anthropic"]
        ago = year_ago(m, "anthropic", last)
        text = f"In {month_label(last)}, Anthropic's {'assistant' if kind == 'product' else 'company'} article drew {num(a)} Wikipedia views"
        text += f" ({change(a, ago)} on a year earlier)" if ago else ""
        text += (
            f", against {ctx.names.get(lead, lead)} at {num(now[lead])} ({now[lead] / a:.1f}× Anthropic)."
            if lead != "anthropic"
            else ", the most of the tracked companies."
        )
        takeaway = [text]
    return line_chart(
        title,
        subtitle,
        "Views per month",
        "int",
        rows,
        takeaway,
        notes + WIKI_NOTES + ([f"Articles summed: {', '.join(titles)}."] if titles else []),
        value="views",
    )


@mart(id="attention.wiki_products", sources=["wikipedia_pageviews"])
def wiki_products(ctx):
    return wiki_chart(
        ctx,
        "product",
        "Wikipedia views of each AI assistant, per month",
        "Wikipedia views per month of each assistant's article: Claude, ChatGPT, Gemini (with Bard), Grok, Le Chat",
        [
            "Claude had no article of its own before January 2024 (it was covered inside Anthropic's), so its series starts then.",
            "Cohere has no consumer assistant. Gemini includes its earlier name Bard and the Gemini model-family article.",
        ],
    )


# ---- news volume (GDELT) ----


# ---- Hacker News ----


# ---- App Store ----


# ---- Google Trends ----


# ---- recent headlines ----
