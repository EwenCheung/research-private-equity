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


def exact(x: float) -> str:
    """Whole number with thousands separators. Takeaways quote counts exactly, never '3.8K'."""
    return f"{x:,.0f}"


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


@mart(id="customers.hn_terms", sources=HN)
def hn_terms(ctx):
    posts, n = hn_frames(ctx)
    rows, takeaway = [], []
    if len(n):
        last = n["month"].max()
        cut = f"{int(last[:4]) - 1}{last[4:]}"  # the 12 complete months ending at `last`: strictly after `cut`
        base = posts[posts["month"] > cut]["posts"].sum()
        all_posts = posts["posts"].sum()
        for (entity, term), g in n.groupby(["entity", "term"]):
            recent, ever = g[g["month"] > cut]["naming"].sum(), g["naming"].sum()
            rows.append(
                {
                    "company": ctx.names.get(entity, entity),
                    "term": "Any of these (counted once per post)" if term == ANY else term,
                    "recent": int(recent),
                    "recent_share": recent / base,
                    "ever": int(ever),
                    "ever_share": ever / all_posts,
                    "_k": (entity != "anthropic", entity, term != ANY, -ever),
                }
            )
        rows.sort(key=lambda r: r["_k"])
        for r in rows:
            r.pop("_k")
        anyrows = {r["company"]: r for r in rows if r["term"].startswith("Any")}
        a = anyrows.get(ctx.names.get("anthropic"))
        peers = [r for c, r in anyrows.items() if c != ctx.names.get("anthropic")]
        if a and peers:
            lead = max(peers, key=lambda r: r["recent"])
            takeaway = [
                (
                    f"Over the 12 complete months to {month_label(last)}, {exact(a['recent'])} of {exact(base)} posts named Claude or Anthropic, "
                    f"against {exact(lead['recent'])} for {lead['company']} ({a['recent'] / lead['recent']:.1f}× as many)."
                )
            ]
    return {
        "title": "Hiring posts naming each lab, by term",
        "subtitle": f"Counts of top-level Who-is-hiring posts: the last 12 complete months, and every month since {month_label(posts['month'].min()) if len(posts) else 'Jan 2023'}",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "term", "label": "Term", "format": "text"},
            {"field": "recent", "label": "Posts, last 12 months", "format": "int"},
            {"field": "recent_share", "label": "Share, last 12 months", "format": "pct"},
            {"field": "ever", "label": "Posts, all months", "format": "int"},
            {"field": "ever_share", "label": "Share, all months", "format": "pct"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            MENTION_NOTE,
            "Shares divide by every top-level post in the same months, so they are comparable across terms and companies.",
            "The 'Any' row counts each post once; its count is less than the sum of the term rows when a post names both terms.",
            "Cohere and Mistral hits are rare enough that a single post moves their share.",
        ],
        "badges": ["arithmetic"],
    }


# ---- federal awards ----
@mart(id="customers.federal_awards", sources=["usaspending_awards"])
def federal_awards(ctx):
    awards = latest(
        dims(
            ctx.obs(metric="federal_award_obligation_usd"),
            "award_id",
            "agency",
            "type",
            "description",
            "recipient",
            "end",
        ),
        keys=("entity", "award_id"),
    )
    found = latest(ctx.obs(metric="federal_awards_found"), keys=("entity",))
    rows, takeaway = [], []
    for r in awards.sort_values(["as_of", "entity"], ascending=[False, True]).itertuples():
        rows.append(
            {
                "company": ctx.names.get(r.entity, r.entity),
                "recipient": r.recipient,
                "start": r.as_of,
                "agency": r.agency,
                "type": str(r.type).title(),
                "description": r.description,
                "obligated": float(r.value),
                "link": r.source_url,
            }
        )
    if len(found):
        by = {ctx.names.get(r.entity, r.entity): int(r.value) for r in found.itertuples()}
        have = {c: [x for x in rows if x["company"] == c] for c, n in by.items() if n}
        none = [c for c, n in by.items() if not n]
        parts = [
            f"{c}: {len(v)} award{'s' if len(v) > 1 else ''}, ${exact(sum(x['obligated'] for x in v))} obligated"
            for c, v in have.items()
        ]
        takeaway = [
            f"USAspending lists {'; '.join(parts) if parts else 'no awards'} under exact recipient names"
            + (f", and none for {', '.join(none)}." if none else ".")
        ]
    return {
        "title": "Federal awards to the AI labs",
        "subtitle": "Direct awards on USAspending.gov whose recipient name matches the company, every award type since Oct 2007",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "recipient", "label": "Recipient name", "format": "text"},
            {"field": "start", "label": "Start", "format": "date"},
            {"field": "agency", "label": "Awarding agency", "format": "text"},
            {"field": "type", "label": "Award type", "format": "text"},
            {"field": "description", "label": "Description", "format": "text"},
            {"field": "obligated", "label": "Obligated", "format": "usd"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Recipient names must match the company in full. USAspending's own search is a substring match: 'anthropic' also returns PHILANTHROPIC recipients, 'cohere' returns COHERENT, 'xai' returns PRAXAIR. MISTRAL INC, a defence contractor, is not Mistral AI and is excluded.",
            "Only awards made in the company's own name appear. Claude or GPT bought through a reseller or cloud marketplace, other-transaction agreements (such as the 2025 Pentagon frontier-AI prototype awards) and GSA schedule listings with no money obligated are not in this list.",
            "Obligated is the total obligated to date, not outlays. Search run on the retrieval date shown below; a company with no rows had no match that day.",
            "Google DeepMind is part of Alphabet and does not receive awards under its own name; Google's awards are not attributed to it.",
        ],
        "badges": [],
    }


# ---- company-stated customer metrics (ledger) ----
@mart(id="customers.kpi_claims", sources=["customers_kpi_claims"])
def kpi_claims(ctx):
    df = dims(ctx.obs(metric="customer_kpi_claim"), "what", "threshold").sort_values(["as_of", "value"])
    rows = [
        {
            "date": r.as_of,
            "what": r.what,
            "value": float(r.value),
            "wording": r.threshold,
            "quote": r.evidence,
            "link": r.source_url,
        }
        for r in df.itertuples()
    ]
    takeaway = []
    big = df[
        df["what"].str.contains("over \\$1M a year")
        & df["what"].str.startswith(("customers spending", "business customers spending"))
    ]
    if len(big) >= 2:
        a, b = big.iloc[0], big.iloc[-1]
        days = (date.fromisoformat(b.as_of) - date.fromisoformat(a.as_of)).days
        takeaway = [
            (
                f"Anthropic said customers spending over $1M a year went from over {exact(a.value)} on {a.as_of} to over {exact(b.value)} on {b.as_of}, "
                f"{b.value / a.value:.1f}× in {days} days; each is a floor ('exceeds'), so the true ratio is not known."
            )
        ]
    return {
        "title": "Customer numbers Anthropic has stated",
        "subtitle": "Anthropic's own newsroom, with the sentence quoted word for word",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "what", "label": "What is counted", "format": "text"},
            {"field": "value", "label": "Number stated", "format": "int"},
            {"field": "wording", "label": "As worded", "format": "text"},
            {"field": "quote", "label": "What Anthropic said", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Anthropic is private and files nothing with the SEC, so every figure is a company claim, never audited. Each quote was checked to appear word for word on its page when the ledger was built.",
            "Numbers are floors ('over', 'exceeds'). Definitions differ between rows (business customers, customers above $1M a year, Bedrock customers), so only rows with the same 'what' are comparable.",
            "Not entered because they cannot be dated or are not numbers: 'a dozen' customers above $1M 'two years ago' (Feb 2026 page), 'nearly 7x' growth in large accounts (Sep 2025), 'hundreds of thousands' of API customers (Jul 2025), and regional growth multiples for EMEA and Asia-Pacific.",
            "Gaps: the Series H announcement (28 May 2026) gives run-rate revenue but no customer count; no figure on paying consumer subscribers or Claude Code seats is stated in a form that can be quoted.",
            "Hand-maintained. Add a row with the customers-kpi-ledger skill when Anthropic states a new number.",
        ],
        "badges": ["arithmetic"] if takeaway else [],
    }


# ---- SEC filings naming the labs ----
# SIC code -> sector. First matching range wins. Divisions follow the SEC's SIC division table, with one documented
# change: the software and data-processing codes (7370-7379) are split out of Services, because they are most of the
# filers and Services would otherwise hide the answer. SEC quirks stay: 2711 (newspapers) and 3674 (semiconductors) are
# Manufacturing, 4822 (message communications) is Transportation, communications & utilities.
SECTORS = [
    ("Software & data processing", 7370, 7379),
    ("Agriculture", 100, 999),
    ("Mining", 1000, 1499),
    ("Construction", 1500, 1799),
    ("Manufacturing", 2000, 3999),
    ("Transport, comms & utilities", 4000, 4999),
    ("Wholesale", 5000, 5199),
    ("Retail", 5200, 5999),
    ("Finance, insurance & real estate", 6000, 6799),
    ("Other services", 7000, 8999),
    ("Public administration", 9100, 9999),
]
NO_SIC = "No SIC code (funds, BDCs, trusts)"


def sector_of(sic) -> str:
    if sic is None or pd.isna(sic) or not str(sic).strip().isdigit():
        return NO_SIC
    code = int(sic)
    return next((name for name, lo, hi in SECTORS if lo <= code <= hi), "Unmapped SIC")


def sec_filings(ctx) -> pd.DataFrame:
    """One row per (company, filing): the newest collection wins, and a filing hit by two terms (Anthropic and a Claude product) counts once.

    Complete quarters only: the quarter still running on the last collection day is not read at all.
    """
    peek = ctx.df[ctx.df["metric"] == "sec_filing_mention"]
    if peek.empty:
        return peek
    last = date.fromisoformat(last_collected(ctx))
    running = period_start(pd.Series([last.isoformat()]), "quarter").iloc[0]
    quarter_end = (pd.Timestamp(running) + pd.offsets.QuarterEnd(0)).date()  # a finished quarter is complete
    keep = peek if last >= quarter_end else peek[period_start(peek["as_of"], "quarter") < running]
    df = ctx.obs(metric="sec_filing_mention", as_of=keep["as_of"].unique().tolist())
    df = latest(dims(df, "adsh", "cik", "filer", "form", "sic", "term"), keys=("entity", "adsh", "term"))
    return df.drop_duplicates(["entity", "adsh"]).assign(quarter=lambda d: period_start(d["as_of"], "quarter"))


@mart(id="customers.sec_filers", sources=SEC)
def sec_filers(ctx):
    df = sec_filings(ctx)
    rows, takeaway = [], []
    if len(df):
        quarters = [
            p.start_time.date().isoformat() for p in pd.period_range(df["quarter"].min(), df["quarter"].max(), freq="Q")
        ]
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


@mart(id="customers.sec_sectors", sources=SEC)
def sec_sectors(ctx):
    df = sec_filings(ctx)
    rows, takeaway = [], []
    window = ""
    if len(df):
        last = df["quarter"].max()
        first = (pd.Timestamp(last) - pd.DateOffset(months=9)).date().isoformat()  # latest four quarters
        window = f"{quarter_label(first)} to {quarter_label(last)}"
        recent = df[df["quarter"] >= first]
        # one sector per filer: its newest SIC code (a filer's code can change, or be blank on one filing)
        newest = recent.sort_values("as_of").dropna(subset=["sic"]).drop_duplicates("cik", keep="last").set_index("cik")["sic"]
        recent = recent.assign(sector=recent["cik"].map(newest).map(sector_of))
        a, o = recent[recent["entity"] == "anthropic"], recent[recent["entity"] == "openai"]
        for sector in a["sector"].unique():
            names = a[a["sector"] == sector].drop_duplicates("cik")["filer"]
            rows.append(
                {
                    "sector": sector,
                    "anthropic": int(a[a["sector"] == sector]["cik"].nunique()),
                    "openai": int(o[o["sector"] == sector]["cik"].nunique()),
                    "examples": ", ".join(sorted(names, key=str.lower)[:4]),
                }
            )
        rows.sort(key=lambda r: -r["anthropic"])
        total = int(a["cik"].nunique())
        if rows:
            top = rows[0]
            takeaway = [
                f"{total} distinct filers named Anthropic or a Claude product in {window}; {top['sector']} is the largest sector with {top['anthropic']} ({pct(top['anthropic'] / total)})."
            ]
    return {
        "title": "Which sectors name Anthropic",
        "subtitle": f"Distinct 10-K and 10-Q filers naming Anthropic or a Claude product, latest four quarters ({window or 'awaiting data'}), by SIC sector",
        "kind": "bar",
        "encoding": {
            "x": {"field": "sector", "type": "nominal", "label": "Sector"},
            "y": {
                "field": "anthropic",
                "type": "quantitative",
                "label": "Distinct filers naming Anthropic",
                "format": "int",
            },
        },
        "columns": [
            {"field": "sector", "label": "Sector", "format": "text"},
            {"field": "anthropic", "label": "Naming Anthropic", "format": "int"},
            {"field": "openai", "label": "Naming OpenAI", "format": "int"},
            {"field": "examples", "label": "Examples naming Anthropic", "format": "text"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Sector comes from the filer's SIC code on EDGAR (first listed). Divisions follow the SEC table; software and data processing (7370-7379) are split out of Services because they are most of the filers. Newspapers (2711) and semiconductors (3674) are Manufacturing under the SEC's own table.",
            "Filers with no SIC code (funds, BDCs, trusts) are their own row. SIC codes describe what a company mostly does, not how it uses Claude.",
            "A company counts once however many of the four quarters it names Anthropic. It may be a customer, an investor, a supplier or a rival; mentions are not customers.",
            "OpenAI is shown in the table for comparison; the bars are Anthropic only.",
        ],
        "badges": ["arithmetic"],
    }
