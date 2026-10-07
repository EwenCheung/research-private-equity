"""Carry the monitor's data to a machine with no internet: fetch everything once, keep it in one SQLite file, unpack it anywhere.

    uv run python -m pipeline.offline fetch     run every collector once (needs internet), rebuild the charts, then pack
    uv run python -m pipeline.offline pack      write the SQLite file from what is already in data/ (no network)
    uv run python -m pipeline.offline restore   rebuild data/ from the SQLite file (no network)
    uv run python -m pipeline.offline check     compare the SQLite file with data/

The file holds the collectors' raw snapshots byte for byte (the source of truth, so a restore is exact), every observation as a row
you can query with SQL, the cited ledgers, the charts and the registry. After a restore, `python -m pipeline.build` and the API
run from those files alone: no collector is called. Vendor files in data/manual are left out unless you ask, because licensed data
stays private.
"""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from contracts import ContractError, validate
from pipeline.core import ROOT, registry
from pipeline.core.store import read_observations

DEFAULT_DB = Path("data/offline/signal-monitor.sqlite")
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (id TEXT PRIMARY KEY, page TEXT, label TEXT, method TEXT, tier TEXT, cadence TEXT, url TEXT, caveats TEXT);
CREATE TABLE files (
  path TEXT PRIMARY KEY,      -- relative to the repo root, for example data/raw/pypi_downloads/20261005T035703Z.jsonl.gz
  kind TEXT NOT NULL,         -- raw, ledger, manual or registry
  source TEXT,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  rows INTEGER,               -- observations parsed from the file; NULL when it does not parse under today's contract
  content BLOB NOT NULL
);
CREATE TABLE observations (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL, file TEXT NOT NULL, source_url TEXT, method TEXT, as_of TEXT NOT NULL, retrieved_at TEXT,
  tier TEXT, entity TEXT NOT NULL, metric TEXT NOT NULL, value REAL, dims TEXT, entered_by TEXT, evidence TEXT
);
CREATE INDEX observations_lookup ON observations (source, entity, metric, as_of);
CREATE TABLE charts (id TEXT PRIMARY KEY, page TEXT, title TEXT, kind TEXT, as_of TEXT, status TEXT, generated_at TEXT, spec TEXT NOT NULL);
"""


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_env(path: Path) -> None:
    """Put KEY=value lines of a .env file into the environment, without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def parse_raw(content: bytes, name: str) -> list[dict] | None:
    """The validated observations of one raw snapshot, or None when it does not parse under today's contract (an old shape)."""
    try:
        rows = [
            validate("observation", json.loads(line))
            for line in gzip.decompress(content).decode("utf-8").splitlines()
            if line
        ]
    except (ContractError, ValueError, OSError, EOFError, KeyError) as e:
        print(
            f"note: {name} does not parse under the current contract ({type(e).__name__}); kept as a file, not as rows",
            file=sys.stderr,
        )
        return None
    return rows


def obs_row(r: dict, source: str, file: str) -> tuple:
    return (
        source,
        file,
        r["source_url"],
        r["method"],
        r["as_of"],
        r["retrieved_at"],
        r["tier"],
        r["entity"],
        r["metric"],
        r["value"],
        json.dumps(r["dims"], ensure_ascii=False, sort_keys=True),
        r.get("entered_by"),
        r.get("evidence"),
    )


def pack(root: Path = ROOT, db: Path | None = None, include_manual: bool = False) -> dict:
    """Write the SQLite file from what is already under data/. Returns the counts."""
    registry.discover()
    db = Path(db) if db else root / DEFAULT_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_name(db.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    counts = {"files": 0, "observations": 0, "charts": 0, "unparsed": 0, "skipped": []}

    con.executemany(
        "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                m["id"],
                m.get("page"),
                m.get("label"),
                m["method"],
                m["tier"],
                m["cadence"],
                m.get("url"),
                m.get("caveats"),
            )
            for m in (s.meta for s in registry.SOURCES.values())
        ],
    )

    def add_file(path: Path, kind: str, source: str | None, rows: list[dict] | None, has_rows: bool = True) -> None:
        content = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?)",
            (rel, kind, source, sha(content), len(content), None if rows is None else len(rows), content),
        )
        counts["files"] += 1
        if rows is None and has_rows:
            counts["unparsed"] += 1
        for r in rows or []:
            con.execute(
                "INSERT INTO observations (source, file, source_url, method, as_of, retrieved_at, tier, entity, metric, value, dims, entered_by, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                obs_row(r, source or r["source"], rel),
            )
            counts["observations"] += 1

    # Raw snapshots, including those of retired sources: history is never dropped.
    for path in sorted((root / "data" / "raw").glob("*/*.jsonl.gz")):
        add_file(path, "raw", path.parent.name, parse_raw(path.read_bytes(), path.relative_to(root).as_posix()))

    # Ledgers and manual files: rows only for registered sources, the file itself always.
    for folder, kind in (("ledgers", "ledger"), ("manual", "manual")):
        for path in sorted((root / "data" / folder).glob("*.csv")):
            if kind == "manual" and not include_manual:
                counts["skipped"].append(path.relative_to(root).as_posix())
                continue
            known = registry.SOURCES.get(path.stem)
            rows = (
                read_observations(root, known.meta) if known and known.meta["method"] in ("manual", "ledger") else None
            )
            add_file(path, kind, path.stem, rows)

    registry_file = root / "data" / "registry.json"
    if registry_file.exists():
        add_file(registry_file, "registry", None, None, has_rows=False)

    for path in sorted((root / "data" / "marts").glob("*.json")):
        text = path.read_text(encoding="utf-8")
        spec = json.loads(text)
        con.execute(
            "INSERT INTO charts VALUES (?,?,?,?,?,?,?,?)",
            (
                spec["id"],
                spec["page"],
                spec["title"],
                spec["kind"],
                spec["as_of"],
                spec["status"],
                spec["generated_at"],
                text,
            ),
        )
        counts["charts"] += 1

    sha_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    ).stdout.strip()
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "packed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_head": sha_head,
        "includes_manual": str(include_manual).lower(),
        **{k: str(v) for k, v in counts.items() if k != "skipped"},
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
    con.close()
    os.replace(tmp, db)
    return counts | {"db": db, "bytes": db.stat().st_size}


def open_db(db: Path) -> sqlite3.Connection:
    if not Path(db).exists():
        raise SystemExit(f"{db} does not exist; run `python -m pipeline.offline pack` first")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise SystemExit(f"{db} was written with another schema version")
    return con


def target(root: Path, rel: str) -> Path:
    """Where a stored path lands, refusing anything outside the repo's data folder."""
    path = (root / rel).resolve()
    if not path.is_relative_to((root / "data").resolve()):
        raise SystemExit(f"refusing to write {rel!r}: it is outside data/")
    return path


def restore(db: Path, root: Path = ROOT) -> dict:
    """Rebuild data/ from the SQLite file. Raw snapshots and ledgers are never overwritten with different bytes; charts and the registry,
    which a build regenerates, are replaced."""
    con = open_db(db)
    files = con.execute("SELECT path, kind, sha256, content FROM files ORDER BY path").fetchall()
    conflicts = [
        p
        for p, kind, digest, _ in files
        if kind != "registry" and (t := target(root, p)).exists() and sha(t.read_bytes()) != digest
    ]
    if conflicts:
        raise SystemExit(
            "these files already exist with different content and are immutable; move them away to restore over them:\n  "
            + "\n  ".join(conflicts)
        )
    written = kept = 0
    for p, kind, digest, content in files:
        t = target(root, p)
        if kind != "registry" and t.exists():
            kept += 1
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(content)
        written += 1
    charts = con.execute("SELECT id, spec FROM charts ORDER BY id").fetchall()
    for cid, spec in charts:
        t = target(root, f"data/marts/{cid}.json")
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(spec, encoding="utf-8")
    con.close()
    return {"written": written, "kept": kept, "charts": len(charts)}


def check(db: Path, root: Path = ROOT) -> list[str]:
    """Differences between the SQLite file and data/; empty when they agree."""
    con = open_db(db)
    problems = []
    stored = {}
    for p, kind, digest, rows in con.execute("SELECT path, kind, sha256, rows FROM files"):
        stored[p] = kind
        t = root / p
        if not t.exists():
            problems.append(f"missing on disk: {p}")
        elif sha(t.read_bytes()) != digest:
            problems.append(f"differs from the file: {p}")
        elif rows is not None:
            n = con.execute("SELECT COUNT(*) FROM observations WHERE file = ?", (p,)).fetchone()[0]
            if n != rows:
                problems.append(f"{p}: {n} observation rows, expected {rows}")
    for path in sorted((root / "data" / "raw").glob("*/*.jsonl.gz")):
        rel = path.relative_to(root).as_posix()
        if rel not in stored:
            problems.append(f"on disk but not in the file (collected since the pack): {rel}")
    for cid, spec in con.execute("SELECT id, spec FROM charts"):
        t = root / "data" / "marts" / f"{cid}.json"
        if not t.exists() or t.read_text(encoding="utf-8") != spec:
            problems.append(f"chart differs or is missing: {cid}")
    con.close()
    return problems


def fetch(
    root: Path = ROOT,
    db: Path | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    include_manual: bool = False,
) -> int:
    """Run every automated collector once, rebuild the charts, then pack. Collectors that cannot run (no key) are reported, never fatal."""
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    load_env(root / ".env")
    registry.discover()
    ids = [
        s.id
        for s in registry.SOURCES.values()
        if s.meta["method"] in registry.AUTOMATED and (not only or s.id in only) and s.id not in (skip or [])
    ]
    if not ids:
        raise SystemExit(
            "no source matches; the automated sources are: "
            + ", ".join(s.id for s in registry.SOURCES.values() if s.meta["method"] in registry.AUTOMATED)
        )
    print(f"calling {len(ids)} sources once: {', '.join(ids)}")
    written, errors, skipped = collector.collect(source_ids=ids, root=root)
    for sid, path in written.items():
        print(f"  {sid}: " + (f"wrote {path.relative_to(root)}" if path else "no rows"))
    for s in skipped:
        print(f"  SKIP {s}")
    for e in errors:
        print(f"  ERROR {e}", file=sys.stderr)
    _, build_errors = builder.build(root=root)
    for e in build_errors:
        print(f"  BUILD ERROR {e}", file=sys.stderr)
    counts = pack(root, db, include_manual)
    print(
        f"packed {counts['observations']:,} observations, {counts['files']} files and {counts['charts']} charts into {counts['db']} ({counts['bytes'] / 1e6:.1f} MB)"
    )
    if errors:
        print(
            f"{len(errors)} source(s) failed: their last stored snapshot is what the file holds. Re-run with --only <id> after fixing.",
            file=sys.stderr,
        )
    return 1 if errors or build_errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.offline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (
        ("fetch", "call every source once, rebuild, pack"),
        ("pack", "pack what is in data/"),
        ("restore", "unpack into data/"),
        ("check", "compare the file with data/"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--db", type=Path, help=f"the SQLite file (default {DEFAULT_DB})")
        p.add_argument("--root", type=Path, default=ROOT, help="the repo to read or write (default: this one)")
        if name in ("fetch", "pack"):
            p.add_argument(
                "--include-manual",
                action="store_true",
                help="also pack data/manual (vendor files: licensed data stays private)",
            )
        if name == "fetch":
            p.add_argument("--only", nargs="+", metavar="ID", help="call only these sources")
            p.add_argument(
                "--skip", nargs="+", metavar="ID", help="leave these sources out (the Internet Archive ones are slow)"
            )
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(
        line_buffering=True
    )  # a long fetch shows its progress as it goes, also when redirected to a file
    root = args.root.resolve()
    db = args.db or root / DEFAULT_DB
    if args.cmd == "fetch":
        return fetch(root, db, args.only, args.skip, args.include_manual)
    if args.cmd == "pack":
        c = pack(root, db, args.include_manual)
        print(
            f"packed {c['observations']:,} observations, {c['files']} files and {c['charts']} charts into {c['db']} ({c['bytes'] / 1e6:.1f} MB)"
        )
        for s in c["skipped"]:
            print(f"left out (vendor file; use --include-manual to pack it): {s}")
        return 0
    if args.cmd == "restore":
        c = restore(db, root)
        print(
            f"restored {c['written']} files ({c['kept']} already there, identical) and {c['charts']} charts into {root / 'data'}"
        )
        return 0
    problems = check(db, root)
    print("\n".join(problems) if problems else "the file and data/ agree")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
