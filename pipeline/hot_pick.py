"""Save the Hot Pick as chart specs: data/marts/hot_pick.week_YYYY_WW.json and hot_pick.month_YYYY_MM.json.

    uv run python -m pipeline.hot_pick freeze

The running week and month can be saved again; a past one is never overwritten.
"""

import argparse
import calendar
import json
from datetime import UTC, date, datetime
from pathlib import Path

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import hot_pick


def period(window: str, end: date) -> tuple[tuple[int, int], str, str]:
    """((year, number), mart id, title) of the week or month that contains `end`."""
    if window == "week":
        year, week, _ = end.isocalendar()
        return (year, week), f"hot_pick.week_{year}_{week:02d}", f"Hot Pick, week {year}-W{week:02d}"
    return (
        (end.year, end.month),
        f"hot_pick.month_{end.year}_{end.month:02d}",
        f"Hot Pick, {calendar.month_name[end.month]} {end.year}",
    )


def current(window: str, now: datetime) -> tuple[int, int]:
    return tuple(now.isocalendar()[:2]) if window == "week" else (now.year, now.month)


def freeze(root: Path = ROOT, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(UTC)
    registry.discover()
    names = {c.slug: c.name for c in load_companies(root).values()}
    written = []
    for window in ("week", "month"):
        mart = registry.MARTS[f"hot_pick.this_{window}"]
        rows = {sid: read_observations(root, registry.SOURCES[sid].meta) for sid in mart.sources}
        end = hot_pick.window_end(Ctx([r for part in rows.values() for r in part], names))
        spec = build_mart(mart, rows, now, names)
        if end is None or spec["status"] != "ok":
            continue
        key, mart_id, title = period(window, end)
        out = root / "data/marts" / f"{mart_id}.json"
        if out.exists() and key < current(window, now):
            raise SystemExit(f"{out.name} is a past {window} and is never overwritten.")
        out.parent.mkdir(parents=True, exist_ok=True)
        spec = {**spec, "id": mart_id, "title": title}
        validate("chart_spec", spec)
        out.write_text(json.dumps(spec, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        written.append(out)
    if not written:
        raise SystemExit("Nothing to save: there are no picks. Run /refresh-data first.")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["freeze"])
    parser.parse_args(argv)
    for path in freeze():
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
