"""Capital and valuation charts. Every number is a cited ledger row, a field of an SEC filing, or arithmetic on those with the formula shown."""

import re

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest

COMPANY_TIER, PRESS_TIER = "Company-stated", "Press-reported"
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
SERIES = re.compile(r"(?:SERIES|SER|CL|CLASS)\.?\s*([A-H])[ -]?(\d)?\b", re.IGNORECASE)


# ---- the ledgers as tables ----


# ---- rounds and valuation ----


# ---- run-rate ----


# ---- Form D ----


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
        "title": "Value per share reported by each fund",
        "subtitle": "Each dot is one fund's reported value per share (value ÷ shares from its SEC N-PORT filing), by report date and share series",
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


# ---- strategic holders ----
