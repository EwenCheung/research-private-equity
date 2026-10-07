"""Carry the monitor's data to a machine with no internet: fetch everything once, keep it in one SQLite file, unpack it anywhere.

    uv run python -m pipeline.offline fetch     run every collector once (needs internet), rebuild the charts, then pack;
                                                the dashboard's Refresh data button runs the same thing
    uv run python -m pipeline.offline pack      write the SQLite file from what is already in data/ (no network)
    uv run python -m pipeline.offline restore   rebuild data/ from the SQLite file (no network)
    uv run python -m pipeline.offline check     compare the SQLite file with data/
    uv run python -m pipeline.offline serve     show the dashboard from the SQLite file alone (no network, no data/ folder)

The file holds the collectors' raw snapshots byte for byte (the source of truth, so a restore is exact), the cited ledgers, the charts,
the registry and the built dashboard; it is small enough to commit. `--with-rows` also lists every observation as a row for SQL. `serve` runs the dashboard from the file
alone. A fetch or refresh never overwrites a good file with a worse one: see `refresh`. After a `restore`, `python -m pipeline.build` and the API also run from the unpacked files: no collector is called. Vendor files in data/manual are left out unless you ask, because licensed data
stays private.
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

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
  kind TEXT NOT NULL,         -- raw, ledger, manual, registry or web (the built dashboard)
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


def pack(
    root: Path = ROOT,
    db: Path | None = None,
    include_manual: bool = False,
    web_from: Path | None = None,
    with_rows: bool = False,
) -> dict:
    """Write the SQLite file from what is already under data/. Returns the counts.

    The `observations` table (every row, for SQL) is derived from the raw snapshots and about ten times their size, and the dashboard
    never reads it, so it stays empty unless `with_rows`: a file without it is small enough to commit.

    The built dashboard comes from frontend/dist; a machine that only serves the file has none, so `web_from` (the file it serves)
    supplies it."""
    registry.discover()
    db = Path(db) if db else root / DEFAULT_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_name(db.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp, uri=True)
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
            if with_rows:
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

    dist = root / "frontend" / "dist"
    for path in sorted(p for p in dist.rglob("*") if p.is_file()) if dist.is_dir() else []:
        add_file(path, "web", None, None, has_rows=False)
    counts["web"] = sum(1 for _ in dist.rglob("*") if _.is_file()) if dist.is_dir() else 0
    if not dist.is_dir() and web_from and Path(web_from).is_file():
        con.execute("ATTACH ? AS previous", (f"{Path(web_from).resolve().as_uri()}?mode=ro",))
        con.execute(
            "INSERT INTO files (path, kind, source, sha256, size, rows, content) "
            "SELECT path, kind, source, sha256, size, rows, content FROM previous.files WHERE kind = 'web'"
        )
        counts["web"] = con.execute("SELECT COUNT(*) FROM files WHERE kind = 'web'").fetchone()[0]
        counts["files"] += counts["web"]
        con.commit()
        con.execute("DETACH previous")

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
        "includes_rows": str(with_rows).lower(),
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
    """Where a stored path lands, refusing anything outside the repo's data folder and the built dashboard."""
    path = (root / rel).resolve()
    if not any(path.is_relative_to((root / part).resolve()) for part in ("data", "frontend/dist")):
        raise SystemExit(f"refusing to write {rel!r}: it is outside data/ and frontend/dist/")
    return path


def restore(db: Path, root: Path = ROOT) -> dict:
    """Rebuild data/ from the SQLite file. Raw snapshots and ledgers are never overwritten with different bytes; charts and the registry,
    which a build regenerates, are replaced."""
    con = open_db(db)
    files = con.execute("SELECT path, kind, sha256, content FROM files ORDER BY path").fetchall()
    regenerated = ("registry", "web")  # a build rewrites these, so a restore replaces them
    conflicts = [
        p
        for p, kind, digest, _ in files
        if kind not in regenerated and (t := target(root, p)).exists() and sha(t.read_bytes()) != digest
    ]
    if conflicts:
        raise SystemExit(
            "these files already exist with different content and are immutable; move them away to restore over them:\n  "
            + "\n  ".join(conflicts)
        )
    written = kept = 0
    for p, kind, _, content in files:
        t = target(root, p)
        if kind not in regenerated and t.exists():
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
    has_rows = (con.execute("SELECT value FROM meta WHERE key = 'includes_rows'").fetchone() or ("false",))[0] == "true"
    for p, kind, digest, rows in con.execute("SELECT path, kind, sha256, rows FROM files"):
        stored[p] = kind
        t = root / p
        if kind == "web" and not (root / "frontend" / "dist").is_dir():
            continue  # a machine that only serves the file has no built dashboard on disk, and needs none
        if not t.exists():
            problems.append(f"missing on disk: {p}")
        elif sha(t.read_bytes()) != digest:
            problems.append(f"differs from the file: {p}")
        elif rows is not None and has_rows:
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


def extract_web(db: Path, dest: Path) -> int:
    """Write the dashboard stored in the file into `dest`, as the built frontend folder the API serves."""
    con = open_db(db)
    rows = con.execute("SELECT path, content FROM files WHERE kind = 'web'").fetchall()
    con.close()
    for rel, content in rows:
        out = (dest / Path(rel).relative_to("frontend/dist")).resolve()
        if not out.is_relative_to(dest.resolve()):
            raise SystemExit(f"refusing to write {rel!r}: it is outside the dashboard folder")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    return len(rows)


def serve(db: Path, root: Path = ROOT, host: str = "127.0.0.1", port: int = 8000) -> int:
    """Show the dashboard from the SQLite file alone: the API reads the charts and registry from it, and the page comes out of it too."""
    import uvicorn

    load_env(root / ".env")
    if not (os.environ.get("DASHBOARD_PASSWORD") and os.environ.get("SESSION_SECRET")):
        raise SystemExit(
            "set DASHBOARD_PASSWORD and SESSION_SECRET (in .env or the environment) to sign in to the dashboard"
        )
    open_db(db).close()
    web = Path(tempfile.mkdtemp(prefix="signal-monitor-web-"))
    try:
        if not extract_web(db, web):
            print(
                "the file holds no dashboard (build the frontend before packing); serving the API only", file=sys.stderr
            )
        os.environ["DATA_DB"], os.environ["FRONTEND_DIR"] = str(Path(db).resolve()), str(web)
        print(f"serving {db} on http://{host}:{port}  (no network needed)")
        uvicorn.run("app.server:app", host=host, port=port, log_level="warning")
    finally:
        shutil.rmtree(web, ignore_errors=True)
    return 0


def build_web(root: Path) -> None:
    """Build the dashboard so the file can carry it; without Node the file still holds the data."""
    if not shutil.which("npm") or not (root / "frontend" / "node_modules").is_dir():
        print(
            "note: npm or frontend/node_modules is missing, so the file will hold the data but not the dashboard",
            file=sys.stderr,
        )
        return
    done = subprocess.run(
        ["npm", "--prefix", "frontend", "run", "build"], cwd=root, capture_output=True, text=True, check=False
    )
    if done.returncode:
        print(
            "note: the frontend build failed, so the file will hold the data but not the dashboard:\n"
            + done.stdout[-600:],
            file=sys.stderr,
        )


WORKERS = 16  # sources on different services run side by side (see pipeline.core.collect.lane); this caps how many
STATE_WORD = {"done": "DONE", "failed": "FAILED", "skipped": "SKIPPED", "empty": "NO ROWS"}
PROBES = ("https://api.github.com", "https://pypi.org", "https://registry.npmjs.org")


def online() -> bool:
    """True when the internet answers. A machine behind a wall gets a quick no, instead of every collector retrying into it."""
    for url in PROBES:
        try:
            httpx.head(url, timeout=4, follow_redirects=True)
            return True
        except httpx.TransportError:
            continue
    return False


def plan(root: Path, only: list[str] | None, skip: list[str] | None) -> tuple[list[str], set[str]]:
    """The automated sources to call, and those left out (config/offline.yaml plus --skip). Naming sources with --only calls exactly those."""
    registry.discover()
    automated = [s.id for s in registry.SOURCES.values() if s.meta["method"] in registry.AUTOMATED]
    if unknown := sorted(set(only or []) - set(automated)):
        raise SystemExit(f"no source matches {', '.join(unknown)}; the automated sources are: {', '.join(automated)}")
    config = root / "config" / "offline.yaml"
    left_out = set((yaml.safe_load(config.read_text()) or {}).get("skip") or []) if config.exists() else set()
    left_out |= set(skip or [])
    ids = [sid for sid in automated if (sid in only if only else sid not in left_out)]
    if not ids:
        raise SystemExit(f"no source matches; the automated sources are: {', '.join(automated)}")
    return ids, left_out


SGT = timezone(timedelta(hours=8), "SGT")  # Singapore: UTC+8, no daylight saving


def when(stamp: str) -> str:
    """2026-10-07T07:08:39Z -> '2026-10-07 15:08 SGT'. Stored times stay UTC; people read Singapore time."""
    return datetime.fromisoformat(stamp).astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT")


def packed_at(db: Path) -> str:
    con = open_db(db)
    try:
        return con.execute("SELECT value FROM meta WHERE key = 'packed_at'").fetchone()[0]
    finally:
        con.close()


def restore_missing(db: Path, root: Path) -> int:
    """Put back the raw snapshots the file holds and this checkout lacks (a fresh clone, a copy without data/raw), never overwriting one.

    A refresh packs data/raw, so without them the new file would drop history the old one has."""
    con = open_db(db)
    try:
        missing = [
            (p, c)
            for p, c in con.execute("SELECT path, content FROM files WHERE kind = 'raw'")
            if not target(root, p).exists()
        ]
    finally:
        con.close()
    for p, content in missing:
        t = target(root, p)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(content)
    return len(missing)


def compare(new: Path, old: Path | None) -> tuple[list[str], int]:
    """What stops `new` from replacing `old` (damage, a raw snapshot lost or changed, a chart lost), and how many raw snapshots it adds."""
    con = sqlite3.connect(new, uri=True)
    try:
        problems = [] if con.execute("PRAGMA integrity_check").fetchone()[0] == "ok" else ["the new file is damaged"]
        added = con.execute("SELECT COUNT(*) FROM files WHERE kind = 'raw'").fetchone()[0]
        if old:
            con.execute("ATTACH ? AS old", (f"{Path(old).resolve().as_uri()}?mode=ro",))
            problems += [
                f"raw snapshot lost or changed: {p}"
                for (p,) in con.execute(
                    "SELECT o.path FROM old.files o LEFT JOIN main.files n ON n.path = o.path AND n.sha256 = o.sha256 "
                    "WHERE o.kind = 'raw' AND n.path IS NULL"
                )
            ]
            problems += [
                f"chart lost: {c}"
                for (c,) in con.execute("SELECT id FROM old.charts WHERE id NOT IN (SELECT id FROM main.charts)")
            ]
            added = con.execute(
                "SELECT COUNT(*) FROM main.files WHERE kind = 'raw' AND path NOT IN (SELECT path FROM old.files)"
            ).fetchone()[0]
        return problems, added
    finally:
        con.close()


def refresh(
    root: Path = ROOT,
    db: Path | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    include_manual: bool = False,
    rebuild_web: bool = True,
    workers: int = WORKERS,
    with_rows: bool = False,
    log=print,
    progress=None,
) -> dict:
    """Fetch every source again, rebuild the charts and replace the SQLite file, without ever making it worse. Returns a report.

    The new file is written beside the old one and the old one is replaced only when the new one is sound:
      * no internet: nothing is called, the file stays.
      * a source that errors keeps the data it had: what it collected before failing is set aside in data/raw/_rejected/ (never deleted).
      * a failed build, a damaged file, or a lost raw snapshot or chart: the whole refresh is rolled back, the file stays.
      * otherwise the file is replaced, and the one it replaced is kept as <file>.previous.
    The first file ever written has nothing to protect, so it is always written.

    `log` receives the progress lines the command line prints; `progress(snapshot)` receives the same as data for the dashboard:
    {stage, finished, total, sources: [{id, label, state, percent}]}.
    """
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    load_env(root / ".env")
    db = Path(db) if db else root / DEFAULT_DB
    ids, left_out = plan(root, only, skip)
    live = db if db.is_file() else None
    before = packed_at(live) if live else None
    report = {
        "swapped": False,
        "updated": [],
        "failed": {},
        "unchanged": [],
        "skipped": [],
        "set_aside": [],
        "problems": [],
    }

    def done(state: str, message: str) -> dict:
        return {**report, "state": state, "message": message}

    steps = {
        sid: {"id": sid, "label": registry.SOURCES[sid].meta.get("label") or sid, "state": "waiting", "percent": 0}
        for sid in ids
    }

    def announce(stage: str) -> None:
        if progress:
            finished = sum(s["state"] not in ("waiting", "running") for s in steps.values())
            progress(
                {"stage": stage, "finished": finished, "total": len(ids), "sources": [dict(s) for s in steps.values()]}
            )

    lock = threading.Lock()  # sources report from several threads at once

    def on_progress(sid: str, done: int, total: int, state: str) -> None:
        with lock:
            steps[sid] = {**steps[sid], "state": state, "percent": round(100 * done / total) if total else 100}
            if state == "running" and done:
                log(f"  collect {sid} {steps[sid]['percent']}%…")
            elif state != "running":
                log(f"  collect {sid} {STATE_WORD[state]}")
                log(
                    f"data collected ({sum(s['state'] not in ('waiting', 'running') for s in steps.values())}/{len(ids)}):"
                )
            announce("Collecting sources")

    kept = f"The dashboard keeps the data from {when(before)}." if before else ""
    log(f"calling {len(ids)} sources once: {', '.join(ids)}")
    if not only and left_out:
        log(f"left out: {', '.join(sorted(left_out))}")
    if not online():
        return done("offline", f"No internet from this machine, so nothing was fetched. {kept}".strip())

    if live and (n := restore_missing(live, root)):
        log(f"put back {n} raw snapshots that the file holds and this checkout lacked")
    announce("Collecting sources")
    log(f"data collected (0/{len(ids)}):")
    written, errors, skipped = collector.collect(source_ids=ids, root=root, on_progress=on_progress, workers=workers)
    for e in errors:
        report["failed"].setdefault(e.partition("/")[0].partition(":")[0], []).append(e)
    for sid in report["failed"]:
        if path := written.pop(sid, None):
            aside = root / "data" / "raw" / "_rejected" / sid / path.name
            aside.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, aside)
            report["set_aside"].append(aside.relative_to(root).as_posix())
    report["updated"] = [sid for sid, path in written.items() if path]
    report["unchanged"] = [sid for sid, path in written.items() if not path]
    report["skipped"] = skipped
    for sid, path in written.items():
        if path:
            log(f"  {sid}: wrote {path.relative_to(root)}")
    for s in skipped:
        log(f"  SKIP {s}")
    for e in errors:
        log(f"  ERROR {e}", file=sys.stderr)

    announce("Rebuilding the charts")
    if rebuild_web:
        build_web(root)
    _, build_errors = builder.build(root=root)
    announce("Packing the new file")
    stage = db.with_name(db.name + ".new")
    try:
        counts = pack(root, stage, include_manual, web_from=live, with_rows=with_rows)
        announce("Checking the new file")
        problems, added = compare(stage, live)
        report["problems"] = [f"build: {e}" for e in build_errors] + problems
        for p in report["problems"]:
            log(f"  PROBLEM {p}", file=sys.stderr)
        failed = ", ".join(report["failed"])
        if report["problems"] and live:
            return done(
                "failed",
                f"Rolled back: {report['problems'][0]}. {kept} Snapshots collected now stay in data/raw and join the next refresh.",
            )
        if live and not added:
            if report["failed"]:
                return done("failed", f"Nothing new: {failed} failed. {kept}")
            return done(
                "unchanged", f"Nothing new since {when(before)}: every source returned what the file already holds."
            )
        if live:
            shutil.copy2(live, db.with_name(db.name + ".previous"))
        os.replace(stage, db)
    finally:
        stage.unlink(missing_ok=True)
    report["swapped"] = True
    report |= {"observations": counts["observations"], "charts": counts["charts"], "packed_at": packed_at(db)}
    log(
        f"packed {counts['observations']:,} observations, {counts['files']} files and {counts['charts']} charts into {db} ({counts['bytes'] / 1e6:.1f} MB)"
    )
    if report["failed"] or report["problems"]:
        what = (
            f"{len(report['failed'])} source(s) failed and keep their previous data: {failed}"
            if report["failed"]
            else "problems were found"
        )
        return done("partial", f"Updated {len(report['updated'])} source(s); {what}.")
    return done(
        "ok",
        f"Updated {len(report['updated'])} source(s). The dashboard now shows the data from {when(report['packed_at'])}.",
    )


def fetch(
    root: Path = ROOT,
    db: Path | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    include_manual: bool = False,
    workers: int = WORKERS,
    with_rows: bool = False,
) -> int:
    """The command line's fetch: `refresh`, printing how it ended. 0 when the file is current, 1 when anything failed."""
    report = refresh(root, db, only, skip, include_manual, workers=workers, with_rows=with_rows)
    print(report["message"], file=sys.stderr if report["state"] in ("failed", "offline", "partial") else sys.stdout)
    return 0 if report["state"] in ("ok", "unchanged") else 1


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
        ("serve", "show the dashboard from the file alone"),
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
            p.add_argument(
                "--with-rows",
                action="store_true",
                help="also fill the observations table (every row, for SQL); about ten times bigger, so not for a file you commit",
            )
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
        if name == "fetch":
            p.add_argument("--only", nargs="+", metavar="ID", help="call only these sources")
            p.add_argument(
                "--skip", nargs="+", metavar="ID", help="leave these sources out (the Internet Archive ones are slow)"
            )
            p.add_argument(
                "--workers",
                type=int,
                default=WORKERS,
                help="sources on different services run side by side, at most this many at once; 1 runs them one by one",
            )
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(
        line_buffering=True
    )  # a long fetch shows its progress as it goes, also when redirected to a file
    root = args.root.resolve()
    db = args.db or root / DEFAULT_DB
    if args.cmd == "fetch":
        return fetch(root, db, args.only, args.skip, args.include_manual, args.workers, args.with_rows)
    if args.cmd == "pack":
        c = pack(root, db, args.include_manual, with_rows=args.with_rows)
        print(
            f"packed {c['observations']:,} observations, {c['files']} files and {c['charts']} charts into {c['db']} ({c['bytes'] / 1e6:.1f} MB)"
        )
        for s in c["skipped"]:
            print(f"left out (vendor file; use --include-manual to pack it): {s}")
        return 0
    if args.cmd == "serve":
        return serve(db, root, args.host, args.port)
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
