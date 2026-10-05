"""Enter licensed alt-data by hand (YipitData, M Science), validated and stamped with who entered it and when.

    uv run python -m pipeline.licensed add yipit_consumer --as-of 2026-09-28 --entity anthropic \\
        --metric panel_spend_usd --value 44200000 --evidence "Weekly report 2026-10-05, p.3, Claude, US card panel"
    uv run python -m pipeline.licensed import-csv yipit_consumer report.csv --evidence "Weekly report 2026-10-05"
    uv run python -m pipeline.licensed status

Rows go to data/manual/<source>.csv and are never overwritten: a correction is a new row with its own evidence.
Licensed data stays in this private repository. Never paste it into issues, pull requests or external services.
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from contracts import ContractError, validate
from pipeline.core import ROOT, registry
from pipeline.core.store import CSV_FIELDS, read_observations
from pipeline.sources.licensed_data import METRICS

HEADER = [
    "as_of",
    "entity",
    "metric",
    "value",
    "dims",
    *[f for f in CSV_FIELDS if f not in ("as_of", "entity", "metric")],
]


def manual_sources() -> dict[str, dict]:
    registry.discover()
    return {sid: s.meta for sid, s in registry.SOURCES.items() if s.meta["method"] == "manual"}


def csv_path(root: Path, source_id: str) -> Path:
    return root / "data" / "manual" / f"{source_id}.csv"


def git_user() -> str:
    return subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=False).stdout.strip()


def build_row(meta: dict, *, as_of, entity, metric, value, dims, url, evidence, entered_by, now) -> dict:
    """One CSV row, validated as the observation it will become. Raises ContractError listing every problem."""
    if metric not in METRICS:
        raise ContractError(f"metric {metric!r} is not one of {METRICS}")
    evidence = (evidence or "").strip()
    if not evidence:  # the contract accepts any one character; a licensed number with blank evidence is not auditable
        raise ContractError(
            f"{as_of} {entity} {metric}: evidence is required (report name, date, page, and the figure as printed)"
        )
    row = {
        "as_of": as_of,
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": json.dumps(dims or {}, ensure_ascii=False),
        "source_url": url or meta["url"],
        "entered_by": entered_by,
        "retrieved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence": evidence,
    }
    validate(
        "observation",
        {
            **row,
            "source": meta["id"],
            "method": meta["method"],
            "tier": meta["tier"],
            "dims": dims or {},
            "value": float(value),
        },
    )
    return row


def append_rows(root: Path, source_id: str, rows: list[dict]) -> Path:
    """Append rows to the source's CSV, writing the header when the file is new. Existing rows are never touched."""
    path = csv_path(root, source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            writer.writeheader()
        writer.writerows(rows)
    return path


def existing_keys(root: Path, meta: dict) -> set[tuple]:
    return {
        (r["as_of"], r["entity"], r["metric"], json.dumps(r["dims"], sort_keys=True))
        for r in read_observations(root, meta)
    }


def add(
    root: Path, source_id: str, records: list[dict], *, entered_by: str | None = None, now: datetime | None = None
) -> tuple[int, list[str]]:
    """Validate and append records. Returns (rows added, skipped duplicates). Nothing is written if any record is invalid."""
    meta = manual_sources()[source_id]
    entered_by = entered_by or git_user()
    if not entered_by:
        raise ContractError("no entered_by: set git config user.name, or pass --entered-by")
    now = now or datetime.now(UTC)
    seen, rows, skipped = existing_keys(root, meta), [], []
    for rec in records:
        row = build_row(meta, entered_by=entered_by, now=now, **rec)
        key = (row["as_of"], row["entity"], row["metric"], json.dumps(json.loads(row["dims"]), sort_keys=True))
        if key in seen:  # an existing entry is never overwritten: a correction is a new row with its own evidence
            skipped.append(f"{row['as_of']} {row['entity']} {row['metric']} already entered")
            continue
        seen.add(key)
        rows.append(row)
    if rows:
        append_rows(root, source_id, rows)
    return len(rows), skipped


def records_from_csv(path: Path, default_evidence: str | None, default_url: str | None) -> list[dict]:
    """Read a vendor table: columns as_of, entity, metric, value; optional dims (JSON), evidence, source_url."""
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for n, r in enumerate(csv.DictReader(f), 2):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
            try:
                out.append(
                    {
                        "as_of": r["as_of"],
                        "entity": r["entity"],
                        "metric": r["metric"],
                        "value": r["value"].replace(",", "").replace("$", ""),
                        "dims": json.loads(r["dims"]) if r.get("dims") else {},
                        "url": r.get("source_url") or default_url,
                        "evidence": r.get("evidence") or default_evidence,
                    }
                )
            except (KeyError, ValueError) as e:
                raise ContractError(f"{path.name} line {n}: {e!r}") from e
    return out


def status(root: Path) -> list[dict]:
    out = []
    for sid, meta in manual_sources().items():
        rows = read_observations(root, meta)
        last = max(rows, key=lambda r: r["retrieved_at"], default=None)
        out.append(
            {
                "source": sid,
                "rows": len(rows),
                "latest_data_date": max((r["as_of"] for r in rows), default=None),
                "last_entered": last["retrieved_at"] if last else None,
                "entered_by": last["entered_by"] if last else None,
            }
        )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.licensed",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="enter one figure")
    a.add_argument("source")
    a.add_argument(
        "--as-of", required=True, help="the date the figure describes, YYYY-MM-DD (the week start for weekly data)"
    )
    a.add_argument("--entity", required=True, help="company slug, e.g. anthropic")
    a.add_argument("--metric", required=True, choices=METRICS)
    a.add_argument("--value", required=True)
    a.add_argument("--dims", default="{}", help='JSON, e.g. \'{"product": "Claude Pro", "panel": "US card"}\'')
    a.add_argument("--evidence", required=True, help="where in the report: name, date, page, and the figure as printed")
    a.add_argument("--url")
    a.add_argument("--entered-by")
    i = sub.add_parser("import-csv", help="enter many figures from a vendor table")
    i.add_argument("source")
    i.add_argument("path", type=Path)
    i.add_argument("--evidence", help="default evidence for rows without an evidence column")
    i.add_argument("--url")
    i.add_argument("--entered-by")
    sub.add_parser("status", help="show when each licensed source was last entered")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        for s in status(ROOT):
            print(
                f"{s['source']}: {s['rows']} rows, data to {s['latest_data_date'] or 'n/a'}, last entered {s['last_entered'] or 'never'} by {s['entered_by'] or 'n/a'}"
            )
        return 0
    if args.source not in manual_sources():
        print(f"unknown source {args.source!r}; known: {sorted(manual_sources())}", file=sys.stderr)
        return 2
    try:
        if args.cmd == "add":
            records = [
                {
                    "as_of": args.as_of,
                    "entity": args.entity,
                    "metric": args.metric,
                    "value": args.value,
                    "dims": json.loads(args.dims),
                    "url": args.url,
                    "evidence": args.evidence,
                }
            ]
        else:
            records = records_from_csv(args.path, args.evidence, args.url)
        added, skipped = add(ROOT, args.source, records, entered_by=args.entered_by)
    except (ContractError, ValueError) as e:
        print(f"nothing written: {e}", file=sys.stderr)
        return 1
    print(f"added {added} rows to data/manual/{args.source}.csv")
    for s in skipped:
        print(f"skipped: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
