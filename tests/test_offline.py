import gzip
import json
import os
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

from pipeline import offline
from pipeline.core import ROOT, registry
from pipeline.core.build import build

ROW = {
    "source": "pypi_downloads",
    "source_url": "https://pypi.example/anthropic",
    "method": "api",
    "as_of": "2026-10-01",
    "retrieved_at": "2026-10-02T00:00:00Z",
    "tier": "platform",
    "entity": "anthropic",
    "metric": "pypi_downloads",
    "value": 1234,
    "dims": {"package": "anthropic", "role": "sdk"},
}
LEDGER = (
    "as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence\n"
    '2025-03-03,anthropic,round_post_money_usd,61500000000,"{""round"": ""Series E""}",https://x/e,tester,2026-10-05T00:00:00Z,a quote\n'
)


def raw(root, source, stamp, rows):
    path = root / "data" / "raw" / source / f"{stamp}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    return path


@pytest.fixture
def world(tmp_path):
    """A tiny data/: a raw snapshot, a retired source in an old shape, a ledger, a vendor file, a chart and a registry."""
    root = tmp_path / "src"
    raw(root, "pypi_downloads", "20261002T000000Z", [ROW, {**ROW, "as_of": "2026-10-02", "value": 99}])
    raw(root, "retired_source", "20250101T000000Z", [{"old": "shape"}])
    (root / "data" / "ledgers").mkdir(parents=True)
    (root / "data" / "ledgers" / "signal_funding_rounds.csv").write_text(LEDGER)
    (root / "data" / "manual").mkdir()
    (root / "data" / "manual" / "yipit_consumer.csv").write_text("as_of,entity\n2026-01-01,anthropic\n")
    (root / "data" / "marts").mkdir()
    chart = {
        "id": "x.y",
        "page": "x",
        "title": "T",
        "kind": "line",
        "as_of": "2026-10-02",
        "status": "ok",
        "generated_at": "2026-10-02T00:00:00Z",
    }
    (root / "data" / "marts" / "x.y.json").write_text(json.dumps(chart, indent=2))
    (root / "data" / "registry.json").write_text('{"generated_at": "2026-10-02T00:00:00Z", "sources": []}')
    return root


def rows(db, sql, *args):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def test_pack_keeps_every_observation_as_a_row_and_every_file_byte_for_byte(world, tmp_path):
    db = tmp_path / "out.sqlite"
    counts = offline.pack(world, db)
    assert (counts["observations"], counts["files"], counts["charts"]) == (3, 5 - 1, 1)  # the vendor file is left out
    assert rows(db, "SELECT source, metric, value FROM observations ORDER BY source, as_of") == [
        ("pypi_downloads", "pypi_downloads", 1234.0),
        ("pypi_downloads", "pypi_downloads", 99.0),
        ("signal_funding_rounds", "round_post_money_usd", 61500000000.0),
    ]
    stored = dict(rows(db, "SELECT path, sha256 FROM files"))
    for rel, digest in stored.items():
        assert offline.sha((world / rel).read_bytes()) == digest
    assert json.loads(rows(db, "SELECT dims FROM observations WHERE value = 99")[0][0]) == {
        "package": "anthropic",
        "role": "sdk",
    }


def test_a_retired_source_in_an_old_shape_is_kept_as_a_file_and_not_as_rows(world, tmp_path):
    db = tmp_path / "out.sqlite"
    counts = offline.pack(world, db)
    assert counts["unparsed"] == 1
    assert rows(db, "SELECT rows FROM files WHERE source = 'retired_source'") == [(None,)]
    assert rows(db, "SELECT COUNT(*) FROM observations WHERE source = 'retired_source'") == [(0,)]


def test_vendor_files_stay_out_unless_asked_for(world, tmp_path):
    out = offline.pack(world, tmp_path / "a.sqlite")
    assert out["skipped"] == ["data/manual/yipit_consumer.csv"]
    assert rows(tmp_path / "a.sqlite", "SELECT COUNT(*) FROM files WHERE kind = 'manual'") == [(0,)]
    offline.pack(world, tmp_path / "b.sqlite", include_manual=True)
    assert rows(tmp_path / "b.sqlite", "SELECT COUNT(*) FROM files WHERE kind = 'manual'") == [(1,)]


def test_restore_rebuilds_data_exactly_in_an_empty_place_and_check_agrees(world, tmp_path):
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    dest = tmp_path / "server"
    result = offline.restore(db, dest)
    assert result == {"written": 4, "kept": 0, "charts": 1}
    for rel in (
        "data/raw/pypi_downloads/20261002T000000Z.jsonl.gz",
        "data/ledgers/signal_funding_rounds.csv",
        "data/registry.json",
        "data/marts/x.y.json",
    ):
        assert (dest / rel).read_bytes() == (world / rel).read_bytes()
    assert not (dest / "data" / "manual").exists()
    assert offline.check(db, dest) == []


def test_restore_is_repeatable_and_never_overwrites_a_raw_file_that_differs(world, tmp_path):
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    dest = tmp_path / "server"
    offline.restore(db, dest)
    assert offline.restore(db, dest) == {
        "written": 1,
        "kept": 3,
        "charts": 1,
    }  # only the registry, which a build regenerates, is rewritten
    snapshot = dest / "data/raw/pypi_downloads/20261002T000000Z.jsonl.gz"
    snapshot.write_bytes(b"edited by hand")
    with pytest.raises(SystemExit, match="immutable"):
        offline.restore(db, dest)
    assert snapshot.read_bytes() == b"edited by hand"  # nothing was touched
    assert any("differs from the file" in p for p in offline.check(db, dest))


def test_check_notices_what_was_collected_after_the_pack(world, tmp_path):
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    raw(world, "pypi_downloads", "20261009T000000Z", [{**ROW, "as_of": "2026-10-09"}])
    assert offline.check(db, world) == [
        "on disk but not in the file (collected since the pack): data/raw/pypi_downloads/20261009T000000Z.jsonl.gz"
    ]


def test_a_stored_path_outside_data_is_refused(world, tmp_path):
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    con = sqlite3.connect(db)
    con.execute("UPDATE files SET path = '../../escaped.txt' WHERE kind = 'ledger'")
    con.commit()
    con.close()
    with pytest.raises(SystemExit, match="outside data/"):
        offline.restore(db, tmp_path / "server")


def test_fetch_calls_every_automated_source_once_then_rebuilds_then_packs_and_reports_failures(
    world, tmp_path, monkeypatch
):
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    order = []
    registry.discover()
    automated = sorted(s.id for s in registry.SOURCES.values() if s.meta["method"] in registry.AUTOMATED)

    def fake_collect(**kw):
        order.append(("collect", sorted(kw["source_ids"])))
        return {"pypi_downloads": None}, ["boom: no route"], ["openrouter_benchmarks: needs a key"]

    monkeypatch.setattr(collector, "collect", fake_collect)
    monkeypatch.setattr(builder, "build", lambda **kw: order.append(("build",)) or ([], []))
    db = tmp_path / "out.sqlite"
    code = offline.fetch(world, db, skip=["status_incidents_archive"])
    assert code == 1 and db.exists()  # a failed source is reported, and the file is still written
    assert order[0] == ("collect", [s for s in automated if s != "status_incidents_archive"]) and order[1] == ("build",)
    with pytest.raises(SystemExit, match="no source matches"):
        offline.fetch(world, db, only=["not_a_source"])


def test_load_env_reads_keys_without_overriding_what_is_set(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('# comment\nOFFLINE_TEST_A="one"\nOFFLINE_TEST_B=two\n\n')
    monkeypatch.setenv("OFFLINE_TEST_B", "kept")
    monkeypatch.delenv("OFFLINE_TEST_A", raising=False)
    offline.load_env(tmp_path / ".env")
    import os

    assert (os.environ["OFFLINE_TEST_A"], os.environ["OFFLINE_TEST_B"]) == ("one", "kept")
    monkeypatch.delenv("OFFLINE_TEST_A")


def test_an_offline_server_can_rebuild_every_chart_from_the_file_alone(tmp_path, monkeypatch):
    """The point of the file: restore the real data into an empty place, block every network connection, and build.

    The charts built there must equal the charts built on the original machine from the same data.
    """
    original = tmp_path / "original"
    for part in ("config", "data/raw", "data/ledgers"):
        shutil.copytree(ROOT / part, original / part)
    built_here, errors_here = build(root=original)
    assert errors_here == []

    db = tmp_path / "real.sqlite"
    offline.pack(original, db)
    server = tmp_path / "server"
    shutil.copytree(ROOT / "config", server / "config")
    offline.restore(db, server)

    def no_network(*a, **k):
        raise AssertionError("the build tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    built_there, errors_there = build(root=server)
    assert errors_there == [] and {p.name for p in built_there} == {p.name for p in built_here}
    for path in built_there:
        a, b = json.loads(path.read_text()), json.loads((original / "data" / "marts" / path.name).read_text())
        assert (a["id"], a["status"], a["as_of"], a["rows"]) == (b["id"], b["status"], b["as_of"], b["rows"])


def test_wikipedia_is_left_out_of_a_fetch_by_default_and_called_only_when_named(world, tmp_path, monkeypatch):
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    called = []
    monkeypatch.setattr(collector, "collect", lambda **kw: called.append(sorted(kw["source_ids"])) or ({}, [], []))
    monkeypatch.setattr(builder, "build", lambda **kw: ([], []))
    (world / "config").mkdir()
    shutil.copy(ROOT / "config" / "offline.yaml", world / "config" / "offline.yaml")
    assert offline.fetch(world, tmp_path / "a.sqlite") == 0
    assert "wikipedia_pageviews" not in called[0] and "pypi_downloads" in called[0]
    offline.fetch(world, tmp_path / "b.sqlite", only=["wikipedia_pageviews"])
    assert called[1] == ["wikipedia_pageviews"]


def with_dashboard(world):
    dist = world / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root>Signal Monitor</div>")
    (dist / "assets" / "app.js").write_text("console.log('dashboard')")
    return dist


def test_the_built_dashboard_travels_in_the_file_and_comes_out_the_same(world, tmp_path):
    with_dashboard(world)
    db = tmp_path / "out.sqlite"
    assert offline.pack(world, db)["web"] == 2
    out = tmp_path / "web"
    assert offline.extract_web(db, out) == 2
    assert (out / "index.html").read_text() == "<div id=root>Signal Monitor</div>" and (
        out / "assets" / "app.js"
    ).exists()
    server = tmp_path / "server"
    offline.restore(db, server)
    assert (server / "frontend" / "dist" / "index.html").exists()


def test_a_machine_that_only_serves_the_file_has_no_dashboard_on_disk_and_check_does_not_mind(world, tmp_path):
    with_dashboard(world)
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    shutil.rmtree(world / "frontend")
    assert offline.check(db, world) == []


def test_the_dashboard_is_served_from_the_file_alone_with_the_network_blocked(world, tmp_path, monkeypatch):
    """Sign in, list the charts and load the page, all from the SQLite file; nothing else is read."""
    from fastapi.testclient import TestClient

    from app.server import create_app

    with_dashboard(world)
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    web = tmp_path / "web"
    offline.extract_web(db, web)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "s")
    shutil.rmtree(world / "data")  # nothing but the file is left
    client = TestClient(create_app(frontend_dir=web, db=db))
    assert "Signal Monitor" in client.get("/").text
    assert client.get("/api/marts").status_code == 401
    client.post("/api/login", json={"password": "pw"})
    assert [m["id"] for m in client.get("/api/marts").json()] == ["x.y"]


def test_serve_needs_a_password_and_starts_the_app_on_the_file(world, tmp_path, monkeypatch):
    import uvicorn

    with_dashboard(world)
    db = tmp_path / "out.sqlite"
    offline.pack(world, db)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(SystemExit, match="DASHBOARD_PASSWORD"):
        offline.serve(db, world)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "s")
    started = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda *a, **k: started.update(
            args=a,
            kwargs=k,
            db=os.environ["DATA_DB"],
            web=os.environ["FRONTEND_DIR"],
            files=sorted(p.name for p in Path(os.environ["FRONTEND_DIR"]).iterdir()),
        ),
    )
    assert offline.serve(db, world, port=8123) == 0
    assert started["args"] == ("app.server:app",) and started["kwargs"]["port"] == 8123
    assert started["db"] == str(db.resolve()) and started["files"] == ["assets", "index.html"]
    assert not Path(started["web"]).exists()  # the temporary dashboard folder is cleaned up
