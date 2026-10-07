"""collect CLI: run automated sources for each company, stamp provenance, validate, write immutable raw files."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.companies import load_companies
from pipeline.core.http import SourceUnavailable
from pipeline.core.store import write_raw


def lane(source) -> str:
    """Sources that call the same service share a lane and run one after another; different lanes run side by side.

    The lane is the registrable domain of the source's url (efts.sec.gov and sec.gov are one lane); a url without one is its own lane."""
    host = urlparse(source.meta.get("url") or "").hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 and "{" not in host else source.id


def collect(
    *,
    cadence=None,
    source_ids=(),
    company=None,
    root: Path = ROOT,
    now: datetime | None = None,
    on_progress=None,
    workers: int = 1,
) -> tuple[dict[str, Path | None], list[str], list[str]]:
    """Return ({source_id: file written, or None when it produced no rows}, errors, skipped).

    A source raising SourceUnavailable (e.g. no API key) is skipped for every company and is not an error.

    on_progress(source_id, done, total, state), if given, is called as each source starts (0 of N companies), after each company,
    and once more when the source ends. `state` is "running" until then, and at the end one of "done" (rows written), "empty"
    (no rows), "failed" (a company errored) or "skipped" (the source can't run here). With workers > 1 it is called from several
    threads at once.

    workers > 1 runs the lanes (see `lane`) in parallel, at most `workers` at a time. Inside a lane, and for every company, calls
    stay one after another, so no service sees more traffic than with workers=1; the results come back in source order either way.
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
    notify = on_progress or (lambda *args: None)

    def run(s) -> tuple[str, Path | None, list[str], list[str]]:
        rows, errors, skipped, state = [], [], [], "running"
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
        path = write_raw(root, s.id, rows, now) if rows else None
        notify(s.id, len(scope), len(scope), state if state != "running" else "done" if rows else "empty")
        return s.id, path, errors, skipped

    lanes: dict[str, list] = {}
    for s in sources:
        lanes.setdefault(lane(s), []).append(s)
    if workers > 1 and len(lanes) > 1:
        with ThreadPoolExecutor(min(workers, len(lanes))) as pool:
            # pool.map re-raises a lane's unexpected error (a raw file that already exists) once every lane has finished
            done = {r[0]: r for batch in pool.map(lambda group: [run(s) for s in group], lanes.values()) for r in batch}
        results = [done[s.id] for s in sources]
    else:
        results = [run(s) for s in sources]
    written = {sid: path for sid, path, _, _ in results}
    return written, [e for r in results for e in r[2]], [k for r in results for k in r[3]]


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
