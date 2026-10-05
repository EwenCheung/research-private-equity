"""collect CLI: run automated sources for each company, stamp provenance, validate, write immutable raw files."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.companies import load_companies
from pipeline.core.http import SourceUnavailable
from pipeline.core.store import write_raw


def collect(
    *, cadence=None, source_ids=(), company=None, root: Path = ROOT, now: datetime | None = None
) -> tuple[dict[str, Path | None], list[str], list[str]]:
    """Return ({source_id: file written, or None when it produced no rows}, errors, skipped).

    A source raising SourceUnavailable (e.g. no API key) is skipped for every company and is not an error.
    """
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    registry.discover()
    companies = load_companies(root)
    if company and company not in companies:
        raise SystemExit(f"unknown company {company!r}; known: {sorted(companies)}")
    scope = [companies[company]] if company else list(companies.values())
    if unknown := set(source_ids) - set(registry.SOURCES):
        raise SystemExit(f"unknown source(s) {sorted(unknown)}; known: {sorted(registry.SOURCES)}")
    sources = [
        s
        for s in registry.SOURCES.values()
        if s.meta["method"] in registry.AUTOMATED
        and (not cadence or s.meta["cadence"] == cadence)
        and (not source_ids or s.id in source_ids)
    ]
    stamp = {"retrieved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    written, errors, skipped = {}, [], []
    for s in sources:
        rows = []
        for co in scope:
            try:  # a failing collector or invalid row drops that company's rows only; everything else still runs
                rows += [
                    validate(
                        "observation",
                        {**row, "source": s.id, "method": s.meta["method"], "tier": s.meta["tier"]} | stamp,
                    )
                    for row in s.fn(co)
                ]
            except SourceUnavailable as e:
                skipped.append(f"{s.id}: {e}")
                break
            except Exception as e:  # noqa: BLE001 - any collector failure must not stop the other sources
                errors.append(f"{s.id}/{co.slug}: {type(e).__name__}: {e}")
        written[s.id] = write_raw(root, s.id, rows, now) if rows else None
    return written, errors, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.collect", description=__doc__)
    ap.add_argument("--cadence", choices=["daily", "weekly", "monthly", "quarterly"])
    ap.add_argument("--source", nargs="+", default=[], metavar="ID")
    ap.add_argument("--company", metavar="SLUG")
    args = ap.parse_args(argv)
    written, errors, skipped = collect(cadence=args.cadence, source_ids=args.source, company=args.company)
    for sid, path in written.items():
        print(f"{sid}: " + (f"wrote {path.relative_to(ROOT)}" if path else "no rows"))
    for s in skipped:
        print(f"SKIP {s}")
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errors else 0
