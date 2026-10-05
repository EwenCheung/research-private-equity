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


@mart(id="dev_adoption.python_sdk_monthly", sources=SDK_SOURCES)
def python_sdk_monthly(ctx):
    df = monthly(ctx, "pypi_downloads", "sdk")
    return line(
        "Python SDK downloads per month",
        "Downloads",
        "downloads",
        company_rows(ctx, df, "downloads"),
        lead_takeaway(ctx, df, "Python SDK"),
        DOWNLOADS
        + spike_note(ctx, df)
        + [
            "Google is google-genai plus the legacy google-generativeai; xAI is xai-sdk.",
            "The running month is left out until it completes.",
        ],
        subtitle="Official API SDKs on PyPI, complete months",
    )


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
        "title": "Share of Python SDK downloads",
        "subtitle": "Each company's share of downloads across the six tracked SDKs, last 18 complete months",
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
        "JavaScript SDK downloads per month",
        "Downloads",
        "downloads",
        company_rows(ctx, df, "downloads"),
        lead_takeaway(ctx, df, "JavaScript SDK"),
        DOWNLOADS
        + spike_note(ctx, df)
        + ["Google is @google/genai plus the legacy @google/generative-ai. xAI publishes no npm SDK."],
        subtitle="Official API SDKs on npm, complete months",
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
        "Coding-agent CLI downloads per month",
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
        subtitle="Claude Code vs OpenAI Codex CLI vs Gemini CLI on npm, complete months",
    )
    return spec


@mart(id="dev_adoption.coauthored_commits", sources=["github_coauthored_commits"])
def coauthored_commits(ctx):
    df = latest(ctx.obs(metric="coauthored_commits", entity="anthropic"), keys=("entity", "as_of"))
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
        "title": "Public GitHub commits co-authored by Claude",
        "subtitle": "Weekly commit-search count of the Claude Code co-author trailer",
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
            "Only public repositories' default branches are indexed; private and enterprise work is invisible.",
            "The trailer is added by Claude Code by default and can be switched off, so this undercounts usage.",
            "Other coding agents add no comparable trailer, so there is no peer series.",
        ],
        "badges": ["arithmetic"],
    }


def snapshot_bar(ctx, metric: str, title: str, label: str, assumptions: list[str], subtitle: str) -> dict:
    """Latest snapshot per company as bars, with the change since our first snapshot once there is one."""
    df = latest(ctx.obs(metric=metric), keys=("entity", "as_of")).sort_values("as_of")
    rows = []
    for entity, g in df.groupby("entity"):
        first, last = g.iloc[0], g.iloc[-1]
        rows.append(
            {
                "company": ctx.names.get(entity, entity),
                "value": int(last.value),
                "since": first.as_of,
                "growth": round(last.value / first.value - 1, 4) if first.value and last.as_of != first.as_of else None,
            }
        )
    rows.sort(key=lambda r: -r["value"])
    takeaway = []
    if rows:
        lead = rows[0]
        takeaway = [f"{lead['company']} leads with {num(lead['value'])} {label.lower()}."]
        a = next((r for r in rows if r["company"] == ctx.names.get("anthropic")), None)
        if a and a is not lead:
            takeaway = [
                f"Anthropic has {num(a['value'])} {label.lower()}; {lead['company']} leads with {num(lead['value'])}."
            ]
        elif a and len(rows) > 1:
            takeaway = [
                f"Anthropic leads with {num(a['value'])} {label.lower()}, {a['value'] / rows[1]['value']:.1f}× {rows[1]['company']}."
            ]
    return {
        "title": title,
        "subtitle": subtitle,
        "kind": "bar",
        "encoding": {
            "x": {"field": "company", "type": "nominal", "label": "Company"},
            "y": {"field": "value", "type": "quantitative", "label": label, "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "value", "label": label, "format": "int"},
            {"field": "growth", "label": "Change since first snapshot", "format": "pct"},
            {"field": "since", "label": "First snapshot", "format": "date"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": assumptions,
        "badges": [],
    }


@mart(id="dev_adoption.vscode_installs", sources=["vscode_installs"])
def vscode(ctx):
    return snapshot_bar(
        ctx,
        "vscode_installs",
        "VS Code extension installs",
        "Installs",
        [
            "Cumulative marketplace installs; uninstalls are not subtracted.",
            "Extensions: Claude Code (anthropic.claude-code), ChatGPT/Codex (openai.chatgpt), Gemini Code Assist.",
            "The marketplace publishes no history; growth is measured from our own daily snapshots.",
        ],
        "Cumulative installs of each company's coding extension, latest snapshot",
    )


@mart(id="dev_adoption.github_stars", sources=["github_org_stats"])
def github_stars(ctx):
    return snapshot_bar(
        ctx,
        "org_stars",
        "GitHub stars across each company's public repos",
        "Stars",
        [
            "Sum of stars on the organisation's public, non-fork repositories on the day collected.",
            "Orgs: anthropics, openai, google-gemini, xai-org, mistralai, cohere-ai.",
            "Stars are cumulative interest, not usage; one viral repo can dominate a total.",
        ],
        "Stars on public, non-fork repositories, latest snapshot",
    )


@mart(id="dev_adoption.mcp_sdk_downloads", sources=["pypi_downloads", "npm_downloads"])
def mcp_sdk_downloads(ctx):
    frames_ = []
    for metric, name in (
        ("pypi_downloads", "Python (mcp)"),
        ("npm_downloads", "TypeScript (@modelcontextprotocol/sdk)"),
    ):
        m = monthly(ctx, metric, "mcp")
        frames_.append(m.assign(series=name))
    df = pd.concat(frames_) if frames_ else pd.DataFrame()
    df = df[df["month"] >= "2024-11-01"] if len(df) else df
    rows = [
        {"month": r.month, "series": r.series, "downloads": int(r.value)}
        for r in df.sort_values(["month", "series"]).itertuples()
    ]
    takeaway = []
    if len(df):
        tot = df.groupby("month")["value"].sum()
        if len(tot) > 3:
            takeaway = [
                (
                    f"Model Context Protocol SDKs were downloaded {num(tot.iloc[-1])} times in {month_label(tot.index[-1])}, "
                    f"{change(tot.iloc[-1], tot.iloc[-4])} on {month_label(tot.index[-4])}."
                )
            ]
    return {
        "title": "Model Context Protocol SDK downloads",
        "subtitle": "MCP is Anthropic's open protocol for connecting models to tools; complete months since launch",
        "kind": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "downloads", "type": "quantitative", "label": "Downloads", "format": "int"},
            "color": {"field": "series", "type": "nominal", "label": "SDK"},
        },
        "columns": [
            CHART_DATE,
            {"field": "series", "label": "SDK", "format": "text"},
            {"field": "downloads", "label": "Downloads", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": DOWNLOADS
        + ["MCP is used by every major AI vendor now, so this measures the protocol's reach, not Claude usage alone."],
        "badges": [],
    }


@mart(id="dev_adoption.mcp_server_repos", sources=["github_mcp_server_repos"])
def mcp_server_repos(ctx):
    df = dims(latest(ctx.obs(metric="mcp_server_repos_new"), keys=("entity", "as_of")), "partial_month").sort_values(
        "as_of"
    )
    rows = [
        {"month": r.as_of, "repos": int(r.value), "partial": "yes" if r.partial_month else ""} for r in df.itertuples()
    ]
    full = df[~df["partial_month"].astype(bool)]
    takeaway = []
    if len(full) > 3:
        takeaway = [
            (
                f"{num(full['value'].iloc[-1])} new mcp-server repos were created in {month_label(full['as_of'].iloc[-1])}; "
                f"{num(full['value'].sum())} since MCP launched."
            )
        ]
    return {
        "title": "New MCP-server repositories per month",
        "subtitle": "Public GitHub repos tagged mcp-server, by creation month",
        "kind": "bar",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "repos", "type": "quantitative", "label": "New repos", "format": "int"},
        },
        "columns": [
            CHART_DATE,
            {"field": "repos", "label": "New repos", "format": "int"},
            {"field": "partial", "label": "Month still running", "format": "text"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Only repos that add the mcp-server topic are counted, so this is a lower bound.",
            "The running month is shown but partial; it is re-counted each run.",
        ],
        "badges": [],
    }
