"""Customers and contracts charts. Every number is counted from collected rows or quoted from a cited ledger row."""

import calendar
from datetime import date

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, pct, period_start

HN = ["hn_who_is_hiring"]
SEC = ["sec_filings_naming"]
MENTION_NOTE = "Naming a lab is not buying from it: a post or filing can name a lab as a tool, a supplier, an investor, a rival or a past employer."
ANY = "any"


def last_collected(ctx) -> str:
    return str(ctx.df["retrieved_at"].max())[:10]


def month_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{calendar.month_abbr[d.month]} {d.year}"


def quarter_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def month_of(as_of: pd.Series) -> pd.Series:
    return as_of.str[:7] + "-01"


def complete_months(df: pd.DataFrame, last_day: str) -> pd.DataFrame:
    """Drop the month still running on the last collection day, so a part month never reads as a fall."""
    d = date.fromisoformat(last_day)
    return df if d.day == calendar.monthrange(d.year, d.month)[1] else df[df["month"] < d.replace(day=1).isoformat()]


# ---- Hacker News: who is hiring ----
def hn_frames(ctx):
    """(posts per month, per-term naming counts per month), complete months only, newest collection per thread."""
    peek = ctx.df[ctx.df["metric"] == "hn_posts_total"]
    if peek.empty:
        return pd.DataFrame(), pd.DataFrame()
    months = peek.assign(month=month_of(peek["as_of"]))
    keep = (
        complete_months(months, last_collected(ctx))["as_of"].unique().tolist()
    )  # read only these, so as_of is honest
    total = ctx.obs(metric="hn_posts_total", as_of=keep)
    naming = ctx.obs(metric="hn_posts_naming", as_of=keep)
    posts = latest(total, keys=("as_of",))[["as_of", "value"]].rename(columns={"value": "posts"})
    counts = latest(dims(naming, "term"), keys=("entity", "as_of", "term"))[["entity", "as_of", "term", "value"]]
    counts = (
        counts.merge(posts, on="as_of").assign(month=lambda d: month_of(d["as_of"])).rename(columns={"value": "naming"})
    )
    return posts.assign(month=month_of(posts["as_of"])), counts


@mart(id="customers.hn_share", sources=HN)
def hn_share(ctx):
    _, n = hn_frames(ctx)
    rows, takeaway = [], []
    if len(n):
        any_ = n[n["term"] == ANY].sort_values(["as_of", "entity"])
        rows = [
            {
                "month": r.month,
                "company": ctx.names.get(r.entity, r.entity),
                "share": r.naming / r.posts,
                "naming": int(r.naming),
                "posts": int(r.posts),
            }
            for r in any_.itertuples()
        ]
        last = any_["month"].max()
        year_ago = f"{int(last[:4]) - 1}{last[4:]}"
        at = lambda m, e: any_[(any_["month"] == m) & (any_["entity"] == e)]
        a, o = at(last, "anthropic"), at(last, "openai")
        if len(a) and len(o):
            a0 = at(year_ago, "anthropic")
            takeaway = [
                f"In {month_label(last)}, {pct(a.iloc[0].naming / a.iloc[0].posts, 1)} of Who-is-hiring posts ({int(a.iloc[0].naming)} of {int(a.iloc[0].posts)}) "
                f"named Claude or Anthropic, against {pct(o.iloc[0].naming / o.iloc[0].posts, 1)} for OpenAI or GPT"
                + (
                    f"; a year earlier Anthropic was at {pct(a0.iloc[0].naming / a0.iloc[0].posts, 1)}."
                    if len(a0)
                    else "."
                )
            ]
    return {
        "title": "Share of hiring posts naming each lab",
        "subtitle": "Top-level posts in Hacker News 'Ask HN: Who is hiring?' that name the lab or its models, by month",
        "kind": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "label": "Month"},
            "y": {"field": "share", "type": "quantitative", "label": "Share of posts", "format": "pct"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "month", "label": "Month", "format": "date"},
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "share", "label": "Share of posts", "format": "pct"},
            {"field": "naming", "label": "Posts naming it", "format": "int"},
            {"field": "posts", "label": "Posts that month", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            MENTION_NOTE,
            "A post is a top-level comment in the monthly thread; replies are not counted. Algolia's thread comment count (394 for Sep 2026) includes replies; the posts counted here are 253.",
            "Terms per company: Anthropic = Claude, Anthropic; OpenAI = OpenAI, GPT, ChatGPT; Google DeepMind = Gemini; xAI = xAI, Grok; Mistral AI = Mistral; Cohere = Cohere. A post naming two terms counts once.",
            "Word-boundary matches, so ngrok and Capgemini do not count; 'Cohere Health' is excluded. Two of 55 Gemini matches are not the model (a solar project and a crypto exchange); they are kept.",
            "'DeepMind' is not counted for Google: of 31 posts naming it, only 2 are Google DeepMind's own postings; the rest are 'ex-DeepMind' or 'backed by DeepMind founders' lines.",
            "Posts that list investors or alumni count too. HN skews to startups and developer tools, so this is a signal of developer demand, not of enterprise spend. One recurring vendor post (Surge AI, 24 monthly threads) names both Anthropic and OpenAI.",
            "Complete months only. Threads are collected one at a time, so a month is never compared with a part-filled thread.",
        ],
        "badges": ["arithmetic"],
    }


# ---- federal awards ----


# ---- company-stated customer metrics (ledger) ----


# ---- SEC filings naming the labs ----
# SIC code -> sector. First matching range wins. Divisions follow the SEC's SIC division table, with one documented
# change: the software and data-processing codes (7370-7379) are split out of Services, because they are most of the
# filers and Services would otherwise hide the answer. SEC quirks stay: 2711 (newspapers) and 3674 (semiconductors) are
# Manufacturing, 4822 (message communications) is Transportation, communications & utilities.


def sec_filings(ctx) -> tuple[pd.DataFrame, str | None]:
    """(one row per company and filing, start of the last complete quarter).

    The newest collection wins, and a filing hit by two terms (Anthropic and a Claude product) counts once.
    Complete quarters only: the quarter still running on the last collection day is not read at all.
    """
    peek = ctx.df[ctx.df["metric"] == "sec_filing_mention"]
    if peek.empty:
        return peek, None
    last = date.fromisoformat(last_collected(ctx))
    running = pd.Period(last, "Q")
    done = running if last >= running.end_time.date() else running - 1
    keep = peek[pd.to_datetime(peek["as_of"]).dt.to_period("Q") <= done]
    df = ctx.obs(metric="sec_filing_mention", as_of=keep["as_of"].unique().tolist())
    df = latest(dims(df, "adsh", "cik", "filer", "form", "sic", "term"), keys=("entity", "adsh", "term"))
    df = df.drop_duplicates(["entity", "adsh"]).assign(quarter=lambda d: period_start(d["as_of"], "quarter"))
    return df, done.start_time.date().isoformat()


@mart(id="customers.sec_filers", sources=SEC)
def sec_filers(ctx):
    df, done = sec_filings(ctx)
    rows, takeaway = [], []
    if len(df):
        quarters = [p.start_time.date().isoformat() for p in pd.period_range(df["quarter"].min(), done, freq="Q")]
        counts = df.groupby(["entity", "quarter"])["cik"].nunique()
        for entity in df["entity"].unique():
            rows += [
                {"quarter": q, "company": ctx.names.get(entity, entity), "filers": int(counts.get((entity, q), 0))}
                for q in quarters
            ]
        last = quarters[-1]
        year_ago = f"{int(last[:4]) - 1}{last[4:]}"
        get = lambda e, q: int(counts.get((e, q), 0))
        takeaway = [
            (
                f"{get('anthropic', last)} distinct 10-K and 10-Q filers named Anthropic or a Claude product in {quarter_label(last)}, "
                f"against {get('anthropic', year_ago)} a year earlier and {get('openai', last)} naming OpenAI."
            )
        ]
    return {
        "title": "Public companies naming each lab in their filings",
        "subtitle": "Distinct 10-K and 10-Q filers whose filing names the lab, by quarter the filing was made",
        "kind": "line",
        "encoding": {
            "x": {"field": "quarter", "type": "temporal", "label": "Quarter filed"},
            "y": {"field": "filers", "type": "quantitative", "label": "Distinct filers", "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "quarter", "label": "Quarter starting", "format": "date"},
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "filers", "label": "Distinct filers", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "A filer that names Anthropic may be a customer, a supplier, an investor (Amazon, Nvidia, Zoom), a landlord or lender to it, or a competitor or litigant (News Corp, Meta). This counts mentions. It does not say who buys Claude.",
            "Anthropic = the word Anthropic, or a Claude product name (Claude Code, Sonnet, Opus, Haiku, AI, Cowork). Bare 'Claude' is not searched because it is mostly a first name; the 7 filings it adds by product name are not otherwise found. All 122 filings naming Anthropic were opened: all but 2 (one filer writing 'anthropic factors') name the company, and that filer is excluded.",
            "Quarter is the quarter the filing was made, so a 10-K lands in Q1 and the 10-Qs in Q2 to Q4. Filers with other year-ends still appear once per filing. 10-K/A and 10-Q/A count. Exhibits are ignored. Zero means no match in EDGAR full-text search, which is the only index used.",
            "xAI counts include lenders holding xAI loans, a tenant and fund holdings, all verified in the filings. Cohere and Mistral are searched only beside another lab's name (bare 'Mistral' is Mistral Equity Partners, bare 'Cohere' is Cohere Health), so their lines are floors. DeepMind shows only Alphabet, which stopped using the word after Feb 2025.",
            "Only complete quarters are shown; a quarter's late filers can still arrive a few weeks after it ends.",
        ],
        "badges": ["arithmetic"],
    }
