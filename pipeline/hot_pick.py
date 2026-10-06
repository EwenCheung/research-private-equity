"""Save this week's Hot Pick as data/marts/hot_pick.week_YYYY_WW.json (a chart spec, served like any other chart).

    uv run python -m pipeline.hot_pick freeze

The current week can be saved again while it is still running; a past week is never overwritten.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import hot_pick


def freeze(root: Path = ROOT, now: datetime | None = None) -> Path:
    now = now or datetime.now(UTC)
    registry.discover()
    mart = registry.MARTS["hot_pick.this_week"]
    rows = {sid: read_observations(root, registry.SOURCES[sid].meta) for sid in mart.sources}
    names = {c.slug: c.name for c in load_companies(root).values()}
    end = hot_pick.window_end(Ctx([r for part in rows.values() for r in part], names))
    spec = build_mart(mart, rows, now, names)
    if end is None or spec["status"] != "ok":
        raise SystemExit("Nothing to save: there are no picks this week. Run /refresh-data first.")
    year, week, _ = end.isocalendar()
    out = root / "data/marts" / f"hot_pick.week_{year}_{week:02d}.json"
    if out.exists() and (year, week) < tuple(now.isocalendar()[:2]):
        raise SystemExit(f"{out.name} is a past week and is never overwritten.")
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = {**spec, "id": f"hot_pick.week_{year}_{week:02d}", "title": f"Hot Pick, week {year}-W{week:02d}"}
    validate("chart_spec", spec)
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["freeze"])
    parser.parse_args(argv)
    print(f"wrote {freeze().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
