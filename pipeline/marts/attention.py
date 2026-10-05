"""Attention charts: how much the public and developers are looking at each company. Takeaways are computed from the rows."""

import calendar
from datetime import date

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import change, dims, latest, num, roll

MONTH = {"field": "month", "label": "Month", "format": "date"}
COMPANY = {"field": "company", "label": "Company", "format": "text"}
NO_KEY = "Awaiting data: Google Trends has no public API, so this needs a SerpAPI key (SERPAPI_KEY in .env; the free plan is enough)."


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
        "Wikipedia views: the AI assistants",
        "Monthly views of each assistant's article: Claude, ChatGPT, Gemini (with Bard), Grok, Le Chat",
        [
            "Claude had no article of its own before January 2024 (it was covered inside Anthropic's), so its series starts then.",
            "Cohere has no consumer assistant. Gemini includes its earlier name Bard and the Gemini model-family article.",
        ],
    )


@mart(id="attention.wiki_companies", sources=["wikipedia_pageviews"])
def wiki_companies(ctx):
    return wiki_chart(
        ctx,
        "company",
        "Wikipedia views: the companies",
        "Monthly views of each company's article",
        ["xAI's article moved to SpaceXAI in 2026; both titles are summed."],
    )


# ---- news volume (GDELT) ----
@mart(id="attention.news_share", sources=["gdelt_news_volume"])
def news_share(ctx):
    df = dims(latest(ctx.obs(metric="news_articles"), keys=("entity", "as_of")), "total_articles")
    rows, takeaway = [], []
    if len(df):
        df = df.assign(total=df["total_articles"].astype(float), month=lambda d: d["as_of"].str[:7] + "-01")
        last_day = df["as_of"].max()
        m = df.groupby(["entity", "month"], as_index=False).agg(articles=("value", "sum"), total=("total", "sum"))
        m = m.assign(per_10k=m["articles"] / m["total"] * 10_000)
        m = complete_months(m.rename(columns={"month": "as_of"}), last_day).rename(columns={"as_of": "month"})
        order = {
            e: i
            for i, e in enumerate(m[m["month"] == m["month"].max()].sort_values("per_10k", ascending=False)["entity"])
        }
        m = m.assign(rank=m["entity"].map(order)).sort_values(["month", "rank"])
        rows = [
            {
                "month": r.month,
                "company": ctx.names.get(r.entity, r.entity),
                "per_10k": round(r.per_10k, 2),
                "articles": int(r.articles),
            }
            for r in m.itertuples()
        ]
        last = m["month"].max()
        now = m[m["month"] == last].set_index("entity")["per_10k"]
        if "anthropic" in now:
            lead = now.idxmax()
            a = now["anthropic"]
            prior = m[
                (m["entity"] == "anthropic")
                & (m["month"] == date.fromisoformat(last).replace(year=date.fromisoformat(last).year - 1).isoformat())
            ]
            text = f"Anthropic appeared in {a:.1f} of every 10,000 monitored English-language news articles in {month_label(last)}"
            text += f" ({change(a, float(prior['per_10k'].iloc[0]))} on a year earlier)" if len(prior) else ""
            text += (
                f"; {ctx.names.get(lead, lead)} was at {now[lead]:.1f}."
                if lead != "anthropic"
                else ", the most of the tracked companies."
            )
            takeaway = [text]
    return line_chart(
        "Share of news coverage",
        "Articles mentioning each company per 10,000 English-language articles GDELT monitored",
        "Articles per 10,000",
        "float",
        rows,
        takeaway,
        [
            "GDELT's monitored article set has shrunk and shifted since 2023, so a raw count would mislead. The chart is each company's share of that day's monitored articles.",
            "Share = articles matching the company's search terms ÷ all monitored English articles, summed over the month. Raw counts are in the table.",
            "Search terms match by words, so some unrelated articles are included, most for common words (Mistral, Cohere, Grok).",
            "Complete months only.",
        ],
        value="per_10k",
        extra_columns=({"field": "articles", "label": "Matching articles", "format": "int"},),
        badges=("arithmetic",),
    )


# ---- Hacker News ----
@mart(id="attention.hn_stories", sources=["hackernews_stories"])
def hn_stories(ctx):
    df = dims(
        latest(ctx.obs(metric=["hn_stories", "hn_stories_50plus"]), keys=("entity", "metric", "as_of")), "partial_month"
    )
    df = df[~df["partial_month"].astype(bool)] if len(df) else df
    rows, takeaway = [], []
    if len(df):
        wide = df.pivot_table(index=["entity", "as_of"], columns="metric", values="value").reset_index()
        last = wide["as_of"].max()
        order = {
            e: i
            for i, e in enumerate(
                wide[wide["as_of"] == last].sort_values("hn_stories_50plus", ascending=False)["entity"]
            )
        }
        wide = wide.assign(rank=wide["entity"].map(order)).sort_values(["as_of", "rank"])
        rows = [
            {
                "month": r.as_of,
                "company": ctx.names.get(r.entity, r.entity),
                "front": int(r.hn_stories_50plus),
                "stories": int(r.hn_stories),
            }
            for r in wide.itertuples()
        ]
        now = wide[wide["as_of"] == last].set_index("entity")["hn_stories_50plus"]
        if "anthropic" in now:
            lead = now.idxmax()
            text = f"Anthropic had {num(now['anthropic'])} Hacker News stories with 50+ points in {month_label(last)}"
            text += (
                f", against {ctx.names.get(lead, lead)} at {num(now[lead])}."
                if lead != "anthropic"
                else ", the most of the tracked companies."
            )
            takeaway = [text]
    return line_chart(
        "Hacker News front-page stories",
        "Stories with 50 or more points mentioning each company, by submission month",
        "Stories with 50+ points",
        "int",
        rows,
        takeaway,
        [
            "A developer-community signal: Hacker News readers are engineers and founders, not the general public.",
            "A story counts if its title or text matches the company's search words; 50 or more points keeps the ones that drew real attention.",
            "xAI is searched as 'Grok' because 'xAI' also matches unrelated stories. Mistral AI and Cohere have few stories, so their lines are noisy.",
            "Complete months only. Reddit is not included: it requires authorised API access.",
        ],
        value="front",
        extra_columns=({"field": "stories", "label": "All stories", "format": "int"},),
    )


# ---- App Store ----
@mart(id="attention.appstore_rank", sources=["appstore_top_charts"])
def appstore_rank(ctx):
    df = dims(latest(ctx.obs(metric="appstore_rank"), keys=("entity", "as_of")), "app_name").sort_values("as_of")
    rows = []
    if len(df):
        today_ = df["as_of"].max()
        for entity, g in df.groupby("entity"):
            if g["as_of"].max() != today_:
                continue  # not in the top 100 on the latest day: no rank is shown, never an invented one
            now = g.iloc[-1]
            prev = g[g["as_of"] < now.as_of]
            rows.append(
                {
                    "company": ctx.names.get(entity, entity),
                    "app": now.app_name,
                    "rank": int(now.value),
                    "climbed": int(prev.iloc[-1].value - now.value) if len(prev) else None,
                    "since": prev.iloc[-1].as_of if len(prev) else None,
                }
            )
        rows.sort(key=lambda r: r["rank"])
    takeaway = []
    a = next((r for r in rows if r["company"] == ctx.names.get("anthropic")), None)
    if a:
        ahead = [f"{r['app']} #{r['rank']}" for r in rows if r["rank"] < a["rank"]]
        takeaway = [
            f"{a['app']} is #{a['rank']} on the US App Store free chart"
            + (f", behind {', '.join(ahead)}." if ahead else ", ahead of every other tracked app.")
        ]
    return {
        "title": "US App Store rank today",
        "subtitle": "Position in Apple's top 100 free iPhone apps, latest snapshot",
        "kind": "stat",
        "encoding": {"y": {"field": "rank", "type": "quantitative", "label": "Rank (1 is best)", "format": "int"}},
        "columns": [
            COMPANY,
            {"field": "rank", "label": "Rank", "format": "int"},
            {"field": "climbed", "label": "Places climbed (+) or fallen (-) since last snapshot", "format": "int"},
            {"field": "app", "label": "App", "format": "text"},
            {"field": "since", "label": "Compared with", "format": "date"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Apple publishes only today's top 100 and no history, so this chart's history starts on the first day we collected (2026-10-05).",
            "An app outside the top 100 has no rank here: it is left out, not shown as 101 or below. Cohere has no consumer app.",
            "Rank follows downloads, so it moves with launches and promotions. US storefront only.",
        ],
        "badges": [],
    }


# ---- Google Trends ----
@mart(id="attention.search_interest", sources=["google_trends"])
def search_interest(ctx):
    df = ctx.obs(metric="search_interest")
    rows, takeaway = [], [NO_KEY]
    if len(df):
        t = pd.to_datetime(df["retrieved_at"], utc=True, format="ISO8601")
        df = df[t == t.max()]  # one run only: each run scales 0-100 to its own window
        df = df.assign(month=pd.to_datetime(df["as_of"]).dt.to_period("M").dt.start_time.dt.date.astype(str))
        m = df.groupby(["entity", "month"], as_index=False)["value"].mean()
        order = {
            e: i
            for i, e in enumerate(m[m["month"] == m["month"].max()].sort_values("value", ascending=False)["entity"])
        }
        m = m.assign(rank=m["entity"].map(order)).sort_values(["month", "rank"])
        rows = [
            {"month": r.month, "company": ctx.names.get(r.entity, r.entity), "interest": round(r.value, 1)}
            for r in m.itertuples()
        ]
        now = m[m["month"] == m["month"].max()].set_index("entity")["value"]
        takeaway = [
            f"In {month_label(m['month'].max())} (month so far), search interest: "
            + ", ".join(f"{ctx.names.get(e, e)} {v:.0f}" for e, v in now.sort_values(ascending=False).items())
            + "."
        ]
    return line_chart(
        "Google search interest",
        "Relative search interest, 100 = the highest point of any compared term in the window",
        "Interest (0-100)",
        "float",
        rows,
        takeaway,
        [
            "Google Trends is relative: 100 is the peak of the most-searched term in the 5-year window, and every other value is a share of it.",
            "Terms compared: Claude AI, ChatGPT, Google Gemini, Grok AI, Mistral AI. Cohere is left out (Trends compares at most five).",
            "Shown as monthly averages of weekly values, from the newest run only, because each run rescales to its own window.",
            "Needs a SerpAPI key; without one this chart is empty.",
        ],
        value="interest",
    )


# ---- recent headlines ----
@mart(id="attention.recent_coverage", sources=["google_news_headlines"])
def recent_coverage(ctx):
    df = dims(ctx.obs(metric="news_headline", entity="anthropic"), "title", "outlet")
    rows = []
    if len(df):
        urls = dict(zip(df.index, df["source_url"], strict=True))
        df = (
            df.sort_values("retrieved_at")
            .drop_duplicates(["title", "outlet"], keep="last")
            .sort_values("as_of", ascending=False)
            .head(40)
        )
        rows = [
            {"date": r.as_of, "outlet": r.outlet, "headline": r.title, "link": urls[r.Index]} for r in df.itertuples()
        ]
    takeaway = [f"The latest {len(rows)} headlines Google News returns for Anthropic, newest first."] if rows else []
    return {
        "title": "Latest Anthropic headlines",
        "subtitle": "Google News results for Anthropic, with outlet and link",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "outlet", "label": "Outlet", "format": "text"},
            {"field": "headline", "label": "Headline", "format": "text"},
            {"field": "link", "label": "Link", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "About the 100 most recent results Google News gives for the search terms, deduplicated by headline and outlet; the 40 newest are shown.",
            "Google News keeps no history, so the list is a snapshot from our daily collection. Headlines are shown with their link, not reproduced.",
            "Search terms match by words, so some results are about the AI industry generally rather than Anthropic.",
        ],
        "badges": [],
    }
