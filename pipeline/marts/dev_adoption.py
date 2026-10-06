"""Developer adoption charts. Every number comes from the dev_adoption sources; every takeaway is computed from the rows."""

import calendar
from datetime import date

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import change, dims, latest, num, pct, roll

DOWNLOADS = [
    "Downloads count every install, including CI and mirrors: read them as relative pull, not users.",
    (
        "Installs pulled in as a dependency of another package count too, so an SDK bundled by popular agent "
        "frameworks (google-genai is one) reads higher than its direct use."
    ),
]
CHART_DATE = {"field": "month", "label": "Month", "format": "date"}
SPIKE = 10  # a day above 10x its package's trailing 28-day median is a bulk-download anomaly, not adoption


def month_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{calendar.month_abbr[d.month]} {d.year}"


def flag_spikes(df: pd.DataFrame) -> pd.DataFrame:
    """Mark transient spikes: days above SPIKE x the package's median over both the 28 days before and the 28 after.

    A bulk-download loop or broken auto-updater jumps and falls back; a launch or real growth stays up, so needing
    both sides keeps genuine ramps (e.g. the Claude API launch in March 2023) in the series.
    """
    df = df.sort_values("as_of")
    before = df.groupby("package")["value"].transform(lambda v: v.shift(1).rolling(28, min_periods=7).median())
    after = df.groupby("package")["value"].transform(
        lambda v: v[::-1].shift(1).rolling(28, min_periods=7).median()[::-1]
    )
    return df.assign(spike=(before > 0) & (after > 0) & (df["value"] > SPIKE * before) & (df["value"] > SPIKE * after))


def monthly(ctx, metric: str, role: str) -> pd.DataFrame:
    """Monthly totals per company for one package role, complete months only.

    value excludes spike days; raw keeps them; spike_days counts what was excluded, so nothing is hidden.
    """
    df = dims(ctx.obs(metric=metric), "package", "role")
    df = latest(df[df["role"] == role], keys=("entity", "package", "as_of"))
    if df.empty:
        return pd.DataFrame(columns=["entity", "month", "value", "raw", "spike_days"])
    df = flag_spikes(df)
    last_day = date.fromisoformat(df["as_of"].max())
    clean = roll(df[~df["spike"]], "month").rename(columns={"as_of": "month"})
    raw = roll(df, "month").rename(columns={"as_of": "month", "value": "raw"})
    spikes = roll(df.assign(value=df["spike"].astype(int)), "month").rename(
        columns={"as_of": "month", "value": "spike_days"}
    )
    out = (
        raw.merge(spikes, on=["entity", "month"]).merge(clean, on=["entity", "month"], how="left").fillna({"value": 0})
    )
    if last_day.day != calendar.monthrange(last_day.year, last_day.month)[1]:  # the newest month is still running
        out = out[out["month"] < last_day.replace(day=1).isoformat()]
    return out


def spike_note(ctx, df: pd.DataFrame) -> list[str]:
    """Assumption lines naming every company-month where spike days were excluded."""
    hit = df[df["spike_days"] > 0]
    if hit.empty:
        return [f"No day exceeded {SPIKE}× its surrounding 28-day medians, so nothing was excluded."]
    cases = "; ".join(
        f"{ctx.names.get(r.entity, r.entity)} {month_label(r.month)} ({int(r.spike_days)} days, raw {num(r.raw)})"
        for r in hit.itertuples()
    )
    return [
        (
            f"Days above {SPIKE}× a package's median of both the 28 days before and after are transient bulk-download "
            f"anomalies and are excluded from the chart: {cases}. Raw totals stay in the table."
        )
    ]


def by_volume(df: pd.DataFrame, value="value") -> list[str]:
    """Companies ordered by their latest value, so the legend reads biggest first."""
    last = df[df["month"] == df["month"].max()]
    return last.sort_values(value, ascending=False)["entity"].tolist()


def company_rows(ctx, df: pd.DataFrame, value_col: str) -> list[dict]:
    order = {e: i for i, e in enumerate(by_volume(df))}
    df = df.assign(rank=df["entity"].map(order).fillna(99)).sort_values(["month", "rank"])
    return [
        {
            "month": r.month,
            "company": ctx.names.get(r.entity, r.entity),
            value_col: int(r.value),
            "raw": int(r.raw),
            "spike_days": int(r.spike_days),
        }
        for r in df.itertuples()
    ]


SPIKE_COLUMNS = [
    {"field": "raw", "label": "Raw (incl. spike days)", "format": "int"},
    {"field": "spike_days", "label": "Spike days excluded", "format": "int"},
]


def line(title: str, y_label: str, value_col: str, rows: list[dict], takeaway, assumptions, subtitle="") -> dict:
    return {
        "title": title,
        "subtitle": subtitle,
        "kind": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": value_col, "type": "quantitative", "label": y_label, "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            CHART_DATE,
            {"field": "company", "label": "Company", "format": "text"},
            {"field": value_col, "label": y_label, "format": "int"},
        ]
        + (SPIKE_COLUMNS if rows and "raw" in rows[0] else []),
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": assumptions,
        "badges": [],
    }


def lead_takeaway(ctx, df: pd.DataFrame, what: str) -> list[str]:
    """'In Sep 2026 Anthropic's <what> had 147M downloads (+12% on Aug), vs OpenAI 294M.' computed from the rows."""
    if df.empty:
        return []
    months = sorted(df["month"].unique())
    m = months[-1]
    now = df[df["month"] == m].set_index("entity")["value"]
    prev = df[df["month"] == months[-2]].set_index("entity")["value"] if len(months) > 1 else pd.Series(dtype=float)
    lead = now.idxmax()
    first = f"In {month_label(m)}, {what} downloads: "
    if "anthropic" in now:
        a = now["anthropic"]
        first += f"Anthropic {num(a)}" + (
            f" ({change(a, prev['anthropic'])} on {month_label(months[-2])})" if "anthropic" in prev else ""
        )
        if lead != "anthropic":
            first += f", against {ctx.names.get(lead, lead)} at {num(now[lead])} ({now[lead] / a:.1f}× Anthropic)."
        else:
            first += ", the most of the tracked companies."
    else:
        first += f"{ctx.names.get(lead, lead)} leads with {num(now[lead])}."
    return [first]


SDK_SOURCES = ["pypi_downloads"]


@mart(id="dev_adoption.python_sdk_share", sources=SDK_SOURCES)
def python_sdk_share(ctx):
    df = monthly(ctx, "pypi_downloads", "sdk")
    df = (
        df[df["month"] >= (pd.Timestamp(df["month"].max()) - pd.DateOffset(months=17)).date().isoformat()]
        if len(df)
        else df
    )
    totals = df.groupby("month")["value"].transform("sum")
    df = df.assign(value=df["value"] / totals)
    order = {e: i for i, e in enumerate(by_volume(df))}
    df = df.assign(rank=df["entity"].map(order).fillna(99)).sort_values(["month", "rank"])
    rows = [
        {"month": r.month, "company": ctx.names.get(r.entity, r.entity), "share": round(r.value, 4)}
        for r in df.itertuples()
    ]
    takeaway = []
    if rows:
        share = df.pivot(index="month", columns="entity", values="value")
        last, first = share.index[-1], share.index[max(0, len(share) - 13)]
        a = share["anthropic"]
        takeaway = [
            (
                f"Anthropic took {pct(a[last])} of tracked Python SDK downloads in {month_label(last)}, "
                f"against {pct(a[first])} in {month_label(first)}."
            )
        ]
    return {
        "title": "Each company's share of Python SDK downloads",
        "subtitle": "Share of downloads across the six companies' official Python SDKs on PyPI, last 18 complete months",
        "kind": "stacked_bar",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "share", "type": "quantitative", "label": "Share of downloads", "format": "pct"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            CHART_DATE,
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "share", "label": "Share", "format": "pct"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Share = a company's SDK downloads ÷ the total across the six tracked companies' SDKs that month.",
            "Untracked SDKs and frameworks that wrap these APIs (LangChain, LiteLLM) are not in the denominator.",
        ]
        + DOWNLOADS,
        "badges": ["arithmetic"],
    }


@mart(id="dev_adoption.js_sdk_monthly", sources=["npm_downloads"])
def js_sdk_monthly(ctx):
    df = monthly(ctx, "npm_downloads", "sdk")
    return line(
        "Downloads of each company's JavaScript SDK per month",
        "Downloads",
        "downloads",
        company_rows(ctx, df, "downloads"),
        lead_takeaway(ctx, df, "JavaScript SDK"),
        DOWNLOADS
        + spike_note(ctx, df)
        + ["Google is @google/genai plus the legacy @google/generative-ai. xAI publishes no npm SDK."],
        subtitle="Official API software kits on npm, complete months",
    )


@mart(id="dev_adoption.coding_agent_cli", sources=["npm_downloads"])
def coding_agent_cli(ctx):
    df = monthly(ctx, "npm_downloads", "cli")
    products = {
        r["entity"]: r["dims"]["package"]
        for _, r in ctx.obs(metric="npm_downloads").iterrows()
        if r["dims"].get("role") == "cli"
    }
    spec = line(
        "Coding-tool downloads per month: Claude Code, Codex, Gemini CLI",
        "Downloads",
        "downloads",
        company_rows(ctx, df, "downloads"),
        lead_takeaway(ctx, df, "coding-agent CLI"),
        DOWNLOADS
        + spike_note(ctx, df)
        + [
            "Products: " + ", ".join(f"{ctx.names.get(e, e)} = {p}" for e, p in sorted(products.items())) + ".",
            "CLIs update often, and each update is a download, so release cadence inflates counts.",
        ],
        subtitle="Downloads of each company's coding-agent command-line tool on npm, complete months",
    )
    return spec


@mart(id="dev_adoption.coauthored_commits", sources=["github_coauthored_commits"])
def coauthored_commits(ctx):
    df = dims(latest(ctx.obs(metric="coauthored_commits", entity="anthropic"), keys=("entity", "as_of")), "incomplete")
    flagged = int(df["incomplete"].astype(bool).sum()) if len(df) else 0
    df = df.sort_values("as_of").assign(avg=lambda d: d["value"].rolling(4, min_periods=4).mean())
    rows = [{"week": r.as_of, "series": "Weekly count", "commits": int(r.value)} for r in df.itertuples()] + [
        {"week": r.as_of, "series": "4-week average", "commits": int(r.avg)} for r in df.itertuples() if pd.notna(r.avg)
    ]
    takeaway = []
    avg = df.dropna(subset=["avg"])
    if len(avg) > 12:
        now, before = avg.iloc[-1], avg.iloc[-13]
        takeaway = [
            (
                f"About {num(now.avg)} public commits a week carried Claude's co-author trailer in the 4 weeks to "
                f"{now.as_of}, {change(now.avg, before.avg)} on the same span 12 weeks earlier."
            )
        ]
    return {
        "title": "Public GitHub commits written with Claude Code, per week",
        "subtitle": "Weekly count of public commits carrying Claude Code's co-author line",
        "kind": "line",
        "encoding": {
            "x": {"field": "week", "type": "temporal", "label": "Week"},
            "y": {"field": "commits", "type": "quantitative", "label": "Commits", "format": "int"},
            "color": {"field": "series", "type": "nominal", "label": "Series"},
        },
        "columns": [
            {"field": "week", "label": "Week starting", "format": "date"},
            {"field": "series", "label": "Series", "format": "text"},
            {"field": "commits", "label": "Commits", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "GitHub's search count is an estimate. The same week has read 8.0M one day and 9.2M the next, so read the trend.",
            f"GitHub flagged {flagged} of the {len(df)} weeks as incomplete results (its search timed out), so those weeks may read low.",
            "Only public repositories' default branches are indexed; private and enterprise work is invisible.",
            "The trailer is added by Claude Code by default and can be switched off, so this undercounts usage.",
            "Other coding agents add no comparable trailer, so there is no peer series.",
        ],
        "badges": ["arithmetic"],
    }
