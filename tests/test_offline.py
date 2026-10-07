import gzip
import json
import shutil
import socket
import sqlite3

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
    """The point of the file: restore the real data into an empty place, block every network connection, and build."""
    db = tmp_path / "real.sqlite"
    offline.pack(ROOT, db)
    server = tmp_path / "server"
    shutil.copytree(ROOT / "config", server / "config")
    offline.restore(db, server)

    def no_network(*a, **k):
        raise AssertionError("the build tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    written, errors = build(root=server)
    packed = {p.name: json.loads(p.read_text()) for p in (ROOT / "data" / "marts").glob("*.json")}
    frozen = {
        n for n in packed if n.startswith(("hot_pick.week_", "hot_pick.month_"))
    }  # saved by `freeze`, not regenerated by a build
    assert errors == [] and {p.name for p in written} == set(packed) - frozen
    for name in frozen:
        assert (server / "data" / "marts" / name).read_text() == (
            ROOT / "data" / "marts" / name
        ).read_text()  # restored, byte for byte
    for path in written:
        rebuilt = json.loads(path.read_text())
        assert (rebuilt["id"], rebuilt["status"], rebuilt["rows"]) == tuple(
            packed[path.name][k] for k in ("id", "status", "rows")
        )
