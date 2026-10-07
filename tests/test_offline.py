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


@pytest.fixture(autouse=True)
def internet(monkeypatch):
    """The tests never touch the network: `online` is answered here, and the collectors are replaced where one runs."""
    monkeypatch.setattr(offline, "online", lambda: True)


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


def test_sources_in_the_skip_list_are_left_out_of_a_fetch_and_called_only_when_named(world, tmp_path, monkeypatch):
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    called = []
    monkeypatch.setattr(collector, "collect", lambda **kw: called.append(sorted(kw["source_ids"])) or ({}, [], []))
    monkeypatch.setattr(builder, "build", lambda **kw: ([], []))
    (world / "config").mkdir()
    (world / "config" / "offline.yaml").write_text("skip: [npm_downloads]\n")
    assert offline.fetch(world, tmp_path / "a.sqlite") == 0
    assert "npm_downloads" not in called[0] and "pypi_downloads" in called[0]
    offline.fetch(world, tmp_path / "b.sqlite", only=["npm_downloads"])
    assert called[1] == ["npm_downloads"]


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


# --- refresh: replace the file only with a better one ---


@pytest.fixture
def live(world, tmp_path):
    """A file already packed from `world`, as the dashboard would be serving it."""
    db = tmp_path / "live.sqlite"
    offline.pack(world, db)
    return db


def fake_run(monkeypatch, collect, build=lambda **kw: ([], [])):
    from pipeline.core import build as builder
    from pipeline.core import collect as collector

    monkeypatch.setattr(collector, "collect", collect)
    monkeypatch.setattr(builder, "build", build)


def snapshot(root, source, stamp="20261008T000000Z", value=7):
    return raw(root, source, stamp, [{**ROW, "source": source, "as_of": "2026-10-08", "value": value}])


def count(db, table="observations"):
    return rows(db, f"SELECT COUNT(*) FROM {table}")[0][0]


def test_a_refresh_replaces_the_file_with_the_new_data_and_keeps_the_old_file_as_previous(world, live, monkeypatch):
    old = live.read_bytes()
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": snapshot(kw["root"], "pypi_downloads")}, [], []))
    report = offline.refresh(world, live)
    assert report["state"] == "ok" and report["swapped"] and report["updated"] == ["pypi_downloads"]
    assert (
        count(live) == 4 and report["observations"] == 4
    )  # the two snapshot rows and the ledger row it had, plus the new one
    assert (live.with_name("live.sqlite.previous")).read_bytes() == old
    assert not live.with_name("live.sqlite.new").exists()


def test_with_no_internet_nothing_is_called_and_the_file_stays(world, live, monkeypatch):
    monkeypatch.setattr(offline, "online", lambda: False)
    fake_run(monkeypatch, lambda **kw: pytest.fail("a collector ran with no internet"))
    old = live.read_bytes()
    report = offline.refresh(world, live)
    assert report["state"] == "offline" and not report["swapped"] and "No internet" in report["message"]
    assert live.read_bytes() == old and not live.with_name("live.sqlite.previous").exists()


def test_a_source_that_errors_keeps_its_previous_data_and_what_it_collected_is_set_aside(world, live, monkeypatch):
    def collect(source_ids, root, **kw):
        return (
            {
                "pypi_downloads": snapshot(root, "pypi_downloads", value=1),
                "hn_stories": snapshot(root, "hn_stories", value=5),
            },
            ["pypi_downloads/anthropic: HTTPError: 500"],
            [],
        )

    fake_run(monkeypatch, collect)
    report = offline.refresh(world, live)
    assert report["state"] == "partial" and report["swapped"] and report["updated"] == ["hn_stories"]
    assert list(report["failed"]) == ["pypi_downloads"] and "keep their previous data" in report["message"]
    stored = {p for (p,) in rows(live, "SELECT path FROM files WHERE kind = 'raw'")}
    assert "data/raw/hn_stories/20261008T000000Z.jsonl.gz" in stored
    assert (
        "data/raw/pypi_downloads/20261008T000000Z.jsonl.gz" not in stored
    )  # the partial snapshot did not reach the file
    assert "data/raw/pypi_downloads/20261002T000000Z.jsonl.gz" in stored  # the data it had is still there
    aside = world / "data" / "raw" / "_rejected" / "pypi_downloads" / "20261008T000000Z.jsonl.gz"
    assert aside.exists() and report["set_aside"] == [aside.relative_to(world).as_posix()]


def test_when_every_source_fails_the_file_is_untouched(world, live, monkeypatch):
    old = live.read_bytes()
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": None}, ["pypi_downloads/anthropic: ConnectError: down"], []))
    report = offline.refresh(world, live)
    assert report["state"] == "failed" and not report["swapped"] and "pypi_downloads failed" in report["message"]
    assert live.read_bytes() == old


def test_nothing_new_is_not_a_swap(world, live, monkeypatch):
    old = live.read_bytes()
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": None}, [], []))
    report = offline.refresh(world, live)
    assert report["state"] == "unchanged" and not report["swapped"] and live.read_bytes() == old


def test_a_build_that_fails_rolls_the_whole_refresh_back(world, live, monkeypatch):
    old = live.read_bytes()
    fake_run(
        monkeypatch,
        lambda **kw: ({"pypi_downloads": snapshot(kw["root"], "pypi_downloads")}, [], []),
        build=lambda **kw: ([], ["signal.tested: ValueError: boom"]),
    )
    report = offline.refresh(world, live)
    assert (
        report["state"] == "failed"
        and not report["swapped"]
        and "Rolled back: build: signal.tested" in report["message"]
    )
    assert live.read_bytes() == old and not live.with_name("live.sqlite.new").exists()
    # the new snapshot stays on disk, and the next refresh that builds fine puts it in
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": None}, [], []))
    assert offline.refresh(world, live)["state"] == "ok" and count(live) == 4


def test_a_refresh_that_would_change_a_raw_snapshot_is_rolled_back(world, live, monkeypatch):
    def collect(source_ids, root, **kw):
        raw(root, "pypi_downloads", "20261002T000000Z", [{**ROW, "value": 1}]).write_bytes(
            b"tampered"
        )  # raw files are immutable
        return {"pypi_downloads": snapshot(root, "pypi_downloads")}, [], []

    old = live.read_bytes()
    fake_run(monkeypatch, collect)
    report = offline.refresh(world, live)
    assert (
        report["state"] == "failed" and "raw snapshot lost or changed" in report["message"] and live.read_bytes() == old
    )


def test_a_checkout_without_the_raw_files_gets_the_history_back_before_it_refreshes(world, live, monkeypatch):
    shutil.rmtree(world / "data" / "raw")  # a fresh clone does not hold what earlier refreshes collected
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": snapshot(kw["root"], "pypi_downloads")}, [], []))
    report = offline.refresh(world, live)
    assert report["state"] == "ok" and count(live) == 4  # nothing the file held was lost


def test_a_refresh_carries_the_dashboard_over_when_the_machine_has_no_build(world, tmp_path, monkeypatch):
    with_dashboard(world)
    live = tmp_path / "live.sqlite"
    offline.pack(world, live)
    shutil.rmtree(world / "frontend")  # a machine that only serves the file
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": snapshot(kw["root"], "pypi_downloads")}, [], []))
    assert offline.refresh(world, live, rebuild_web=False)["state"] == "ok"
    assert offline.extract_web(live, tmp_path / "web") == 2


def test_the_first_file_is_written_even_when_a_source_failed(world, tmp_path, monkeypatch):
    fake_run(monkeypatch, lambda **kw: ({"pypi_downloads": None}, ["pypi_downloads/anthropic: boom"], []))
    db = tmp_path / "first.sqlite"
    assert offline.refresh(world, db)["state"] == "partial" and db.exists()


def test_the_online_probe_answers_no_when_nothing_is_reachable(monkeypatch):
    import httpx

    monkeypatch.undo()  # this test is about the real probe, with the network call stubbed to fail
    monkeypatch.setattr(httpx, "head", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("blocked")))
    assert offline.online() is False
    monkeypatch.setattr(httpx, "head", lambda *a, **k: object())
    assert offline.online() is True


def test_the_refresh_button_runs_the_refresh_on_the_file_the_api_serves(world, live, tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app import server

    called = []
    monkeypatch.setattr(
        offline,
        "refresh",
        lambda root, db, **kw: called.append((root, db, kw)) or {"state": "ok", "swapped": False, "message": "m"},
    )
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "s")
    client = TestClient(server.create_app(frontend_dir=tmp_path, db=live))
    client.post("/api/login", json={"password": "pw"})
    assert client.post("/api/refresh").status_code == 202
    while client.get("/api/refresh").json()["running"]:
        time.sleep(0.02)
    ((root, db, kw),) = called
    assert (root, db, kw["rebuild_web"]) == (
        server.ROOT,
        live.resolve(),
        False,
    )  # no Node needed on the machine that serves
    assert callable(kw["progress"])


def test_a_refresh_shows_each_source_as_it_goes_for_the_command_line_and_the_dashboard(world, live, monkeypatch):
    def collect(source_ids, root, on_progress, **kw):
        for event in (
            ("pypi_downloads", 0, 2, "running"),
            ("pypi_downloads", 1, 2, "running"),
            ("pypi_downloads", 2, 2, "running"),
            ("pypi_downloads", 2, 2, "done"),
            ("hn_stories", 0, 2, "running"),
            ("hn_stories", 2, 2, "failed"),
        ):
            on_progress(*event)
        return (
            {"pypi_downloads": snapshot(root, "pypi_downloads"), "hn_stories": None},
            ["hn_stories/anthropic: boom"],
            [],
        )

    fake_run(monkeypatch, collect)
    lines, snapshots = [], []
    offline.refresh(world, live, log=lambda *a, **k: lines.append(" ".join(map(str, a))), progress=snapshots.append)
    total = len(snapshots[0]["sources"])
    assert lines[1:8] == [
        f"data collected (0/{total}):",
        "  collect pypi_downloads 50%…",
        "  collect pypi_downloads 100%…",
        "  collect pypi_downloads DONE",
        f"data collected (1/{total}):",
        "  collect hn_stories FAILED",
        f"data collected (2/{total}):",
    ]
    collecting = [s for s in snapshots if s["stage"] == "Collecting sources"]
    last = {x["id"]: x for x in collecting[-1]["sources"]}
    assert (last["pypi_downloads"]["state"], last["pypi_downloads"]["percent"]) == ("done", 100)
    assert last["hn_stories"]["state"] == "failed" and last["npm_downloads"]["state"] == "waiting"
    assert collecting[-1]["finished"] == 2 and collecting[0]["finished"] == 0
    assert [s["stage"] for s in snapshots][-3:] == [
        "Rebuilding the charts",
        "Packing the new file",
        "Checking the new file",
    ]


def test_times_in_messages_are_singapore_time():
    assert offline.when("2026-10-07T07:08:39Z") == "2026-10-07 15:08 SGT"
    assert offline.when("2026-10-07T20:00:00Z") == "2026-10-08 04:00 SGT"  # across midnight


def test_a_refresh_fetches_in_parallel_by_default_and_the_command_line_can_turn_it_off(world, live, monkeypatch):
    seen = []
    fake_run(monkeypatch, lambda **kw: seen.append(kw["workers"]) or ({"pypi_downloads": None}, [], []))
    offline.refresh(world, live)
    offline.refresh(world, live, workers=1)
    assert seen == [offline.WORKERS, 1] and offline.WORKERS > 1


def test_only_names_that_are_not_automated_sources_are_refused_even_next_to_good_ones(world, live):
    with pytest.raises(SystemExit, match="no source matches not_a_source"):
        offline.refresh(world, live, only=["pypi_downloads", "not_a_source"])


def test_retrying_only_the_failed_sources_collects_exactly_those_and_the_report_shrinks(world, live, monkeypatch):
    calls = []

    def first(source_ids, root, **kw):
        calls.append(sorted(source_ids))
        return (
            {
                "pypi_downloads": snapshot(root, "pypi_downloads", "20261008T000000Z"),
                "hn_stories": None,
                "npm_downloads": None,
            },
            ["hn_stories/anthropic: HTTPError: 503", "npm_downloads/openai: ConnectError: down"],
            [],
        )

    fake_run(monkeypatch, first)
    report = offline.refresh(world, live)
    assert report["state"] == "partial" and sorted(report["failed"]) == ["hn_stories", "npm_downloads"]

    def second(source_ids, root, **kw):
        calls.append(sorted(source_ids))
        return (
            {"hn_stories": snapshot(root, "hn_stories", "20261009T000000Z"), "npm_downloads": None},
            ["npm_downloads/openai: ConnectError: still down"],
            [],
        )

    fake_run(monkeypatch, second)
    again = offline.refresh(world, live, only=sorted(report["failed"]))
    assert calls[1] == ["hn_stories", "npm_downloads"]  # nothing else is called again
    assert (
        again["state"] == "partial"
        and again["updated"] == ["hn_stories"]
        and list(again["failed"]) == ["npm_downloads"]
    )
    stored = {p for (p,) in rows(live, "SELECT path FROM files WHERE kind = 'raw'")}
    assert "data/raw/hn_stories/20261009T000000Z.jsonl.gz" in stored  # the retry's data reached the file
