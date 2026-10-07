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
    *, cadence=None, source_ids=(), company=None, root: Path = ROOT, now: datetime | None = None, on_progress=None
) -> tuple[dict[str, Path | None], list[str], list[str]]:
    """Return ({source_id: file written, or None when it produced no rows}, errors, skipped).

    A source raising SourceUnavailable (e.g. no API key) is skipped for every company and is not an error.

    on_progress(source_id, done, total, state), if given, is called as each source starts (0 of N companies), after each company,
    and once more when the source ends. `state` is "running" until then, and at the end one of "done" (rows written), "empty"
    (no rows), "failed" (a company errored) or "skipped" (the source can't run here).
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
    notify = on_progress or (lambda *args: None)
    for s in sources:
        rows, state = [], "running"
        notify(s.id, 0, len(scope), state)
        for n, co in enumerate(scope, 1):
            produced = []
            # A failure stops this company's collection only. Rows already yielded are complete, provenance-stamped
            # observations, so they are kept (a long backfill that hits a limit keeps its progress); the error is reported.
            try:
                for row in s.fn(co):
                    produced.append(
                        validate(
                            "observation",
                            {**row, "source": s.id, "method": s.meta["method"], "tier": s.meta["tier"]} | stamp,
                        )
                    )
            except SourceUnavailable as e:
                skipped.append(f"{s.id}: {e}")
                state = "skipped"
                break
            except Exception as e:  # noqa: BLE001 - any collector failure must not stop the other sources
                kept = f" (kept {len(produced)} rows collected before it)" if produced else ""
                errors.append(f"{s.id}/{co.slug}: {type(e).__name__}: {e}{kept}")
                state = "failed"
            finally:
                rows += produced
            notify(s.id, n, len(scope), state)
        written[s.id] = write_raw(root, s.id, rows, now) if rows else None
        notify(s.id, len(scope), len(scope), state if state != "running" else "done" if rows else "empty")
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
