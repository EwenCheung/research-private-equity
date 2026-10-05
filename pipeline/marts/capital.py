"""Capital and valuation charts. Every number is a cited ledger row, a field of an SEC filing, or arithmetic on those with the formula shown."""

import math
import re
from datetime import UTC, date, datetime

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest, period_start, usd

COMPANY_TIER, PRESS_TIER = "Company-stated", "Press-reported"
PRESS_MAX_GAP_DAYS = 120  # a press run-rate further than this from a round is not used for that round's multiple
NEW_VEHICLE, AMENDMENT = "New vehicle (Form D)", "Amendment (Form D/A)"
FAMILIES = [  # registrant name starts with / contains -> the fund family a reader knows
    (r"^(fidelity|variable insurance products)", "Fidelity"),
    (r"t\.? rowe price", "T. Rowe Price"),
    (r"^blackrock", "BlackRock"),
    (r"^ark venture", "ARK"),
    (r"^alger", "Alger"),
    (
        r"^(american funds|amcap|american balanced|capital world|growth fund of america|new economy fund)",
        "Capital Group (American Funds)",
    ),
    (r"^(new york life|nylim)", "New York Life"),
    (r"^franklin", "Franklin"),
    (r"^nuveen", "Nuveen"),
    (r"^j\.?p\.? morgan", "J.P. Morgan"),
    (r"^legg mason", "Legg Mason (ClearBridge)"),
    (r"^krane", "KraneShares"),
    (r"^brighthouse", "Brighthouse"),
    (r"^jnl", "JNL"),
]
INSTRUMENTS = {
    "convertible notes": "Convertible notes",
    "nonvoting preferred stock": "Nonvoting preferred stock",
    "convertible notes and nonvoting preferred stock (combined)": "Notes and preferred (combined, no split)",
}
SERIES = re.compile(r"(?:SERIES|SER|CL|CLASS)\.?\s*([A-H])[ -]?(\d)?\b", re.IGNORECASE)


def money(x: float) -> str:
    """$61.5B, $965B, $190.4B, $87M: billions keep one decimal so a takeaway never rounds away tenths."""
    return "$" + f"{x / 1e9:,.1f}".removesuffix(".0") + "B" if abs(x) >= 1e9 else usd(x)


def day(iso: str) -> date:
    return date.fromisoformat(iso)


def gap_days(a: str, b: str) -> int:
    """Days from a to b (negative when b is earlier)."""
    return (day(b) - day(a)).days


def quarter_label(iso: str) -> str:
    d = day(iso)
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def month_label(iso: str) -> str:
    return day(iso).strftime("%b %Y")


def last_collected(ctx) -> str:
    return str(ctx.df["retrieved_at"].max())[:10] if len(ctx.df) else datetime.now(UTC).date().isoformat()


# ---- the ledgers as tables ----
def rounds(ctx) -> pd.DataFrame:
    """One row per round: date, round, amount, post_money (NaN when Anthropic states none), leads, quote, link."""
    df = ctx.obs(metric=["round_amount_usd", "round_post_money_usd"])
    if df.empty:
        return df
    df = latest(dims(df, "round", "leads", "co_leads"), keys=("metric", "as_of", "round"))
    amount = df[df["metric"] == "round_amount_usd"].set_index("round")
    post = df[df["metric"] == "round_post_money_usd"].set_index("round")["value"]
    out = pd.DataFrame(
        {
            "date": amount["as_of"],
            "amount": amount["value"].astype(float),
            "post_money": post.reindex(amount.index).astype(float),
            "leads": amount["leads"],
            "co_leads": amount["co_leads"].fillna(""),
            "quote": amount["evidence"],
            "link": amount["source_url"],
        }
    )
    return out.reset_index().sort_values("date").reset_index(drop=True)


def run_rates(ctx, sources: dict[str, str]) -> pd.DataFrame:
    """Company-scope run-rate statements from the named sources: date, tier, run_rate, qualifier, stated_on, quote, link."""
    df = ctx.obs(metric="run_rate_usd")
    if df.empty:
        return df
    df = dims(df, "scope", "qualifier", "stated_on", "published")
    df = latest(df[df["scope"] == "company"], keys=("source", "as_of", "value"))
    df = df[df["source"].isin(sources)]
    return (
        pd.DataFrame(
            {
                "date": df["as_of"],
                "tier": df["source"].map(sources),
                "run_rate": df["value"].astype(float),
                "qualifier": df["qualifier"],
                "stated_on": df["stated_on"].fillna(df["published"]),
                "quote": df["evidence"],
                "link": df["source_url"],
            }
        )
        .sort_values("date")
        .reset_index(drop=True)
    )


def nearest(date_: str, points: pd.DataFrame) -> pd.Series | None:
    """The run-rate point closest in time to date_, the earlier one on a tie; None without points."""
    if points.empty:
        return None
    return points.loc[points["date"].map(lambda d: abs(gap_days(date_, d))).idxmin()]


# ---- rounds and valuation ----
@mart(id="capital.rounds", sources=["capital_funding_rounds"])
def rounds_table(ctx):
    df = rounds(ctx)
    rows, takeaway = [], []
    for r in df.itertuples():
        led = r.leads.replace("; ", ", ") + (f"; co-led by {r.co_leads.replace('; ', ', ')}" if r.co_leads else "")
        rows.append(
            {
                "date": r.date,
                "round": r.round,
                "amount": r.amount,
                "post_money": None if pd.isna(r.post_money) else r.post_money,
                "led_by": led,
                "quote": r.quote,
                "link": r.link,
            }
        )
    if len(df):
        priced = df.dropna(subset=["post_money"])
        last = df.iloc[-1]
        takeaway = [
            f"Anthropic announced {len(df)} funding rounds from {df['date'].iloc[0][:4]} to {last['date'][:4]}, {money(df['amount'].sum())} in all; "
            f"the latest is {last['round']} on {last['date']}: {money(last['amount'])} at {money(last['post_money'])} post-money."
            if len(priced)
            else f"Anthropic announced {len(df)} funding rounds."
        ]
    return {
        "title": "Anthropic funding rounds",
        "subtitle": "Every round Anthropic announced itself, with the amount, the lead investors and the sentence quoted",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "date", "label": "Announced", "format": "date"},
            {"field": "round", "label": "Round", "format": "text"},
            {"field": "amount", "label": "Raised", "format": "usd_compact"},
            {"field": "post_money", "label": "Post-money valuation", "format": "usd_compact"},
            {"field": "led_by", "label": "Led by", "format": "text"},
            {"field": "quote", "label": "What Anthropic said", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Only rounds Anthropic announced on anthropic.com/news. Series A to C announcements state no valuation, so those cells are blank, not zero.",
            (
                "Series D (reported by the press as led by Menlo Ventures) was never announced by Anthropic. The press pages found say only that it was "
                "'in talks' (CNBC, 2023-12-21) or give a rounded $18 billion with no close date (TechCrunch, 2026-06-23), so it is left out until a dated, "
                "citable source is found."
            ),
            (
                "Series G includes a portion of the Microsoft and NVIDIA investments announced in November 2025; Series H includes $15 billion "
                "previously committed by hyperscalers, of which $5 billion from Amazon. Amounts are as Anthropic states them."
            ),
            "Anthropic confidentially submitted a draft S-1 on 2026-06-01 (anthropic.com/news/confidential-draft-s1-sec). No S-1 is public on EDGAR yet.",
        ],
        "badges": [],
    }


@mart(id="capital.valuation", sources=["capital_funding_rounds", "capital_arr_milestones", "capital_arr_press"])
def valuation(ctx):
    df = rounds(ctx)
    if len(df):
        df = df.dropna(subset=["post_money"])
    company = run_rates(ctx, {"capital_arr_milestones": COMPANY_TIER})
    press = run_rates(ctx, {"capital_arr_press": PRESS_TIER})
    rows, takeaway = [], []
    for r in df.itertuples():
        row = {
            "date": r.date, "round": r.round, "post_money": r.post_money, "run_rate": None, "run_rate_date": None,
            "gap_days": None, "multiple": None, "formula": "no company-stated run-rate on file",
            "press_run_rate": None, "press_gap_days": None, "press_multiple": None,
        }  # fmt: skip
        if (c := nearest(r.date, company)) is not None:
            m = r.post_money / c["run_rate"]
            row |= {
                "run_rate": c["run_rate"], "run_rate_date": c["date"], "gap_days": gap_days(r.date, c["date"]), "multiple": m,
                "formula": f"{money(r.post_money)} post-money ÷ {money(c['run_rate'])} run-rate = {m:.1f}x",
            }  # fmt: skip
        if (p := nearest(r.date, press)) is not None and abs(gap_days(r.date, p["date"])) <= PRESS_MAX_GAP_DAYS:
            row |= {
                "press_run_rate": p["run_rate"],
                "press_gap_days": gap_days(r.date, p["date"]),
                "press_multiple": r.post_money / p["run_rate"],
            }
        rows.append(row)
    if len(rows) >= 2:
        first, last = rows[0], rows[-1]
        takeaway = [
            (
                f"Post-money rose {last['post_money'] / first['post_money']:.1f}x from {money(first['post_money'])} ({first['round']}, {month_label(first['date'])}) "
                f"to {money(last['post_money'])} ({last['round']}, {month_label(last['date'])})."
            )
        ]
        if first["multiple"] and last["multiple"]:
            takeaway.append(
                f"Against the nearest company-stated run-rate the multiple went from {first['multiple']:.1f}x to {last['multiple']:.1f}x."
            )
    return {
        "title": "Valuation at each round, and the multiple of run-rate",
        "subtitle": "Post-money valuation Anthropic announced at each round (dots, not interpolated); table view shows post-money ÷ the run-rate nearest in time",
        "kind": "scatter",
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Round announced"},
            "y": {
                "field": "post_money",
                "type": "quantitative",
                "label": "Post-money valuation",
                "format": "usd_compact",
            },
        },
        "columns": [
            {"field": "date", "label": "Round announced", "format": "date"},
            {"field": "round", "label": "Round", "format": "text"},
            {"field": "post_money", "label": "Post-money", "format": "usd_compact"},
            {"field": "run_rate", "label": "Run-rate (company)", "format": "usd_compact"},
            {"field": "run_rate_date", "label": "Run-rate date", "format": "date"},
            {"field": "gap_days", "label": "Gap, days (run-rate date − round date)", "format": "int"},
            {"field": "multiple", "label": "Multiple", "format": "multiple"},
            {"field": "formula", "label": "Formula", "format": "text"},
            {
                "field": "press_run_rate",
                "label": "Run-rate (press)",
                "format": "usd_compact",
            },
            {"field": "press_gap_days", "label": "Press gap, days", "format": "int"},
            {"field": "press_multiple", "label": "Press multiple", "format": "multiple"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            (
                "Multiple = post-money valuation ÷ run-rate revenue, using the run-rate statement nearest in time to the round. The date gap is shown "
                "because a run-rate that is months away from the round changes the multiple a lot."
            ),
            "A post-money valuation is an equity value, not an enterprise value: Anthropic's cash and debt are not public, so this is not an EV / run-rate multiple.",
            "Run-rates are 'approximately', 'over' or 'crossed' figures, so each multiple is approximate, and an 'over' figure makes it an upper bound.",
            "Press columns use only press-reported run-rates within 120 days of the round, kept apart from the company-stated ones.",
            "Peer valuations (OpenAI, Google DeepMind, xAI, Mistral, Cohere) are not in a cited ledger yet, so no peer multiple is shown. This is a gap.",
        ],
        "badges": ["arithmetic"],
    }


# ---- run-rate ----
@mart(id="capital.run_rate", sources=["capital_arr_milestones", "capital_arr_press"])
def run_rate(ctx):
    company = run_rates(ctx, {"capital_arr_milestones": COMPANY_TIER})
    press = run_rates(ctx, {"capital_arr_press": PRESS_TIER})
    rows = []
    prev = None
    for r in company.itertuples():
        growth = cagr = doubling = None
        if prev is not None and r.run_rate != prev.run_rate and gap_days(prev.date, r.date) > 0:
            days, mult = gap_days(prev.date, r.date), r.run_rate / prev.run_rate
            growth, cagr = mult, mult ** (365 / days) - 1
            doubling = days * math.log(2) / math.log(mult) if mult > 1 else None
        if prev is None or r.run_rate != prev.run_rate:
            prev = r
        rows.append(row_of(r, growth, cagr, doubling))
    rows += [row_of(r) for r in press.itertuples()]
    rows.sort(key=lambda r: (r["tier"] != COMPANY_TIER, r["date"]))
    takeaway = []
    if len(company) >= 2:
        a, b = company.iloc[0], company.iloc[-1]
        days, mult = gap_days(a["date"], b["date"]), b["run_rate"] / a["run_rate"]
        takeaway = [
            (
                f"Anthropic's own run-rate statements go from {money(a['run_rate'])} ({a['date']}) to {money(b['run_rate'])} ({b['date']}): "
                f"{mult:,.0f}x in {days} days, which is {mult ** (365 / days):.1f}x a year (CAGR) and doubling every {days * math.log(2) / math.log(mult):.0f} days."
            )
        ]
    extras = []
    cc = ctx.obs(metric="run_rate_usd")
    cc = dims(cc, "scope")
    cc = cc[cc["scope"] == "Claude Code"].sort_values("as_of")
    if len(cc):
        extras.append(
            "Claude Code run-rate statements (a product line, not charted): "
            + "; ".join(f"{money(r.value)} on {r.as_of}" for r in cc.itertuples())
            + "."
        )
    rev = ctx.obs(metric="revenue_usd")
    rev = dims(rev, "period").sort_values("as_of")
    if len(rev):
        extras.append(
            "Press-reported recognised revenue (not a run-rate): "
            + "; ".join(f"{r.period} {money(r.value)}" for r in rev.itertuples())
            + "."
        )
    return {
        "title": "Anthropic run-rate revenue, as stated",
        "subtitle": "Each dot is one statement of annualised revenue: Anthropic's own, and press reports kept as a separate series. Nothing is drawn between dots",
        "kind": "scatter",
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Date the figure describes"},
            "y": {
                "field": "run_rate",
                "type": "quantitative",
                "label": "Run-rate revenue (USD per year)",
                "format": "usd_compact",
            },
            "color": {"field": "tier", "type": "nominal", "label": "Who said it"},
        },
        "columns": [
            {"field": "date", "label": "Date described", "format": "date"},
            {"field": "tier", "label": "Who said it", "format": "text"},
            {"field": "run_rate", "label": "Run-rate", "format": "usd"},
            {"field": "qualifier", "label": "Wording", "format": "text"},
            {"field": "stated_on", "label": "Stated on", "format": "date"},
            {"field": "growth", "label": "× previous company-stated figure", "format": "multiple"},
            {"field": "cagr", "label": "Annualised growth (CAGR)", "format": "pct"},
            {"field": "doubling_days", "label": "Doubling time (days)", "format": "int"},
            {"field": "quote", "label": "What was said", "format": "text"},
            {"field": "link", "label": "Source", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            (
                "Growth columns are arithmetic on Anthropic's own statements only: × = this figure ÷ the previous different figure; "
                "CAGR = (this ÷ previous)^(365 ÷ days between them) − 1; doubling time = days × ln 2 ÷ ln(this ÷ previous). Press rows have none."
            ),
            "A run-rate is a company's annualised projection from a recent period, not recognised revenue. Words like 'over', 'surpassed' and 'crossed' make a figure a floor.",
            "Short gaps magnify CAGR: a 43-day gap that grows 1.6x reads as roughly 40x a year. Read the × column first.",
            "Dates: 'start of 2024' is 2024-01-01; 'end of 2025' is 2025-12-31; 'August 2025' uses the month end; other statements use the date they were made (see the wording and the quote).",
            "The press series is not company-confirmed except where the outlet says so, and stays visibly separate. Nothing is interpolated between points.",
            *extras,
        ],
        "badges": ["arithmetic"],
    }


def row_of(r, growth=None, cagr=None, doubling=None) -> dict:
    return {
        "date": r.date, "tier": r.tier, "run_rate": r.run_rate, "qualifier": r.qualifier, "stated_on": r.stated_on,
        "growth": growth, "cagr": cagr, "doubling_days": None if doubling is None else round(doubling), "quote": r.quote, "link": r.link,
    }  # fmt: skip


# ---- Form D ----
@mart(id="capital.form_d", sources=["sec_form_d"])
def form_d(ctx):
    df = ctx.obs(metric="form_d_filing")
    rows, takeaway = [], []
    if len(df):
        df = latest(dims(df, "adsh", "form", "issuer_class"), keys=("adsh",))
        sold = latest(dims(ctx.obs(metric="form_d_amount_sold_usd"), "adsh"), keys=("adsh",)).set_index("adsh")["value"]
        df = df.assign(
            sold=df["adsh"].map(sold),
            kind=df["form"].map(lambda f: NEW_VEHICLE if f == "D" else AMENDMENT),
            quarter=period_start(df["as_of"], "quarter"),
        )
        current = period_start(pd.Series([last_collected(ctx)]), "quarter").iloc[0]
        done = df[df["quarter"] < current]
        grouped = (
            done.groupby(["quarter", "kind"])
            .agg(filings=("adsh", "size"), amount_sold=("sold", lambda values: values.sum(min_count=1)))
            .reset_index()
        )
        for r in grouped.itertuples():
            rows.append(
                {
                    "quarter": f"{r.quarter[:4]} {quarter_label(r.quarter)[:2]}",  # '2026 Q3': reads without a year tick
                    "kind": r.kind,
                    "filings": int(r.filings),
                    "amount_sold": float(r.amount_sold) if r.kind == NEW_VEHICLE and pd.notna(r.amount_sold) else None,
                }
            )
        own = int((df["issuer_class"] == "anthropic_own").sum())
        new = done[done["kind"] == NEW_VEHICLE].groupby("quarter").size()
        last_q = new.index.max() if len(new) else None
        year_ago = f"{int(last_q[:4]) - 1}{last_q[4:]}" if last_q else None
        takeaway = [
            f"{len(df)} Form D filings name Anthropic since {df['as_of'].min()[:4]}: {own} filed by Anthropic itself and {len(df) - own} by vehicles other firms set up to hold its shares."
        ]
        if last_q:
            takeaway.append(
                f"{int(new.get(last_q, 0))} new vehicles filed in {quarter_label(last_q)}, against {int(new.get(year_ago, 0))} in {quarter_label(year_ago)}."
            )
    return {
        "title": "SEC Form D filings that name Anthropic, per quarter",
        "subtitle": "Third-party vehicles only (none is Anthropic's own raise): a proxy for secondary-market demand for its shares",
        "kind": "stacked_bar",
        "encoding": {
            "x": {"field": "quarter", "type": "ordinal", "label": "Quarter filed"},
            "y": {"field": "filings", "type": "quantitative", "label": "Form D filings", "format": "int"},
            "color": {"field": "kind", "type": "nominal", "label": "Filing"},
        },
        "columns": [
            {"field": "quarter", "label": "Quarter filed", "format": "text"},
            {"field": "kind", "label": "Filing", "format": "text"},
            {"field": "filings", "label": "Filings", "format": "int"},
            {"field": "amount_sold", "label": "Amount sold when filed (new vehicles)", "format": "usd_compact"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Separation: a filing is Anthropic's own only if its issuer is Anthropic or Anthropic PBC; every filer found is a special-purpose vehicle (Hiive, HII, Linqto, CGF2021 series and others) that holds or plans to hold Anthropic shares.",
            "These filings are not Anthropic's funding. Anthropic's rounds are in the funding ledger above; no Form D by Anthropic itself was found in EDGAR full-text search.",
            "A name containing 'Anthropic' does not prove the vehicle holds Anthropic PBC shares, and vehicles are not the only secondary-market activity.",
            "Amounts are what each vehicle stated it had sold when it filed, not verified, and an amendment repeats the same raise, so only new-vehicle amounts are summed.",
            "Complete quarters only. A few filings list a first sale before the filing date; the chart uses the filing date.",
        ],
        "badges": [],
    }


# ---- N-PORT fund marks ----
def family_of(reg_name: str) -> str:
    return next((fam for pat, fam in FAMILIES if re.search(pat, reg_name.strip().lower())), reg_name.strip().title())


def series_of(title: str) -> str:
    """'ANTHROPIC PBC SER F-1 CVT PFD PP' -> 'Series F-1'; 'Anthropic PBC' -> 'Unspecified'."""
    m = SERIES.search(title)
    return f"Series {m.group(1).upper()}" + (f"-{m.group(2)}" if m.group(2) else "") if m else "Unspecified"


def marks(ctx) -> pd.DataFrame:
    """One row per fund, security and report date: shares, value and mark per share, for direct holdings of Anthropic shares only."""
    df = ctx.obs(metric=["nport_value_usd", "nport_units"])
    if df.empty:
        return df
    df = dims(
        df,
        "adsh",
        "line",
        "fund_family",
        "fund",
        "series_id",
        "security",
        "name",
        "units_type",
        "issuer_category",
        "filed",
    )
    df = latest(df, keys=("adsh", "line", "metric"))
    # Shares of Anthropic itself (units type NS): not units of a vehicle that holds it, not 'economic exposure' lines
    direct = (df["units_type"] == "NS") & ~df["name"].str.contains("economic exposure", case=False)
    df = df[direct].assign(series_id=lambda d: d["series_id"].fillna(""))
    keys = ["adsh", "as_of", "fund_family", "fund", "series_id", "security", "filed"]
    wide = df.pivot_table(
        index=keys, columns="metric", values="value", aggfunc="sum"
    ).reset_index()  # lots in one filing add up
    wide = wide[(wide["nport_units"] > 0) & (wide["nport_value_usd"] > 0)]
    wide = wide.assign(fund_key=wide["series_id"].where(wide["series_id"] != "", wide["fund"].str.upper()))
    # Some funds re-file a period months later with the same holdings: keep the latest filing so a mark is never counted twice
    wide = wide.sort_values("filed").drop_duplicates(["fund_key", "as_of", "security"], keep="last")
    return (
        wide.assign(
            family=wide["fund_family"].map(family_of),
            series=wide["security"].map(series_of),
            mark=wide["nport_value_usd"] / wide["nport_units"],
        )
        .sort_values(["as_of", "family", "fund"])
        .reset_index(drop=True)
    )


@mart(id="capital.fund_marks", sources=["sec_nport_marks"])
def fund_marks(ctx):
    df = marks(ctx)
    rows, takeaway = [], []
    for r in df.itertuples():
        rows.append(
            {
                "date": r.as_of, "family": r.family, "fund": r.fund, "series": r.series.split("-")[0], "security": r.security,
                "shares": float(r.nport_units), "value": float(r.nport_value_usd), "mark": float(r.mark), "filed": r.filed,
            }
        )  # fmt: skip
    if len(df):
        last = df["as_of"].max()
        at = df[df["as_of"] == last]
        takeaway = [
            (
                f"{df['fund_key'].nunique()} funds from {df['family'].nunique()} fund families have reported holding Anthropic shares; at {last}, "
                f"{at['fund_key'].nunique()} reported marks from ${at['mark'].min():,.2f} to ${at['mark'].max():,.2f} per share across series "
                f"(median ${at['mark'].median():,.2f})."
            )
        ]
    return {
        "title": "What mutual funds mark one Anthropic share at",
        "subtitle": "Each dot is one fund's N-PORT mark: reported value ÷ reported shares, by report date and Anthropic preferred series",
        "kind": "scatter",
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Report date"},
            "y": {"field": "mark", "type": "quantitative", "label": "Mark, USD per share", "format": "float"},
            "color": {"field": "series", "type": "nominal", "label": "Preferred series"},
        },
        "columns": [
            {"field": "date", "label": "Report date", "format": "date"},
            {"field": "family", "label": "Fund family", "format": "text"},
            {"field": "fund", "label": "Fund", "format": "text"},
            {"field": "series", "label": "Series", "format": "text"},
            {"field": "security", "label": "Security as filed", "format": "text"},
            {"field": "shares", "label": "Shares", "format": "int"},
            {"field": "value", "label": "Value (USD)", "format": "usd"},
            {"field": "mark", "label": "Mark per share (USD)", "format": "float"},
            {"field": "filed", "label": "Filed", "format": "date"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Mark per share = valUSD ÷ shares as reported in the fund's N-PORT-P. Only the lines naming Anthropic are kept; the funds' other holdings are not stored.",
            "These are the funds' own fair-value estimates of restricted private shares (level 3). Funds report different dates because their fiscal years differ.",
            "Preferred series (B, D, E, F, G, H and their -1 variants) have different prices at the same date, so compare marks within one series. '-1' variants are drawn with their series; the table keeps the exact security.",
            "No implied valuation is shown. Anthropic's share count is not public, and no share count is cited in the ledgers, so price per share cannot be turned into a company value.",
            "Left out: units of vehicles that hold Anthropic (for example Coatue Innovative Strategies Fund's private-fund units), 'economic exposure' lines, and a different company, Anthropics Technology Ltd.",
        ],
        "badges": [],
    }


@mart(id="capital.fund_mark_changes", sources=["sec_nport_marks", "capital_funding_rounds"])
def fund_mark_changes(ctx):
    df, rnd = marks(ctx), rounds(ctx)
    rows, takeaway = [], []
    if len(df) and len(rnd):
        last_round = rnd.iloc[-1]
        single = 0
        for (_, series), g in df.groupby(["fund_key", "series"]):
            if len(g) < 2:
                single += 1
                continue
            g = g.sort_values("as_of")
            near = g.loc[g["as_of"].map(lambda d: abs(gap_days(last_round["date"], d))).idxmin()]
            end = g.iloc[-1]
            rows.append(
                {
                    "family": end["family"], "fund": end["fund"], "series": series, "round_mark_date": near["as_of"],
                    "round_mark_gap_days": gap_days(last_round["date"], near["as_of"]), "round_mark": float(near["mark"]),
                    "latest_date": end["as_of"], "latest_mark": float(end["mark"]),
                    # the latest mark IS the nearest one for some funds: no later mark exists to compare, so no change is shown
                    "change": None if near["as_of"] == end["as_of"] else float(end["mark"] / near["mark"] - 1),
                }
            )  # fmt: skip
        rows.sort(key=lambda r: (r["change"] is None, r["change"] or 0, r["family"], r["fund"]))
        moved = pd.Series([r["change"] for r in rows if r["change"] is not None], dtype=float)
        if len(moved):
            takeaway = [
                (
                    f"Since the mark nearest {last_round['round']} ({last_round['date']}), {int((moved.abs() < 0.01).sum())} of {len(moved)} fund lines with a later mark are within 1%, "
                    f"{int((moved <= -0.01).sum())} are lower and {int((moved >= 0.01).sum())} higher; changes run from {moved.min() * 100:+.0f}% to {moved.max() * 100:+.0f}%."
                )
            ]
        skipped_note = (
            f"{len(rows) - len(moved)} fund lines have their latest mark as the one nearest the round, so they show no change. "
            f"{single} lines with a single mark are left out."
        )
    else:
        skipped_note = "No marks or no rounds yet."
    return {
        "title": "Fund marks since the last round",
        "subtitle": "Each fund's latest mark per share against its own mark nearest the last funding round",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "family", "label": "Fund family", "format": "text"},
            {"field": "fund", "label": "Fund", "format": "text"},
            {"field": "series", "label": "Series", "format": "text"},
            {"field": "round_mark_date", "label": "Mark nearest the round (date)", "format": "date"},
            {"field": "round_mark_gap_days", "label": "Days from the round", "format": "int"},
            {"field": "round_mark", "label": "Mark then (USD/share)", "format": "float"},
            {"field": "latest_date", "label": "Latest mark (date)", "format": "date"},
            {"field": "latest_mark", "label": "Latest mark (USD/share)", "format": "float"},
            {"field": "change", "label": "Change", "format": "pct"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            (
                "Change = latest mark ÷ the fund's mark nearest the last round in the funding ledger − 1, for the same fund and the same preferred series. "
                "'Nearest' is by report date, the earlier one on a tie; the gap in days is shown."
            ),
            "The mark nearest the round can still be weeks before or after it, and the funds' reports predate or postdate Anthropic's announcement.",
            skipped_note,
            "Marks are the funds' own estimates, not transactions. No implied company valuation is derived (the share count is not public).",
        ],
        "badges": ["arithmetic"],
    }


# ---- strategic holders ----
@mart(id="capital.amazon", sources=["capital_strategic_holders"])
def amazon(ctx):
    df = ctx.obs(metric="holder_value_usd")
    inv = ctx.obs(metric="holder_invested_usd")
    rows, takeaway = [], []
    if len(df):
        df = latest(dims(df, "holder", "instrument", "basis"), keys=("as_of", "instrument"))
        rows = [
            {
                "date": r.as_of,
                "instrument": INSTRUMENTS.get(r.instrument, r.instrument),
                "value": float(r.value),
                "basis": r.basis,
                "quote": r.evidence,
                "link": r.source_url,
            }
            for r in df.sort_values(["as_of", "instrument"]).itertuples()
        ]
        both = df.groupby("as_of")["instrument"].agg(
            lambda s: {"convertible notes", "nonvoting preferred stock"} <= set(s)
        )
        dates = [d for d, ok in both.items() if ok]
        if len(dates) >= 2:
            tot = (
                df[df["instrument"].isin(["convertible notes", "nonvoting preferred stock"])]
                .groupby("as_of")["value"]
                .sum()
            )
            a, b = dates[0], dates[-1]
            takeaway = [
                f"Amazon records {money(tot[b])} for its Anthropic convertible notes and nonvoting preferred stock at {b}, against {money(tot[a])} at {a}."
            ]
            if len(inv):
                cash = float(latest(inv, keys=("as_of", "value", "evidence"))["value"].sum())
                takeaway.append(
                    f"It reports investing {money(cash)} in cash since 2023, so the carrying value is {tot[b] / cash:.1f}x the cash invested."
                )
    return {
        "title": "Amazon's reported stake in Anthropic",
        "subtitle": "Convertible notes at estimated fair value and nonvoting preferred stock at the amount Amazon records, from its 10-K and 10-Q",
        "kind": "stacked_bar",
        "encoding": {
            "x": {"field": "date", "type": "ordinal", "label": "Balance-sheet date"},
            "y": {"field": "value", "type": "quantitative", "label": "Amount recorded (USD)", "format": "usd_compact"},
            "color": {"field": "instrument", "type": "nominal", "label": "Instrument"},
        },
        "columns": [
            {"field": "date", "label": "Balance-sheet date", "format": "date"},
            {"field": "instrument", "label": "Instrument", "format": "text"},
            {"field": "value", "label": "Amount recorded", "format": "usd_compact"},
            {"field": "basis", "label": "Basis", "format": "text"},
            {"field": "quote", "label": "What the filing says", "format": "text"},
            {"field": "link", "label": "Filing", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Notes are at estimated fair value (level 3); the preferred stock is the amount Amazon records, adjusted for observable price changes. They are different bases, so the stack shows what Amazon reports, not one valuation method.",
            "At 2025-06-30 Amazon gave one combined figure for notes and preferred without a split, so that bar is a single series; at 2024-12-31 only the notes are given.",
            "Amazon does not disclose a share count or an ownership percentage, so no stake percentage or implied company valuation is computed. It says only that conversions on a liquidity event are subject to an ownership cap.",
            "Cash invested is the sum of the quarterly investments Amazon states ($8.0 billion in notes through Q4 2025, then $10.0 billion of preferred in Q2 2026); the filings' own $8.0 billion note total matches.",
            "Alphabet's 10-K and 10-Q filings never name Anthropic (checked 2023 to 2026), so there is no Alphabet row. This is a gap, not a zero.",
            "Amazon also states a financing facility of up to $20.0 billion ($15.0 billion available after it took $5.0 billion of Series H); see the ledger.",
        ],
        "badges": ["arithmetic"] if len(rows) else [],
    }
