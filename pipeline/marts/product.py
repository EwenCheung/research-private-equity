"""Product, pricing and reliability charts. Every number is computed from collected incidents or from cited ledger rows."""

import calendar
import re
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, num, pct

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


def arena_models(ctx) -> pd.DataFrame:
    """One current row per Arena model, with score, price and context in columns."""
    metrics = [
        "arena_text_score",
        "arena_text_votes",
        "api_price_input_usd_mtok",
        "api_price_output_usd_mtok",
        "context_window_tokens",
    ]
    df = dims(
        ctx.obs(source="arena_text_leaderboard", metric=metrics),
        "model",
        "rank",
        "score_margin",
        "preliminary",
    )
    if df.empty:
        return df
    df = latest(df, keys=("entity", "metric", "model"))
    return (
        df.pivot_table(
            index=["entity", "model", "rank", "score_margin", "preliminary", "source_url", "as_of"],
            columns="metric",
            values="value",
            aggfunc="last",
        )
        .reset_index()
        .sort_values(["rank", "model"])
    )


def model_key(value: object) -> str:
    """Comparable key for display names such as `GPT-6 Astra (max)` and `gpt-6-astra-max`."""
    text = re.sub(r"\bwith fallback\b", "", str(value).lower())
    text = re.sub(r"\beffort\b", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def artificial_analysis_models(ctx) -> pd.DataFrame:
    """One current row per Artificial Analysis model, with benchmark measures in columns."""
    metrics = [
        "artificial_analysis_intelligence_index",
        "artificial_analysis_cost_per_task_usd",
        "artificial_analysis_output_tokens_s",
        "artificial_analysis_first_chunk_seconds",
        "artificial_analysis_total_response_seconds",
        "context_window_tokens",
    ]
    df = dims(
        ctx.obs(source="artificial_analysis_leaderboard", metric=metrics),
        "model",
        "creator",
        "arena_model",
        "independently_evaluated",
    )
    if df.empty:
        return df
    df = latest(df, keys=("entity", "metric", "model"))
    df["comparison_model"] = [
        model_key(arena_model if isinstance(arena_model, str) and arena_model else model)
        for arena_model, model in zip(df["arena_model"], df["model"], strict=True)
    ]
    df["independently_evaluated"] = df["independently_evaluated"].fillna(False).astype(bool)
    return (
        df.pivot_table(
            index=[
                "entity",
                "model",
                "comparison_model",
                "creator",
                "independently_evaluated",
                "source_url",
                "as_of",
            ],
            columns="metric",
            values="value",
            aggfunc="last",
        )
        .reset_index()
        .sort_values(["entity", "model"])
    )


def livebench_models(ctx) -> pd.DataFrame:
    """One current row per LiveBench model, with overall, category and cost measures in columns."""
    metrics = [
        "livebench_overall_score",
        "livebench_reasoning_score",
        "livebench_coding_score",
        "livebench_agentic_coding_score",
        "livebench_mathematics_score",
        "livebench_data_analysis_score",
        "livebench_language_score",
        "livebench_instruction_following_score",
        "livebench_cost_per_question_usd",
        "livebench_cost_per_successful_task_usd",
        "livebench_avg_input_tokens",
        "livebench_avg_output_tokens",
        "api_price_input_usd_mtok",
        "api_price_output_usd_mtok",
    ]
    df = dims(ctx.obs(source="livebench_leaderboard", metric=metrics), "model", "release")
    if df.empty:
        return df
    df = latest(df, keys=("entity", "metric", "model"))
    df["comparison_model"] = df["model"].map(model_key)
    return (
        df.pivot_table(
            index=["entity", "model", "comparison_model", "release", "as_of"],
            columns="metric",
            values="value",
            aggfunc="last",
        )
        .reset_index()
        .sort_values(["entity", "model"])
    )


def matched_benchmark_models(ctx) -> pd.DataFrame:
    """One model per company that is present in both Arena and Artificial Analysis, without cross-model filling."""
    arena = arena_models(ctx)
    benchmark = artificial_analysis_models(ctx)
    if arena.empty or benchmark.empty:
        return pd.DataFrame()
    arena = arena.assign(comparison_model=arena["model"].map(model_key)).rename(
        columns={
            "model": "arena_model",
            "rank": "arena_rank",
            "source_url": "arena_url",
            "as_of": "arena_as_of",
            "context_window_tokens": "arena_context_tokens",
        }
    )
    benchmark = benchmark.rename(
        columns={
            "model": "benchmark_model",
            "source_url": "benchmark_url",
            "as_of": "benchmark_as_of",
            "context_window_tokens": "benchmark_context_tokens",
        }
    )
    joined = arena.merge(benchmark, on=["entity", "comparison_model"], how="inner")
    livebench = livebench_models(ctx)
    livebench_entities: set[str] = set()
    if len(livebench):
        livebench_entities = set(livebench["entity"])
        livebench = livebench.rename(
            columns={
                "model": "livebench_model",
                "as_of": "livebench_as_of",
                "api_price_input_usd_mtok": "livebench_input_price",
                "api_price_output_usd_mtok": "livebench_output_price",
            }
        )
        joined = joined.merge(livebench, on=["entity", "comparison_model"], how="left")
    empty = pd.Series(index=joined.index, dtype=float)
    joined["selected_input_price"] = joined["api_price_input_usd_mtok"].combine_first(
        joined.get("livebench_input_price", empty)
    )
    joined["selected_output_price"] = joined["api_price_output_usd_mtok"].combine_first(
        joined.get("livebench_output_price", empty)
    )
    joined["selected_context_tokens"] = joined["arena_context_tokens"].combine_first(joined["benchmark_context_tokens"])
    arena_price = joined["api_price_input_usd_mtok"].notna() & joined["api_price_output_usd_mtok"].notna()
    livebench_price = (
        joined["livebench_input_price"].notna() & joined["livebench_output_price"].notna()
        if "livebench_input_price" in joined
        else pd.Series(False, index=joined.index)
    )
    joined["price_source"] = [
        "Arena" if from_arena else "LiveBench" if from_livebench else None
        for from_arena, from_livebench in zip(arena_price, livebench_price, strict=True)
    ]
    required = [
        "arena_text_score",
        "arena_text_votes",
        "selected_input_price",
        "selected_output_price",
        "selected_context_tokens",
        "artificial_analysis_intelligence_index",
    ]
    if joined.empty or any(column not in joined for column in required):
        return pd.DataFrame()
    comparable = joined.dropna(subset=required)
    chosen = []
    for entity, group in comparable.groupby("entity"):
        if entity in livebench_entities and "livebench_overall_score" in group:
            triple_covered = group.dropna(subset=["livebench_overall_score"])
            if len(triple_covered):
                group = triple_covered
        chosen.append(
            group.sort_values(["arena_rank", "artificial_analysis_intelligence_index"], ascending=[True, False]).iloc[0]
        )
    if not chosen:
        return pd.DataFrame()
    selected = pd.DataFrame(chosen)
    order = {entity: n for n, entity in enumerate(PEER_ORDER)}
    return selected.assign(_order=selected["entity"].map(order)).sort_values("_order")


def frontier_models(ctx) -> pd.DataFrame:
    """Highest-ranked Arena entry per company with comparable score, price and context fields."""
    df = arena_models(ctx)
    required = [
        "arena_text_score",
        "arena_text_votes",
        "api_price_input_usd_mtok",
        "api_price_output_usd_mtok",
        "context_window_tokens",
    ]
    if df.empty or any(c not in df for c in required):
        return pd.DataFrame()
    comparable = df.dropna(subset=required)
    return comparable.sort_values("rank").groupby("entity", as_index=False).first()


def peer_name(ctx, entity: str) -> str:
    return ctx.names.get(entity, entity)


def selected_model_rows(ctx) -> list[dict]:
    df = frontier_models(ctx)
    if df.empty:
        return []
    order = {entity: n for n, entity in enumerate(PEER_ORDER)}
    df = df.assign(_order=df["entity"].map(order)).sort_values("_order")
    return [
        {
            "company": peer_name(ctx, r.entity),
            "model": r.model,
            "rank": int(r.rank),
            "score": float(r.arena_text_score),
            "margin": float(r.score_margin),
            "votes": int(r.arena_text_votes),
            "input": float(r.api_price_input_usd_mtok),
            "output": float(r.api_price_output_usd_mtok),
            "context": int(r.context_window_tokens),
            "status": "Preliminary" if bool(r.preliminary) else "Ranked",
            "link": r.source_url,
        }
        for r in df.itertuples()
    ]


# ---- peer model comparison ----
@mart(id="product.frontier_scorecard", sources=["arena_text_leaderboard"])
def frontier_scorecard(ctx):
    rows = selected_model_rows(ctx)
    takeaway = []
    if rows:
        best = min(rows, key=lambda r: r["rank"])
        anthropic = next((r for r in rows if r["company"] == "Anthropic"), None)
        text = f"{best['model']} ({best['company']}) is the highest-ranked fully specified peer model at Arena rank {best['rank']}."
        takeaway = [text]
        if anthropic and anthropic is not best:
            takeaway.append(
                f"Anthropic's selected model is rank {anthropic['rank']} with a {num(anthropic['context'])}-token context window."
            )
    return {
        "title": "Frontier model scorecard",
        "subtitle": "Highest-ranked Arena model per company with score, input/output price and context all published",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "rank", "label": "Arena rank", "format": "int"},
            {"field": "score", "label": "Arena score", "format": "float"},
            {"field": "margin", "label": "±", "format": "float"},
            {"field": "votes", "label": "Votes", "format": "int"},
            {"field": "input", "label": "Input USD / MTok", "format": "float"},
            {"field": "output", "label": "Output USD / MTok", "format": "float"},
            {"field": "context", "label": "Context tokens", "format": "int"},
            {"field": "status", "label": "Score status", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Selection rule: the highest-ranked entry for each company with an Arena score, input price, output price and context window all present. A higher-ranked row with missing fields is skipped rather than silently mixing specifications from another model.",
            "Arena is a human-preference leaderboard, not an objective task-accuracy benchmark. Reasoning levels and model variants appear as separate entries.",
            "Prices and context are Arena's reported fields. Prices are per million tokens and exclude caching, batch and long-context adjustments.",
            "Preliminary scores can move as votes accumulate; the score margin shown is Arena's published ± value.",
        ],
        "badges": [],
    }


@mart(id="product.price_vs_quality", sources=["arena_text_leaderboard"])
def price_vs_quality(ctx):
    rows = [
        {
            "company": r["company"],
            "model": r["model"],
            "output": r["output"],
            "score": r["score"],
            "rank": r["rank"],
            "votes": r["votes"],
        }
        for r in selected_model_rows(ctx)
    ]
    takeaway = []
    if rows:
        cheap = min(rows, key=lambda r: r["output"])
        best = max(rows, key=lambda r: r["score"])
        takeaway = [
            (
                f"{cheap['company']} has the lowest output-token list price in this selected set "
                f"(${cheap['output']:g}/MTok); {best['company']} has the highest Arena score ({best['score']:g})."
            )
        ]
    return {
        "title": "Arena score versus output-token price",
        "subtitle": "Selected fully specified frontier model per company; upper-left combines a higher score with a lower list price",
        "kind": "scatter",
        "encoding": {
            "x": {
                "field": "output",
                "type": "quantitative",
                "label": "Output USD per million tokens",
                "format": "float",
            },
            "y": {"field": "score", "type": "quantitative", "label": "Arena score", "format": "float"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "output", "label": "Output USD / MTok", "format": "float"},
            {"field": "score", "label": "Arena score", "format": "float"},
            {"field": "rank", "label": "Arena rank", "format": "int"},
            {"field": "votes", "label": "Votes", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "This is a price-versus-preference comparison, not a claim that Arena score measures business value or task accuracy.",
            "One selected model per company, using the same complete-field selection rule as the frontier scorecard.",
            "Tokenizers and token counts differ by model, so equal per-token prices do not guarantee equal cost for the same text.",
        ],
        "badges": [],
    }


@mart(id="product.context_windows", sources=["arena_text_leaderboard"])
def context_windows(ctx):
    rows = [{"company": r["company"], "model": r["model"], "context": r["context"]} for r in selected_model_rows(ctx)]
    takeaway = []
    if rows:
        biggest = max(rows, key=lambda r: r["context"])
        smallest = min(rows, key=lambda r: r["context"])
        takeaway = [
            f"Published context spans {num(smallest['context'])} to {num(biggest['context'])} tokens across these selected models."
        ]
    return {
        "title": "Published context windows",
        "subtitle": "Maximum context reported by Arena for each selected model",
        "kind": "bar",
        "encoding": {
            "x": {"field": "company", "type": "nominal", "label": "Company"},
            "y": {"field": "context", "type": "quantitative", "label": "Context tokens", "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "context", "label": "Context tokens", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Advertised context capacity is not evidence that accuracy remains constant across the full window.",
            "One selected model per company, using the same complete-field selection rule as the frontier scorecard.",
        ],
        "badges": [],
    }


TASKS = {
    "quick": (2_000, 500),
    "coding": (20_000, 5_000),
    "long_document": (100_000, 5_000),
}


def workload_cost(input_price: float, output_price: float, tokens: tuple[int, int]) -> float:
    input_tokens, output_tokens = tokens
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


@mart(id="product.task_costs", sources=["arena_text_leaderboard"])
def task_costs(ctx):
    rows = [
        {
            "company": r["company"],
            "model": r["model"],
            "quick_cents": round(workload_cost(r["input"], r["output"], TASKS["quick"]) * 100, 3),
            "coding_cents": round(workload_cost(r["input"], r["output"], TASKS["coding"]) * 100, 3),
            "long_document_cents": round(workload_cost(r["input"], r["output"], TASKS["long_document"]) * 100, 3),
        }
        for r in selected_model_rows(ctx)
    ]
    takeaway = []
    if rows:
        coding = min(rows, key=lambda r: r["coding_cents"])
        long_doc = min(rows, key=lambda r: r["long_document_cents"])
        if coding["company"] == long_doc["company"]:
            takeaway = [
                (
                    f"At these fixed token counts, {coding['company']} is cheapest for both the coding workload "
                    f"(${coding['coding_cents'] / 100:.3f}) and long-document analysis "
                    f"(${long_doc['long_document_cents'] / 100:.3f})."
                )
            ]
        else:
            takeaway = [
                (
                    f"At these fixed token counts, {coding['company']} is cheapest for the coding workload "
                    f"(${coding['coding_cents'] / 100:.3f}) and {long_doc['company']} for long-document analysis "
                    f"(${long_doc['long_document_cents'] / 100:.3f})."
                )
            ]
    return {
        "title": "List-price cost for fixed token workloads",
        "subtitle": "Estimated API cost per attempt: quick 2K/0.5K, coding 20K/5K, long document 100K/5K input/output tokens",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "quick_cents", "label": "Quick answer ¢", "format": "float"},
            {"field": "coding_cents", "label": "Coding attempt ¢", "format": "float"},
            {"field": "long_document_cents", "label": "Long-document attempt ¢", "format": "float"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "These are transparent fixed-token scenarios, not measured cost per successfully completed task. True cost per completed task also depends on success rate, retries, tool calls and latency.",
            "Formula per attempt in USD = input tokens × input price / 1M + output tokens × output price / 1M. The table displays US cents so sub-dollar differences remain visible.",
            "Reasoning tokens, caching, batch discounts, long-context surcharges and provider-specific tokenization are excluded.",
            "One selected model per company, using the same complete-field selection rule as the frontier scorecard.",
        ],
        "badges": ["arithmetic"],
    }


def optional_float(value: object) -> float | None:
    return None if pd.isna(value) else float(value)


def selected_livebench_rows(ctx) -> list[dict]:
    df = livebench_models(ctx)
    if df.empty or "livebench_overall_score" not in df:
        return []
    top = (
        df.dropna(subset=["livebench_overall_score"])
        .sort_values(["entity", "livebench_overall_score"], ascending=[True, False])
        .drop_duplicates("entity", keep="first")
    )
    order = {entity: n for n, entity in enumerate(PEER_ORDER)}
    top = top.assign(_order=top["entity"].map(order)).sort_values("_order")
    return [
        {
            "company": peer_name(ctx, row.entity),
            "model": row.model,
            "overall": float(row.livebench_overall_score),
            "reasoning": optional_float(getattr(row, "livebench_reasoning_score", None)),
            "coding": optional_float(getattr(row, "livebench_coding_score", None)),
            "agentic": optional_float(getattr(row, "livebench_agentic_coding_score", None)),
            "mathematics": optional_float(getattr(row, "livebench_mathematics_score", None)),
            "data_analysis": optional_float(getattr(row, "livebench_data_analysis_score", None)),
            "language": optional_float(getattr(row, "livebench_language_score", None)),
            "instruction": optional_float(getattr(row, "livebench_instruction_following_score", None)),
            "success_cost": optional_float(getattr(row, "livebench_cost_per_successful_task_usd", None)),
            "input_price": optional_float(getattr(row, "api_price_input_usd_mtok", None)),
            "output_price": optional_float(getattr(row, "api_price_output_usd_mtok", None)),
            "avg_input": optional_float(getattr(row, "livebench_avg_input_tokens", None)),
            "avg_output": optional_float(getattr(row, "livebench_avg_output_tokens", None)),
        }
        for row in top.itertuples()
    ]


@mart(id="product.livebench_leaderboard", sources=["livebench_leaderboard"])
def livebench_leaderboard(ctx):
    rows = selected_livebench_rows(ctx)
    takeaway = []
    if rows:
        leader = max(rows, key=lambda row: row["overall"])
        cheapest = min(
            (row for row in rows if row["success_cost"] is not None),
            key=lambda row: row["success_cost"],
            default=None,
        )
        takeaway = [f"{leader['model']} leads these company representatives at {leader['overall']:.1f} overall."]
        if cheapest:
            takeaway.append(
                f"{cheapest['model']} has the lowest LiveBench cost per successful task (${cheapest['success_cost']:.3f})."
            )
    return {
        "title": "LiveBench peer leaderboard",
        "subtitle": "Highest-overall model per covered company in the latest release; objective category scores and cost per successful task",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "overall", "label": "Overall", "format": "float"},
            {"field": "reasoning", "label": "Reasoning", "format": "float"},
            {"field": "coding", "label": "Coding", "format": "float"},
            {"field": "agentic", "label": "Agentic coding", "format": "float"},
            {"field": "mathematics", "label": "Mathematics", "format": "float"},
            {"field": "data_analysis", "label": "Data analysis", "format": "float"},
            {"field": "language", "label": "Language", "format": "float"},
            {"field": "instruction", "label": "Instruction following", "format": "float"},
            {"field": "success_cost", "label": "Cost / successful task USD", "format": "float"},
            {"field": "input_price", "label": "Input USD / MTok", "format": "float"},
            {"field": "output_price", "label": "Output USD / MTok", "format": "float"},
            {"field": "avg_input", "label": "Avg input tokens", "format": "float"},
            {"field": "avg_output", "label": "Avg output tokens", "format": "float"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "LiveBench uses objective, automatically scored ground truth and periodically refreshes tasks to limit contamination. A benchmark score is still not a complete measure of production usefulness.",
            "Overall is the mean of seven category averages, reproducing LiveBench's published calculation. The representative is each covered company's highest-overall model in the latest release.",
            "Cost per successful task = total recorded cost ÷ question count ÷ score fraction over the release workload. It incorporates actual verbosity and success rate, unlike a fixed-token estimate.",
            "Input/output prices and average tokens are LiveBench's published cost-file fields. Caching treatment and provider routing follow LiveBench's runner and may differ from a buyer's deployment.",
            "The current release contains Anthropic, OpenAI, Google and xAI models, but no Mistral or Cohere model; absent companies are unknown, not zero.",
        ],
        "badges": ["arithmetic"],
    }


@mart(id="product.livebench_cost_quality", sources=["livebench_leaderboard"])
def livebench_cost_quality(ctx):
    rows = [
        {
            "company": row["company"],
            "model": row["model"],
            "success_cost": row["success_cost"],
            "overall": row["overall"],
        }
        for row in selected_livebench_rows(ctx)
        if row["success_cost"] is not None
    ]
    takeaway = []
    if rows:
        leader = max(rows, key=lambda row: row["overall"])
        cheapest = min(rows, key=lambda row: row["success_cost"])
        takeaway = [
            (
                f"{leader['model']} has the highest overall score ({leader['overall']:.1f}); "
                f"{cheapest['model']} has the lowest cost per successful task (${cheapest['success_cost']:.3f})."
            )
        ]
    return {
        "title": "LiveBench quality versus cost per successful task",
        "subtitle": "Highest-overall model per covered company; upper-left combines a higher objective score with lower success-adjusted cost",
        "kind": "scatter",
        "encoding": {
            "x": {
                "field": "success_cost",
                "type": "quantitative",
                "label": "Cost per successful task (USD)",
                "format": "float",
            },
            "y": {
                "field": "overall",
                "type": "quantitative",
                "label": "LiveBench overall",
                "format": "float",
            },
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "success_cost", "label": "Cost / successful task USD", "format": "float"},
            {"field": "overall", "label": "LiveBench overall", "format": "float"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Mirrors LiveBench's quality-versus-cost view, restricted to one highest-overall model per covered company so a prolific provider does not dominate the chart.",
            "Cost is success-adjusted only for the LiveBench release workload; it should not be treated as the expected cost of an arbitrary production task.",
            "Mistral and Cohere have no model in the current LiveBench release and are not plotted.",
        ],
        "badges": ["arithmetic"],
    }


@mart(id="product.benchmark_cost_quality", sources=["arena_text_leaderboard", "artificial_analysis_leaderboard"])
def benchmark_cost_quality(ctx):
    selected = matched_benchmark_models(ctx)
    rows = []
    if len(selected) and "artificial_analysis_cost_per_task_usd" in selected:
        rows = [
            {
                "company": peer_name(ctx, row.entity),
                "model": row.arena_model,
                "task_cost": float(row.artificial_analysis_cost_per_task_usd),
                "intelligence": float(row.artificial_analysis_intelligence_index),
                "arena_rank": int(row.arena_rank),
            }
            for row in selected.dropna(subset=["artificial_analysis_cost_per_task_usd"]).itertuples()
        ]
    takeaway = []
    if rows:
        highest = max(rows, key=lambda row: row["intelligence"])
        cheapest = min(rows, key=lambda row: row["task_cost"])
        takeaway = [
            (
                f"{highest['model']} has the highest Artificial Analysis index in this matched set "
                f"({highest['intelligence']:g}) at ${highest['task_cost']:.2f} per benchmark task; "
                f"{cheapest['model']} has the lowest reported task cost (${cheapest['task_cost']:.2f})."
            )
        ]
    return {
        "title": "Benchmark quality versus measured task cost",
        "subtitle": "Same-model rows shared by Arena and Artificial Analysis; upper-left combines a higher index with lower benchmark-task cost",
        "kind": "scatter",
        "encoding": {
            "x": {
                "field": "task_cost",
                "type": "quantitative",
                "label": "Artificial Analysis cost per task (USD)",
                "format": "float",
            },
            "y": {
                "field": "intelligence",
                "type": "quantitative",
                "label": "Artificial Analysis Intelligence Index",
                "format": "float",
            },
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Model", "format": "text"},
            {"field": "task_cost", "label": "AA task cost (USD)", "format": "float"},
            {"field": "intelligence", "label": "AA Intelligence Index", "format": "float"},
            {"field": "arena_rank", "label": "Arena rank", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Artificial Analysis cost per task is the weighted-average USD consumed by one task in its Intelligence Index workload, using the actual input, cached-input and output tokens generated. It is not a universal business-task cost.",
            "Selection rule: for each company, use the highest-ranked fully priced Arena model whose exact normalized model name also appears in Artificial Analysis. A documented alias maps Arena's dated command-a-03-2025 identifier to Artificial Analysis's Command A label.",
            "Rows without a published Artificial Analysis task cost are absent from this scatter, not treated as zero. The combined table below retains them with a blank cell.",
            "The relationship is descriptive, not causal: higher task cost can reflect more reasoning tokens or longer answers as well as token list price.",
        ],
        "badges": [],
    }


@mart(
    id="product.combined_model_signal",
    sources=[
        "arena_text_leaderboard",
        "artificial_analysis_leaderboard",
        "livebench_leaderboard",
        "openrouter_rankings",
    ],
)
def combined_model_signal(ctx):
    selected = matched_benchmark_models(ctx)
    share_rows = dims(
        ctx.obs(source="openrouter_rankings", metric="openrouter_request_share"),
        "rank",
    )
    shares = {}
    if len(share_rows):
        share_rows = latest(share_rows, keys=("entity", "metric"))
        shares = {row.entity: float(row.value) for row in share_rows.itertuples()}
    rows = []
    for row in selected.itertuples():
        rows.append(
            {
                "company": peer_name(ctx, row.entity),
                "model": row.arena_model,
                "arena_rank": int(row.arena_rank),
                "arena_score": float(row.arena_text_score),
                "input_price": float(row.selected_input_price),
                "output_price": float(row.selected_output_price),
                "price_source": row.price_source,
                "coding_cents": round(
                    workload_cost(
                        float(row.selected_input_price),
                        float(row.selected_output_price),
                        TASKS["coding"],
                    )
                    * 100,
                    3,
                ),
                "aa_index": float(row.artificial_analysis_intelligence_index),
                "aa_task_cost": optional_float(getattr(row, "artificial_analysis_cost_per_task_usd", None)),
                "livebench_overall": optional_float(getattr(row, "livebench_overall_score", None)),
                "livebench_success_cost": optional_float(getattr(row, "livebench_cost_per_successful_task_usd", None)),
                "first_chunk": optional_float(getattr(row, "artificial_analysis_first_chunk_seconds", None)),
                "output_speed": optional_float(getattr(row, "artificial_analysis_output_tokens_s", None)),
                "total_response": optional_float(getattr(row, "artificial_analysis_total_response_seconds", None)),
                "context": int(row.selected_context_tokens),
                "openrouter_share": shares.get(row.entity),
            }
        )
    takeaway = []
    measured = [row for row in rows if row["aa_task_cost"] is not None]
    live_measured = [row for row in rows if row["livebench_success_cost"] is not None]
    timed = [row for row in rows if row["first_chunk"] is not None]
    if measured:
        highest = max(measured, key=lambda row: row["aa_index"])
        cheapest = min(measured, key=lambda row: row["aa_task_cost"])
        takeaway.append(
            f"{highest['model']} has the highest Artificial Analysis index in the matched set ({highest['aa_index']:g}) at ${highest['aa_task_cost']:.2f} per benchmark task; {cheapest['model']} has the lowest reported benchmark-task cost (${cheapest['aa_task_cost']:.2f})."
        )
    secondary = []
    if live_measured:
        leader = max(live_measured, key=lambda row: row["livebench_overall"])
        cheapest_live = min(live_measured, key=lambda row: row["livebench_success_cost"])
        secondary.append(
            f"On LiveBench, {leader['model']} has the highest overall score ({leader['livebench_overall']:.1f}), while {cheapest_live['model']} has the lowest success-adjusted cost (${cheapest_live['livebench_success_cost']:.3f})"
        )
    if timed:
        fastest = min(timed, key=lambda row: row["first_chunk"])
        secondary.append(
            f"{fastest['model']} has the lowest Artificial Analysis first-chunk latency ({fastest['first_chunk']:.2f}s)"
        )
    if secondary:
        takeaway.append("; ".join(secondary) + ".")
    return {
        "title": "Combined model economics, quality, speed and usage signal",
        "subtitle": "One matched model per company; blanks mean the source does not publish that measurement for the selected model",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "model", "label": "Same model", "format": "text"},
            {"field": "arena_rank", "label": "Arena rank", "format": "int"},
            {"field": "arena_score", "label": "Arena score", "format": "float"},
            {"field": "aa_index", "label": "AA index", "format": "float"},
            {"field": "input_price", "label": "Input USD / MTok", "format": "float"},
            {"field": "output_price", "label": "Output USD / MTok", "format": "float"},
            {"field": "price_source", "label": "Token-price source", "format": "text"},
            {"field": "coding_cents", "label": "20K/5K coding attempt ¢", "format": "float"},
            {"field": "aa_task_cost", "label": "AA task cost USD", "format": "float"},
            {"field": "livebench_overall", "label": "LiveBench overall", "format": "float"},
            {
                "field": "livebench_success_cost",
                "label": "LiveBench cost / success USD",
                "format": "float",
            },
            {"field": "first_chunk", "label": "First chunk s", "format": "float"},
            {"field": "output_speed", "label": "Output tok/s", "format": "float"},
            {"field": "total_response", "label": "Total response s", "format": "float"},
            {"field": "context", "label": "Context tokens", "format": "int"},
            {"field": "openrouter_share", "label": "OpenRouter author share", "format": "pct"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Every row is one same model across its populated model-level sources. Where LiveBench covers a company, selection uses the highest-ranked Arena entry present in both Artificial Analysis and LiveBench with a same-model price pair; otherwise it uses the highest-ranked Arena–Artificial Analysis match. No field is borrowed from another variant.",
            "Blank means not published for that same model, not zero. The current LiveBench release has no Mistral or Cohere model. Some Artificial Analysis rows omit cost or timing, and OpenRouter omits xAI and Cohere from its displayed author leaders.",
            "The coding-attempt estimate is our arithmetic at 20K input and 5K output tokens using the same model's Arena price pair when available, otherwise LiveBench's published price pair; the source is shown per row. It is cost per attempt, not cost per successful task, and excludes retries, tools, caching, batch discounts and reasoning-token differences.",
            "Artificial Analysis task cost is measured on its weighted Intelligence Index workload using actual tokens consumed. It is not directly comparable to the fixed 20K/5K coding attempt because the task mix and token counts differ.",
            "LiveBench cost per successful task divides its recorded cost per question by the score fraction over an objective, automatically scored workload. It is success-adjusted for that release only, not for a generic production task.",
            "First chunk is request-to-first-returned-chunk latency; output speed is median generation speed after the first token; total response is end-to-end. Provider routing, endpoint choice and load can change all three.",
            "Arena score is human preference; Artificial Analysis and LiveBench are different benchmark suites. OpenRouter share is company-level public request share, not usage of the selected model, total market share, users, tokens or revenue.",
            "These columns reveal trade-offs but do not prove that a higher token price causes higher quality or lower latency.",
        ],
        "badges": ["arithmetic"],
    }


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


@mart(id="product.peer_plans", sources=["product_peer_plan_prices"])
def peer_plans(ctx):
    df = dims(ctx.obs(metric="plan_price_usd_month"), "product", "plan", "billing")
    rows = []
    if len(df):
        order = {entity: n for n, entity in enumerate(PEER_ORDER)}
        df = latest(df, keys=("entity", "product", "plan")).assign(_order=lambda d: d["entity"].map(order))
        rows = [
            {
                "company": peer_name(ctx, r.entity),
                "product": r.product,
                "plan": r.plan,
                "price": float(r.value),
                "billing": r.billing,
                "quote": r.evidence,
                "link": r.source_url,
            }
            for r in df.sort_values("_order").itertuples()
        ]
    takeaway = []
    if rows:
        low, high = min(rows, key=lambda r: r["price"]), max(rows, key=lambda r: r["price"])
        takeaway = [
            (
                f"Standard individual paid plans range from ${low['price']:.2f} a month at {low['company']} "
                f"to ${high['price']:.2f} at {high['company']}."
            )
        ]
    return {
        "title": "Standard individual subscription prices",
        "subtitle": "One representative paid monthly consumer plan per company, from official pricing pages",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "product", "label": "Product", "format": "text"},
            {"field": "plan", "label": "Plan", "format": "text"},
            {"field": "price", "label": "USD per month", "format": "usd"},
            {"field": "billing", "label": "Billing", "format": "text"},
            {"field": "quote", "label": "What the page says", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "One standard paid individual plan per company, using US monthly list price before tax. Annual discounts are excluded.",
            "Features, usage allowances and included storage differ substantially, so price alone is not a value comparison.",
            "API usage is separate. Cohere has no comparable consumer subscription in this table.",
            "Hand-maintained ledger: refresh when an official pricing page changes.",
        ],
        "badges": [],
    }


@mart(id="product.public_adoption", sources=["product_adoption_claims"])
def public_adoption(ctx):
    df = dims(ctx.obs(metric="public_adoption_count"), "measure", "period", "scope", "qualifier")
    rows = []
    if len(df):
        order = {entity: n for n, entity in enumerate(PEER_ORDER)}
        df = latest(df, keys=("entity", "measure", "scope")).assign(_order=lambda d: d["entity"].map(order))
        rows = [
            {
                "company": peer_name(ctx, r.entity),
                "disclosure": r.measure,
                "value": int(r.value),
                "qualifier": r.qualifier,
                "period": r.period,
                "scope": r.scope,
                "date": r.as_of,
                "quote": r.evidence,
                "link": r.source_url,
            }
            for r in df.sort_values("_order").itertuples()
        ]
    return {
        "title": "Public adoption disclosures",
        "subtitle": "Latest usable company-stated count; shown as a disclosure table because the scopes are not comparable",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "disclosure", "label": "Measure", "format": "text"},
            {"field": "value", "label": "Count", "format": "int"},
            {"field": "qualifier", "label": "Qualifier", "format": "text"},
            {"field": "period", "label": "Period", "format": "text"},
            {"field": "scope", "label": "Scope", "format": "text"},
            {"field": "date", "label": "Disclosure date", "format": "date"},
            {"field": "quote", "label": "What the company said", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": [
            "These disclosures answer different questions: consumer reach for OpenAI and Google, blended X/Grok reach for xAI, and high-spend customers for Anthropic. They do not support a single adoption ranking."
        ]
        if rows
        else [],
        "assumptions": [
            "Do not compare the counts directly: weekly users, monthly users, blended platform reach and customers above a spend threshold are different measures.",
            "xAI's figure combines X and Grok apps, so it is not a standalone Grok user count. Anthropic disclosed customers spending more than $1 million annualized, not users or subscriptions.",
            "Mistral and Cohere are omitted because no sufficiently clear comparable public count was identified; omission does not mean zero adoption.",
            "Company-stated figures are not independently audited. Each row preserves the original scope, qualifier, quote and date.",
        ],
        "badges": [],
    }
