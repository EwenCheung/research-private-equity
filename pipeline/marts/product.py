"""Product, pricing and reliability charts. Every number is computed from collected incidents or from cited ledger rows."""

import calendar
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, num

IMPACTS = ["minor", "major", "critical"]
MONTH = {"field": "month", "label": "Month", "format": "date"}
STATUS_NOTE = "Incidents are the ones the company chose to post, with its own impact rating, so a company that posts more freely looks worse."


def month_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{calendar.month_abbr[d.month]} {d.year}"


def incidents(ctx, entity: str) -> pd.DataFrame:
    """One row per incident (newest collection wins), with its dims as columns."""
    df = ctx.obs(metric="incident", entity=entity)
    if df.empty:
        return df
    df = latest(dims(df, "code", "name", "impact", "started", "minutes"), keys=("entity", "code"))
    return df.assign(month=df["as_of"].str[:7] + "-01")


def complete_months(df: pd.DataFrame, last_day: str) -> pd.DataFrame:
    """Drop the month still running at the last collection day, so a part month never reads as an improvement."""
    d = date.fromisoformat(last_day)
    return df if d.day == calendar.monthrange(d.year, d.month)[1] else df[df["month"] < d.replace(day=1).isoformat()]


def last_collected(ctx) -> str:
    return str(ctx.df["retrieved_at"].max())[:10] if len(ctx.df) else datetime.now(UTC).date().isoformat()


# ---- reliability ----
@mart(id="product.incidents_monthly", sources=["status_incidents"])
def incidents_monthly(ctx):
    df = incidents(ctx, "anthropic")
    rows, takeaway = [], []
    if len(df):
        df = complete_months(df, last_collected(ctx))
        counts = df.groupby(["month", "impact"]).size()
        for month in sorted(df["month"].unique()):
            for impact in IMPACTS:
                n = int(counts.get((month, impact), 0))
                if n:
                    rows.append({"month": month, "impact": impact.capitalize(), "incidents": n})
        by_month = df[df["impact"].isin(IMPACTS)].groupby("month").size()
        last = by_month.index.max()
        prior = (date.fromisoformat(last).replace(year=date.fromisoformat(last).year - 1)).isoformat()
        severe = int(df[(df["month"] == last) & df["impact"].isin(["major", "critical"])].shape[0])
        text = f"Claude's status page logged {by_month[last]} incidents in {month_label(last)} ({severe} major or critical)"
        takeaway = [
            text + (f", against {by_month[prior]} in {month_label(prior)}." if prior in by_month.index else ".")
        ]
    return {
        "title": "Claude status incidents per month",
        "subtitle": "Incidents posted on status.claude.com, by the impact Anthropic assigned, complete months",
        "kind": "stacked_bar",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "incidents", "type": "quantitative", "label": "Incidents", "format": "int"},
            "color": {"field": "impact", "type": "nominal", "label": "Impact"},
        },
        "columns": [
            MONTH,
            {"field": "impact", "label": "Impact", "format": "text"},
            {"field": "incidents", "label": "Incidents", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            STATUS_NOTE,
            "Impact is Anthropic's own rating: minor, major or critical. Notices rated 'none' (maintenance, information) are left out.",
            "Growth in incidents partly reflects growth in usage and in how much Anthropic reports, not only in failures. Compare with hours below.",
            "Counted by the month the incident started. Complete months only.",
        ],
        "badges": [],
    }


@mart(id="product.incident_hours", sources=["status_incidents"])
def incident_hours(ctx):
    df = incidents(ctx, "anthropic")
    rows, takeaway = [], []
    if len(df):
        df = complete_months(df, last_collected(ctx))
        severe = df[df["impact"].isin(["major", "critical"])]
        done = severe[severe["minutes"].notna()].assign(minutes=lambda d: d["minutes"].astype(float))
        by = done.groupby("month").agg(hours=("minutes", lambda m: m.sum() / 60), incidents=("minutes", "size"))
        rows = [{"month": m, "hours": round(r.hours, 1), "incidents": int(r.incidents)} for m, r in by.iterrows()]
        if len(by):
            last = by.index.max()
            med = float(
                done[done["month"] >= (date.fromisoformat(last) - timedelta(days=62)).isoformat()]["minutes"].median()
            )
            takeaway = [
                (
                    f"Major or critical incidents ran {by.loc[last, 'hours']:.1f} hours in {month_label(last)}; "
                    f"the median one over the last three months took {med:.0f} minutes to resolve."
                )
            ]
    return {
        "title": "Hours of major and critical incidents",
        "subtitle": "Total time from first post to resolved, incidents rated major or critical, by month started",
        "kind": "bar",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "hours", "type": "quantitative", "label": "Hours", "format": "float"},
        },
        "columns": [
            MONTH,
            {"field": "hours", "label": "Hours", "format": "float"},
            {"field": "incidents", "label": "Incidents", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            STATUS_NOTE,
            "Hours = sum of (resolved time − first post time) for each incident. Incidents can overlap, so hours are not hours of downtime.",
            "Incidents without a resolved time are left out. A model-specific degradation counts the same as an outage.",
            "Complete months only.",
        ],
        "badges": ["arithmetic"],
    }


@mart(id="product.recent_incidents", sources=["status_incidents"])
def recent_incidents(ctx):
    df = incidents(ctx, "anthropic")
    rows = []
    if len(df):
        urls = dict(zip(df.index, df["source_url"], strict=True))
        top = df.sort_values("started", ascending=False).head(15)
        rows = [
            {
                "started": r.started[:16].replace("T", " ") + " UTC",
                "incident": r.name_,
                "impact": r.impact,
                "minutes": None if pd.isna(r.minutes) else int(r.minutes),
                "link": urls[r.Index],
            }
            for r in top.rename(columns={"name": "name_"}).itertuples()
        ]
    return {
        "title": "Latest Claude incidents",
        "subtitle": "The 15 most recent posts on status.claude.com",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "started", "label": "Started", "format": "text"},
            {"field": "incident", "label": "Incident", "format": "text"},
            {"field": "impact", "label": "Impact", "format": "text"},
            {"field": "minutes", "label": "Minutes to resolve", "format": "int"},
            {"field": "link", "label": "Link", "format": "url"},
        ],
        "rows": rows,
        "takeaway": [
            f"{sum(r['impact'] in ('major', 'critical') for r in rows)} of the latest {len(rows)} incidents were rated major or critical."
        ]
        if rows
        else [],
        "assumptions": [
            STATUS_NOTE,
            "Minutes run from first post to resolved; blank means unresolved or no time posted.",
        ],
        "badges": [],
    }


@mart(id="product.status_comparison", sources=["status_incidents"])
def status_comparison(ctx):
    rows, takeaway = [], []
    since = (date.fromisoformat(last_collected(ctx)) - timedelta(days=14)).isoformat()
    for entity in ("anthropic", "openai"):
        df = incidents(ctx, entity)
        if df.empty:
            continue
        recent = df[(df["as_of"] >= since) & df["impact"].isin(IMPACTS)]
        done = recent[recent["minutes"].notna()]
        rows.append(
            {
                "company": ctx.names.get(entity, entity),
                "incidents": len(recent),
                "severe": int(recent["impact"].isin(["major", "critical"]).sum()),
                "hours": round(float(done["minutes"].astype(float).sum()) / 60, 1),
            }
        )
    if len(rows) == 2:
        a, o = rows
        takeaway = [
            f"In the last 14 days Anthropic posted {a['incidents']} incidents ({a['severe']} major or critical) and OpenAI {o['incidents']} ({o['severe']})."
        ]
    return {
        "title": "Reliability against OpenAI, last 14 days",
        "subtitle": "Incidents each company posted on its own status page",
        "kind": "stat",
        "encoding": {"y": {"field": "incidents", "type": "quantitative", "label": "Incidents posted", "format": "int"}},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "incidents", "label": "Incidents", "format": "int"},
            {"field": "severe", "label": "Major or critical", "format": "int"},
            {"field": "hours", "label": "Hours (first post to resolved)", "format": "float"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            STATUS_NOTE,
            "OpenAI's status feed returns only its latest 25 incidents, so a longer comparison is not possible yet; its history builds from our daily collection.",
            "Google DeepMind, xAI, Mistral and Cohere publish no comparable status feed.",
            "Informational notices rated 'none' are left out.",
        ],
        "badges": ["arithmetic"],
    }


# ---- model releases ----
def releases(ctx) -> pd.DataFrame:
    df = dims(ctx.obs(metric="model_release"), "model", "tier")
    return df.sort_values(["as_of", "model"], ascending=[False, True]) if len(df) else df


@mart(id="product.model_releases", sources=["product_model_releases"])
def model_releases(ctx):
    df = releases(ctx)
    rows = [
        {"date": r.as_of, "model": r.model, "tier": r.tier, "quote": r.evidence, "link": r.source_url}
        for r in df.itertuples()
    ]
    takeaway = []
    if len(df):
        newest = df.iloc[0]
        year = (date.fromisoformat(newest.as_of) - timedelta(days=365)).isoformat()
        takeaway = [
            (
                f"Anthropic has announced {len(df)} models since {df['as_of'].min()[:4]}; the latest is {newest.model} on {newest.as_of}, "
                f"and {int((df['as_of'] > year).sum())} of them came in the 12 months before."
            )
        ]
    return {
        "title": "Anthropic model releases",
        "subtitle": "Every model announcement, dated by Anthropic's own page, with the sentence quoted",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "tier", "label": "Tier", "format": "text"},
            {"field": "quote", "label": "What Anthropic said", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "One row per model. Models announced together (Claude 3, Opus 4 and Sonnet 4, Fable and Mythos) share a date and a quote.",
            "Mythos models are limited to Project Glasswing participants; they are counted but not open to all customers.",
            "The Claude 3.5 Sonnet page is dated 21 Jun 2024 (UTC); it was announced the evening of 20 Jun in the US.",
            "Hand-maintained ledger: each row was checked to quote its page word for word. Add a row when a model ships.",
        ],
        "badges": [],
    }


@mart(id="product.release_cadence", sources=["product_model_releases"])
def release_cadence(ctx):
    df = releases(ctx)
    rows, takeaway = [], []
    if len(df):
        q = pd.to_datetime(df["as_of"]).dt.to_period("Q")
        counts = q.value_counts().sort_index()
        full = pd.period_range(counts.index.min(), counts.index.max(), freq="Q")
        rows = [{"quarter": p.start_time.date().isoformat(), "models": int(counts.get(p, 0))} for p in full]
        newest = date.fromisoformat(df["as_of"].max())
        last12 = int((df["as_of"] > (newest - timedelta(days=365)).isoformat()).sum())
        prev12 = int(
            (
                (df["as_of"] <= (newest - timedelta(days=365)).isoformat())
                & (df["as_of"] > (newest - timedelta(days=730)).isoformat())
            ).sum()
        )
        takeaway = [
            f"Anthropic announced {last12} models in the 12 months to {newest}, against {prev12} in the 12 months before."
        ]
    return {
        "title": "Models announced per quarter",
        "subtitle": "Count of models in the release ledger, by quarter announced",
        "kind": "bar",
        "encoding": {
            "x": {"field": "quarter", "type": "temporal", "label": "Quarter"},
            "y": {"field": "models", "type": "quantitative", "label": "Models announced", "format": "int"},
        },
        "columns": [
            {"field": "quarter", "label": "Quarter starting", "format": "date"},
            {"field": "models", "label": "Models announced", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Counts each model once: a family announced together counts one per model, and Mythos models count too.",
            "Measures how often Anthropic ships, not how big each release is.",
        ],
        "badges": ["arithmetic"],
    }


# ---- prices ----
def tier_of(model: str) -> str:
    return next((t for t in ("Opus", "Sonnet", "Haiku", "Fable", "Mythos") if t in model), "Other")


@mart(id="product.api_prices", sources=["product_model_releases", "product_api_prices"])
def api_prices(ctx):
    rel = (
        releases(ctx)[["model", "as_of"]].rename(columns={"as_of": "released"})
        if len(releases(ctx))
        else pd.DataFrame(columns=["model", "released"])
    )
    px = dims(ctx.obs(metric=["api_price_input_usd_mtok", "api_price_output_usd_mtok"]), "model", "basis")
    rows, takeaway = [], []
    if len(px) and len(rel):
        wide = (
            px.pivot_table(index=["model", "basis"], columns="metric", values="value")
            .reset_index()
            .merge(rel, on="model")
        )
        wide = (
            wide[wide["model"].map(tier_of) != "Mythos"]
            .assign(tier=lambda d: d["model"].map(tier_of))
            .sort_values(["released", "model"])
        )
        rows = [
            {
                "released": r.released,
                "tier": r.tier,
                "model": r.model,
                "input": float(r.api_price_input_usd_mtok),
                "output": float(r.api_price_output_usd_mtok),
                "basis": r.basis,
            }
            for r in wide.itertuples()
        ]
        opus = wide[wide["tier"] == "Opus"].sort_values("released")
        if len(opus) > 1:
            first, last = opus.iloc[0], opus.iloc[-1]
            takeaway = [
                (
                    f"Opus input pricing went from ${num(first.api_price_input_usd_mtok)} per million tokens ({first.model}) to "
                    f"${num(last.api_price_input_usd_mtok)} ({last.model})."
                )
            ]
    return {
        "title": "API price per model, at release",
        "subtitle": "Input price in USD per million tokens, by the date each model was released",
        "kind": "line",
        "encoding": {
            "x": {"field": "released", "type": "temporal", "label": "Model released"},
            "y": {"field": "input", "type": "quantitative", "label": "Input USD per million tokens", "format": "float"},
            "color": {"field": "tier", "type": "nominal", "label": "Tier"},
        },
        "columns": [
            {"field": "released", "label": "Released", "format": "date"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "tier", "label": "Tier", "format": "text"},
            {"field": "input", "label": "Input USD / MTok", "format": "float"},
            {"field": "output", "label": "Output USD / MTok", "format": "float"},
            {"field": "basis", "label": "Price basis", "format": "text"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Models still on the pricing page show today's list price; older models show the price in their launch announcement. The basis is in the table.",
            "One model's price can change after release. Claude 3.5 Haiku was repriced from its launch price to $0.80 / $4.",
            "List prices only: batch, prompt-caching and long-context rates differ. Mythos models are limited-access and not charted.",
            "Newer tokenizers (Claude 4.7 and later) produce about 30% more tokens for the same text, so a lower price per token is not always a lower price per task.",
        ],
        "badges": [],
    }


@mart(id="product.plans", sources=["product_plan_prices"])
def plans(ctx):
    df = dims(ctx.obs(metric="plan_price_usd_month"), "plan", "billing")
    rows = [
        {"plan": r.plan, "price": float(r.value), "billing": r.billing, "quote": r.evidence, "link": r.source_url}
        for r in df.itertuples()
    ]
    takeaway = []
    pro = next((r for r in rows if r["plan"] == "Pro"), None)
    mx = next((r for r in rows if r["plan"] == "Max"), None)
    if pro and mx:
        takeaway = [
            f"Pro is ${pro['price']:.0f} a month on annual billing and Max starts at ${mx['price']:.0f}, {mx['price'] / pro['price']:.1f}× Pro."
        ]
    return {
        "title": "Claude plans today",
        "subtitle": "Published price per month, with the page text quoted",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "plan", "label": "Plan", "format": "text"},
            {"field": "price", "label": "USD per month", "format": "float"},
            {"field": "billing", "label": "Billing", "format": "text"},
            {"field": "quote", "label": "What the page says", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "As published on claude.com/pricing on 2026-10-05. Annual-billing prices are shown; monthly-billing prices are not read from the page.",
            "Max is 'from' $100: it comes in 5× and 20× usage tiers. Enterprise is $20 per seat plus usage billed at API rates.",
            "Plan prices were not collected before today, so there is no plan-price history yet.",
        ],
        "badges": [],
    }
