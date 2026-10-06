"""Product, pricing and reliability charts. Every number is computed from collected incidents or from cited ledger rows."""

import calendar
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, pct

IMPACTS = ["minor", "major", "critical"]
MONTH = {"field": "month", "label": "Month", "format": "date"}
STATUS_NOTE = "Incidents are the ones the company chose to post, with its own impact rating, so a company that posts more freely looks worse."
PEER_ORDER = ["anthropic", "openai", "google-deepmind", "xai", "mistral", "cohere"]


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


def peer_name(ctx, entity: str) -> str:
    return ctx.names.get(entity, entity)


# ---- usage ----


@mart(id="product.openrouter_share", sources=["openrouter_rankings"])
def openrouter_share(ctx):
    df = dims(
        ctx.obs(metric=["openrouter_request_share", "openrouter_request_change"]),
        "author",
        "rank",
        "window",
    )
    rows = []
    if len(df):
        df = latest(df, keys=("entity", "metric"))
        wide = df.pivot_table(
            index=["entity", "author", "rank", "window", "source_url"], columns="metric", values="value", aggfunc="last"
        ).reset_index()
        rows = [
            {
                "company": peer_name(ctx, r.entity),
                "author": r.author,
                "rank": int(r.rank),
                "share": float(r.openrouter_request_share),
                "change": float(r.openrouter_request_change),
                "link": r.source_url,
            }
            for r in wide.sort_values("rank").itertuples()
        ]
    takeaway = []
    if rows:
        leader = max(rows, key=lambda r: r["share"])
        anthropic = next((r for r in rows if r["company"] == "Anthropic"), None)
        takeaway = [
            f"{leader['company']} has {pct(leader['share'], 1)} of requests in OpenRouter's public author table."
        ]
        if anthropic:
            takeaway.append(
                f"Anthropic is rank {anthropic['rank']} at {pct(anthropic['share'], 1)} in that same table."
            )
    return {
        "title": "OpenRouter request share by model author",
        "subtitle": "Trailing seven days among authors shown in OpenRouter's public request-share ranking",
        "kind": "bar",
        "encoding": {
            "x": {"field": "company", "type": "nominal", "label": "Company"},
            "y": {"field": "share", "type": "quantitative", "label": "Share of requests", "format": "pct"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "author", "label": "OpenRouter author", "format": "text"},
            {"field": "rank", "label": "Rank", "format": "int"},
            {"field": "share", "label": "Request share", "format": "pct"},
            {"field": "change", "label": "Request change", "format": "pct"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "This is OpenRouter request share, not total market share, users, tokens or revenue. It covers only traffic visible to OpenRouter.",
            "The public table shows leading authors only. xAI and Cohere are absent, which means unknown/below the display cutoff—not zero.",
            "Request counts give a tiny prompt the same weight as a large generation; private requests may be excluded.",
        ],
        "badges": [],
    }


INDEXES = ["openrouter_aa_intelligence_index", "openrouter_aa_coding_index", "openrouter_aa_agentic_index"]
EVAL_LABELS = {"gpqa_diamond": "GPQA Diamond", "tau_bench_verified_airline": "tau-bench airline"}


def cell(x):
    return None if pd.isna(x) else float(x)


def missing_companies(ctx, present) -> str:
    return ", ".join(peer_name(ctx, e) for e in PEER_ORDER if e not in present) or "none"


@mart(id="product.openrouter_indexes", sources=["openrouter_benchmarks"])
def openrouter_indexes(ctx):
    df = dims(ctx.obs(metric=INDEXES), "model")
    rows, takeaway, present = [], [], set()
    if len(df):
        df = latest(df, keys=("entity", "metric", "model"))
        wide = df.pivot_table(
            index=["entity", "model", "as_of", "source_url"], columns="metric", values="value", aggfunc="last"
        ).reset_index()
        wide = wide.dropna(subset=[INDEXES[0]]).sort_values(INDEXES[0], ascending=False).drop_duplicates("entity")
        present = set(wide["entity"])
        rows = [
            {
                "company": peer_name(ctx, r["entity"]),
                "model": r["model"],
                "intelligence": cell(r[INDEXES[0]]),
                "coding": cell(r.get(INDEXES[1])),
                "agentic": cell(r.get(INDEXES[2])),
                "date": r["as_of"],
                "link": r["source_url"],
            }
            for r in wide.to_dict("records")
        ]
    if rows:
        top = rows[0]
        takeaway = [
            f"{top['company']}'s {top['model']} has the highest Intelligence Index here, {top['intelligence']:.1f}."
        ]
        mine = next((r for r in rows if r["company"] == "Anthropic"), None)
        if mine and mine is not top:
            gap = top["intelligence"] - mine["intelligence"]
            takeaway.append(
                f"Anthropic's best, {mine['model']}, scores {mine['intelligence']:.1f}: {gap:.1f} points behind."
            )
    return {
        "title": "Intelligence Index of each company's best model",
        "subtitle": "Artificial Analysis indexes as relayed by OpenRouter's benchmarks API; the highest-scoring model per company",
        "kind": "bar",
        "encoding": {
            "x": {"field": "company", "type": "nominal", "label": "Company"},
            "y": {"field": "intelligence", "type": "quantitative", "label": "Intelligence Index", "format": "float"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "intelligence", "label": "Intelligence", "format": "float"},
            {"field": "coding", "label": "Coding", "format": "float"},
            {"field": "agentic", "label": "Agentic", "format": "float"},
            {"field": "date", "label": "Feed date", "format": "date"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Source: Artificial Analysis (artificialanalysis.ai) via OpenRouter (openrouter.ai/rankings). We relay the numbers unchanged.",
            "Selection rule: the model with the highest Intelligence Index in the feed for each company; its Coding and Agentic indexes are shown for that same model. A blank is a score the feed does not publish, not zero.",
        ]
        + (
            [
                f"Companies with no model in the feed: {missing_companies(ctx, present)}. Absent means not listed on OpenRouter, or a provider prefix in config/identifiers/product.yaml that does not match."
            ]
            if rows
            else []
        ),
        "badges": [],
    }


@mart(id="product.openrouter_evals", sources=["openrouter_benchmarks"])
def openrouter_evals(ctx):
    df = dims(ctx.obs(metric="openrouter_eval_accuracy"), "model", "benchmark", "stddev", "tasks", "cost_per_task_usd")
    rows, takeaway = [], []
    if len(df):
        df = latest(df, keys=("entity", "benchmark", "model"))
        best = df.sort_values("value", ascending=False).drop_duplicates(["entity", "benchmark"])
        rows = [
            {
                "benchmark": EVAL_LABELS.get(r["benchmark"], r["benchmark"]),
                "company": peer_name(ctx, r["entity"]),
                "model": r["model"].split(": ", 1)[
                    -1
                ],  # the feed prefixes this one with the provider ("Google: Gemini ...")
                "accuracy": float(r["value"]),
                "stddev": cell(r["stddev"]),
                "tasks": None if pd.isna(r["tasks"]) else int(r["tasks"]),
                "cost": cell(r["cost_per_task_usd"]),
                "date": r["as_of"],
                "link": r["source_url"],
            }
            for r in best.sort_values(["benchmark", "value"], ascending=[True, False]).to_dict("records")
        ]
    for name in dict.fromkeys(r["benchmark"] for r in rows):
        group = [r for r in rows if r["benchmark"] == name]
        top = group[0]
        text = f"{name}: {top['company']}'s {top['model']} leads at {pct(top['accuracy'], 0)} accuracy."
        mine = next((r for r in group if r["company"] == "Anthropic"), None)
        if mine and mine is not top:
            text += f" Anthropic's best, {mine['model']}, scores {pct(mine['accuracy'], 0)}."
        takeaway.append(text)
    return {
        "title": "OpenRouter's own evaluations",
        "subtitle": "Accuracy and cost per task on tasks OpenRouter runs itself; the highest-accuracy model per company and benchmark",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "benchmark", "label": "Benchmark", "format": "text"},
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "accuracy", "label": "Accuracy", "format": "pct"},
            {"field": "stddev", "label": "± std dev", "format": "pct"},
            {"field": "tasks", "label": "Tasks", "format": "int"},
            {"field": "cost", "label": "USD per task", "format": "float"},
            {"field": "date", "label": "Last run", "format": "date"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "OpenRouter runs these evaluations itself (GPQA Diamond and tau-bench verified airline); the feed publishes accuracy, its standard deviation, the task count and average cost per task.",
            "Selection rule: the highest-accuracy model per company and benchmark. A company's best model on one benchmark need not be its best on another. A model with few tasks or no standard deviation is a noisier reading, so check the Tasks column before ranking.",
            "Models are matched to companies by OpenRouter's provider prefix; a company absent from a benchmark was not evaluated or does not match a prefix in config/identifiers/product.yaml.",
            "Cost per task is an average over the tasks run and depends on how long each model reasons; it is not a list price.",
        ],
        "badges": [],
    }


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
