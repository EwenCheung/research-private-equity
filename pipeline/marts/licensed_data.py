"""Licensed alt-data charts: one set per vendor, empty ("awaiting data") until figures are entered by hand."""

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import change, dims, num, usd

VENDORS = {"yipit_consumer": "YipitData", "mscience_panel": "M Science"}
PANELS = (  # metric, chart id suffix, title, y label, y format, what the takeaway counts
    ("panel_spend_usd", "spend", "weekly spend", "Spend per week", "usd_compact"),
    ("panel_users", "users", "weekly users", "Users", "int"),
    ("panel_growth_yoy", "growth", "year-on-year growth", "Growth vs a year earlier", "pct"),
)
PANEL_NOTE = (
    "A panel figure is the vendor's projection from a sample of cards or receipts: an estimate of consumer or business spend, "
    "not the company's revenue, and it carries the vendor's own sampling error."
)


def series_label(ctx, entity: str, product) -> str:
    name = ctx.names.get(entity, entity)
    return f"{name} · {product}" if isinstance(product, str) and product else name


def fmt_value(metric: str, v: float) -> str:
    return usd(v) if metric == "panel_spend_usd" else f"{v * 100:.0f}%" if metric == "panel_growth_yoy" else num(v)


def make_chart(source_id: str, vendor: str, metric: str, suffix: str, noun: str, y_label: str, y_format: str):
    @mart(id=f"licensed_data.{source_id}_{suffix}", sources=[source_id])
    def chart(ctx):
        df = dims(ctx.obs(metric=metric), "product")
        rows, takeaway = [], []
        if len(df):
            df = df.assign(product=df["product"].where(df["product"].notna(), None))
            t = pd.to_datetime(df["retrieved_at"], utc=True, format="ISO8601")
            df = (
                df.assign(_t=t)
                .sort_values("_t")
                .drop_duplicates(["entity", "product", "as_of"], keep="last")
                .sort_values(["as_of", "entity"])
            )
            rows = [
                {
                    "week": r.as_of,
                    "series": series_label(ctx, r.entity, r.product),
                    "value": float(r.value),
                    "entered_by": r.entered_by,
                    "evidence": r.evidence,
                }
                for r in df.itertuples()
            ]
            a = df[df["entity"] == "anthropic"]
            if len(a):
                last_week = a["as_of"].max()
                latest_rows = a[a["as_of"] == last_week]
                head = latest_rows.iloc[0]
                label = series_label(ctx, "anthropic", head["product"])
                prior = a[
                    (a["as_of"] <= (pd.Timestamp(last_week) - pd.Timedelta(days=27)).date().isoformat())
                    & (a["product"].fillna("") == (head["product"] if isinstance(head["product"], str) else ""))
                ]
                text = f"{vendor}'s panel puts {label}'s {noun} at {fmt_value(metric, head['value'])} in the week of {last_week}"
                if len(prior) and metric != "panel_growth_yoy":
                    text += (
                        f", {change(head['value'], prior.iloc[-1]['value'])} on the week of {prior.iloc[-1]['as_of']}"
                    )
                takeaway = [text + f". Last entered {head['retrieved_at'][:10]} by {head['entered_by']}."]
            else:
                takeaway = [
                    f"{vendor} figures are entered for {len(df['entity'].unique())} companies but none for Anthropic yet."
                ]
        else:
            takeaway = [
                f"Awaiting data: no {vendor} figures have been entered yet. Enter them with the /add-manual-data skill in Claude Code."
            ]
        return {
            "title": f"{vendor}: {noun}",
            "subtitle": f"Entered by hand from {vendor}'s report. Licensed: keep inside the team.",
            "kind": "line",
            "encoding": {
                "x": {"field": "week", "type": "temporal", "label": "Week"},
                "y": {"field": "value", "type": "quantitative", "label": y_label, "format": y_format},
                "color": {"field": "series", "type": "nominal", "label": "Series"},
            },
            "columns": [
                {"field": "week", "label": "Week starting", "format": "date"},
                {"field": "series", "label": "Series", "format": "text"},
                {"field": "value", "label": y_label, "format": y_format},
                {"field": "entered_by", "label": "Entered by", "format": "text"},
                {"field": "evidence", "label": "Where in the report", "format": "text"},
            ],
            "rows": rows,
            "takeaway": takeaway,
            "assumptions": [
                PANEL_NOTE,
                f"Entered by hand from {vendor}'s report: every row shows who entered it and where in the report it came from.",
                "The 'last entered' date is when the figure was typed in, not the week it describes; the freshness badge turns amber if the team stops updating.",
                "A correction is a new row with its own evidence; entered rows are never overwritten.",
            ],
            "badges": [],
        }

    return chart


for _source, _vendor in VENDORS.items():
    for _metric, _suffix, _noun, _y_label, _y_format in PANELS:
        make_chart(_source, _vendor, _metric, _suffix, _noun, _y_label, _y_format)
