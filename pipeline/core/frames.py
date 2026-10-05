"""Helpers every mart shares: snapshot dedupe, dims as columns, period roll-ups, and number formats for takeaways."""

from datetime import date

import pandas as pd

PERIOD = {"week": "W-SUN", "month": "M", "quarter": "Q"}  # W-SUN: weeks run Monday..Sunday, labelled by Monday


def dims(df: pd.DataFrame, *names: str) -> pd.DataFrame:
    """A copy of df with the named dims as ordinary columns (missing, i.e. NaN, where a row lacks one)."""
    out = df.copy()
    for n in names:
        out[n] = [d.get(n) if isinstance(d, dict) else None for d in out["dims"]]
    return out


def latest(df: pd.DataFrame, keys=("entity", "metric", "as_of")) -> pd.DataFrame:
    """One row per key, the most recently retrieved, so re-collected days and overlapping backfills never double count."""
    if df.empty:
        return df
    t = pd.to_datetime(df["retrieved_at"], utc=True, format="ISO8601")
    keys = list(keys)
    return df.assign(_t=t).sort_values("_t").drop_duplicates(keys, keep="last").drop(columns="_t").sort_values(keys)


def period_start(as_of: pd.Series, period: str) -> pd.Series:
    """ISO date of the first day of the week / month / quarter containing each as_of."""
    return pd.to_datetime(as_of).dt.to_period(PERIOD[period]).dt.start_time.dt.date.astype(str)


def roll(df: pd.DataFrame, period: str, how: str = "sum", by=("entity",)) -> pd.DataFrame:
    """Aggregate value per (by..., period start). Columns: *by, as_of, value."""
    if df.empty:
        return pd.DataFrame(columns=[*by, "as_of", "value"])
    out = df.assign(as_of=period_start(df["as_of"], period))
    return out.groupby([*by, "as_of"], as_index=False)["value"].agg(how)


def drop_partial(df: pd.DataFrame, period: str, today: date) -> pd.DataFrame:
    """Drop rows in the still-running period, so a half month never reads as a fall. Say so in the assumptions."""
    current = period_start(pd.Series([today.isoformat()]), period).iloc[0]
    return df[df["as_of"] < current]


def num(x: float) -> str:
    """640 -> '640', 12,345 -> '12.3K', 147,328,552 -> '147M', 2.5e9 -> '2.5B'."""
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= size:
            v = x / size
            return f"{v:.0f}{suffix}" if abs(v) >= 100 else f"{v:.1f}{suffix}".replace(".0" + suffix, suffix)
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def usd(x: float) -> str:
    return ("-" if x < 0 else "") + "$" + num(abs(x))


def pct(x: float, digits: int = 0) -> str:
    """0.412 -> '41%'."""
    return f"{x * 100:.{digits}f}%"


def change(new: float, old: float) -> str:
    """Signed relative change, '+12%' / '-3%'; 'n/a' when there is no base."""
    if not old:
        return "n/a"
    return f"{(new - old) / abs(old) * 100:+.0f}%"
