"""What a refresh changed and what needs attention: source freshness, row counts against the last commit, and empty charts.

    uv run python -m pipeline.report

Run it after `pipeline.collect` and `pipeline.build`. Freshness is computed against the clock, the same rule the dashboard uses.
Exit code 1 when any source is stale, never collected, or unreadable, so a script can stop on it.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from contracts import freshness
from pipeline.core import ROOT

ATTENTION = ("stale", "never")


def current(root: Path) -> dict:
    path = root / "data" / "registry.json"
    if not path.exists():
        raise SystemExit("data/registry.json not found: run `uv run python -m pipeline.build` first")
    return json.loads(path.read_text())


def previous(root: Path) -> dict | None:
    """The registry as committed at HEAD, for row-count changes; None when there is no history for it."""
    out = subprocess.run(
        ["git", "show", "HEAD:data/registry.json"], cwd=root, capture_output=True, text=True, check=False
    )
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


def empty_charts(root: Path) -> list[dict]:
    out = []
    for p in sorted((root / "data" / "marts").glob("*.json")):
        m = json.loads(p.read_text())
        if m["status"] == "awaiting_data":
            out.append({"chart": m["id"], "why": (m["takeaway"] or ["no reason given"])[0]})
    return out


def build_report(root: Path = ROOT, now: datetime | None = None, before: dict | None = None) -> dict:
    now = now or datetime.now(UTC)
    reg = current(root)
    before = before if before is not None else previous(root)
    old = {s["id"]: s for s in (before or {}).get("sources", [])}
    sources = []
    for s in reg["sources"]:
        got = datetime.fromisoformat(s["retrieved_at"]) if s["retrieved_at"] else None
        sources.append(
            {
                **s,
                "freshness": freshness(got, s["sla_days"], now),
                "rows_added": s["row_count"] - old[s["id"]]["row_count"] if s["id"] in old else None,
                "age_days": round((now - got).total_seconds() / 86400, 1) if got else None,
            }
        )
    needs = [s for s in sources if s["freshness"] in ATTENTION or not s["readable"]]
    return {
        "generated_at": reg["generated_at"],
        "sources": sources,
        "needs_attention": needs,
        "empty_charts": empty_charts(root),
        "had_previous": before is not None,
    }


ACTION = {
    "api": "run `uv run python -m pipeline.collect --source {id}` and read its error",
    "scrape": "run `uv run python -m pipeline.collect --source {id}` and read its error",
    "manual": "enter the latest figures with /add-manual-data",
    "ledger": "update the ledger: add the new rows with a source link and a verbatim quote",
}


def render(rep: dict) -> str:
    lines = [f"Registry built {rep['generated_at']}. {len(rep['sources'])} sources.", ""]
    width = max(len(s["id"]) for s in rep["sources"])
    for s in rep["sources"]:
        added = "" if s["rows_added"] is None else f" ({s['rows_added']:+d})"
        got = f"{s['age_days']}d ago" if s["age_days"] is not None else "never"
        lines.append(
            f"  {s['id']:<{width}}  {s['freshness']:<6}  collected {got:<10} data to {s['as_of'] or 'n/a':<10}  rows {s['row_count']}{added}"
        )
    lines += [
        "",
        "Needs attention:"
        if rep["needs_attention"]
        else "Nothing needs attention: every source is within twice its SLA.",
    ]
    for s in rep["needs_attention"]:
        why = (
            "unreadable: stored rows failed validation"
            if not s["readable"]
            else f"{s['freshness']} (SLA {s['sla_days']} days)"
        )
        lines.append(f"  - {s['id']}: {why}. To fix: {ACTION[s['method']].format(id=s['id'])}.")
    if rep["empty_charts"]:
        lines += ["", f"{len(rep['empty_charts'])} charts are awaiting data:"]
        lines += [f"  - {c['chart']}: {c['why'][:110]}" for c in rep["empty_charts"]]
    if not rep["had_previous"]:
        lines += ["", "(No committed registry to compare with, so row changes are not shown.)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    rep = build_report()
    print(render(rep))
    return 1 if rep["needs_attention"] else 0


if __name__ == "__main__":
    sys.exit(main())
