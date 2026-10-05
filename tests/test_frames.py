from datetime import date

import pandas as pd

from pipeline.core import frames


def df(rows):
    return pd.DataFrame(rows)


def test_latest_keeps_the_newest_retrieval_per_day():
    d = df(
        [
            {"entity": "a", "metric": "m", "as_of": "2026-10-05", "value": 1, "retrieved_at": "2026-10-05T03:07:10Z"},
            {"entity": "a", "metric": "m", "as_of": "2026-10-05", "value": 2, "retrieved_at": "2026-10-05T03:30:15Z"},
            {"entity": "a", "metric": "m", "as_of": "2026-10-04", "value": 3, "retrieved_at": "2026-10-05T03:07:10Z"},
        ]
    )
    out = frames.latest(d)
    assert out[["as_of", "value"]].values.tolist() == [["2026-10-04", 3], ["2026-10-05", 2]]


def test_roll_and_partial_periods():
    d = df(
        [
            {"entity": "a", "as_of": "2026-08-30", "value": 1},
            {"entity": "a", "as_of": "2026-08-31", "value": 2},
            {"entity": "a", "as_of": "2026-09-01", "value": 5},
            {"entity": "a", "as_of": "2026-10-01", "value": 7},
        ]
    )
    m = frames.roll(d, "month")
    assert m[["as_of", "value"]].values.tolist() == [["2026-08-01", 3], ["2026-09-01", 5], ["2026-10-01", 7]]
    assert frames.drop_partial(m, "month", date(2026, 10, 5))["as_of"].tolist() == ["2026-08-01", "2026-09-01"]
    w = frames.roll(d, "week")
    assert w["as_of"].tolist() == ["2026-08-24", "2026-08-31", "2026-09-28"]  # Monday starts
    assert frames.roll(d, "quarter")["as_of"].tolist() == ["2026-07-01", "2026-10-01"]


def test_dims_become_columns():
    d = df([{"dims": {"function": "research"}}, {"dims": {}}])
    col = frames.dims(d, "function")["function"]
    assert col.iloc[0] == "research" and pd.isna(col.iloc[1])


def test_number_formats():
    assert [frames.num(x) for x in (640, 12_345, 147_328_552, 2.5e9, 1e6)] == ["640", "12.3K", "147M", "2.5B", "1M"]
    assert frames.usd(183e9) == "$183B" and frames.pct(0.412) == "41%" and frames.change(110, 100) == "+10%"
    assert frames.change(5, 0) == "n/a"
