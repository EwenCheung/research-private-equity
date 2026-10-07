import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import contracts
from app.server import create_app

FIXTURES = Path(__file__).parent / "fixtures"
MART_IDS = sorted(p.stem for p in (FIXTURES / "marts").glob("*.json"))
PASSWORD = "test-password"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    return TestClient(create_app(frontend_dir=tmp_path))  # empty dir: no frontend mounted


@pytest.fixture
def authed(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return client


def test_data_routes_need_a_session(client):
    for path in ["/api/marts", f"/api/marts/{MART_IDS[0]}", "/api/registry", "/api/freshness"]:
        assert client.get(path).status_code == 401, path


def test_login_logout_session(client):
    assert client.get("/api/session").json() == {"authenticated": False}
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/session").json() == {"authenticated": False}
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/session").json() == {"authenticated": True}
    client.post("/api/logout")
    assert client.get("/api/session").json() == {"authenticated": False}
    assert client.get("/api/marts").status_code == 401


def test_forged_cookie_is_rejected(client):
    client.cookies.set("session", "not-a-signed-token")
    assert client.get("/api/marts").status_code == 401


def test_login_disabled_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    c = TestClient(create_app(frontend_dir=tmp_path))
    assert c.post("/api/login", json={"password": ""}).status_code == 503


def test_marts_list(authed):
    rows = authed.get("/api/marts").json()
    assert [r["id"] for r in rows] == MART_IDS
    assert set(rows[0]) == {"id", "page", "title", "status", "as_of"}


@pytest.mark.parametrize("mart_id", MART_IDS)
def test_each_mart_is_contract_valid(authed, mart_id):
    body = authed.get(f"/api/marts/{mart_id}").json()
    assert body == json.loads((FIXTURES / "marts" / f"{mart_id}.json").read_text())
    contracts.validate("chart_spec", body)


@pytest.mark.parametrize("mart_id", ["nope.missing", "../tests", "sample", "a.b.c"])
def test_unknown_mart_is_404(authed, mart_id):
    assert authed.get(f"/api/marts/{mart_id}").status_code == 404


def test_freshness_covers_every_state(authed):
    body = authed.get("/api/freshness").json()
    assert {v["freshness"] for v in body.values()} == {"fresh", "aging", "stale", "never"}
    assert set(body["mscience_panel"]) == {"freshness", "retrieved_at", "as_of"}
    assert body["mscience_panel"]["retrieved_at"] is None


def test_registry_lists_sources_with_counts(authed):
    reg = {s["id"]: s for s in authed.get("/api/registry").json()}
    assert len(reg) == len(json.loads((FIXTURES / "sources.json").read_text()))
    assert reg["greenhouse_jobs"]["row_count"] == 2
    assert reg["greenhouse_jobs"]["retrieved_at"] == "2026-10-05T06:02:11Z"
    assert reg["mscience_panel"]["freshness"] == "never"
    assert reg["mscience_panel"]["row_count"] == 0


def test_freshness_is_recomputed_live_from_the_registry(monkeypatch, tmp_path):
    """A mart built when everything was fresh must turn stale on its own if the pipeline stops."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    data = tmp_path / "data"
    (data / "marts").mkdir(parents=True)
    mart = json.loads((FIXTURES / "marts" / "sample.open_roles.json").read_text())
    assert {s["freshness"] for s in mart["sources"]} == {"fresh"}
    (data / "marts" / "sample.open_roles.json").write_text(json.dumps(mart))
    long_ago = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = json.loads((FIXTURES / "registry.json").read_text())
    for s in reg["sources"]:
        if s["id"] in ("greenhouse_jobs", "ashby_jobs"):
            s["retrieved_at"] = long_ago
    (data / "registry.json").write_text(json.dumps(reg))
    c = TestClient(create_app(data_dir=data, frontend_dir=tmp_path / "none"))
    c.post("/api/login", json={"password": PASSWORD})
    body = c.get("/api/marts/sample.open_roles").json()
    assert {s["freshness"] for s in body["sources"]} == {"stale"}  # sla_days 2, ten days without a collection
    contracts.validate("chart_spec", body)
    assert c.get("/api/freshness").json()["greenhouse_jobs"]["freshness"] == "stale"


def test_repeated_wrong_passwords_are_throttled(client):
    for _ in range(10):
        assert client.post("/api/login", json={"password": "guess"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_serves_built_frontend(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    c = TestClient(create_app(frontend_dir=tmp_path))
    assert "root" in c.get("/").text
    assert "root" in c.get("/sample").text  # client-side route falls back to index.html
    assert c.get("/api/marts").status_code == 401  # the catch-all never shadows the API
    assert c.get("/api/nope").status_code in (401, 404)


def test_companies_put_the_target_first_then_its_peers(authed):
    names = [c["name"] for c in authed.get("/api/companies").json()]
    assert names[0] == "Anthropic"
    assert names[1:] == ["OpenAI", "Google DeepMind", "xAI", "Mistral AI", "Cohere"]


# ---- the offline SQLite file (DATA_DB) ----


def offline_db(path, registry=None, version=1):
    """A file shaped like the one `python -m pipeline.offline pack` writes, from the fixture charts and registry."""
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE charts (id TEXT PRIMARY KEY, spec TEXT NOT NULL);"
        "CREATE TABLE files (path TEXT PRIMARY KEY, kind TEXT NOT NULL, content BLOB NOT NULL);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('packed_at', '2026-10-07T06:45:00Z'), ('observations', '70788'), ('charts', '26'), ('git_head', 'abc');"
    )
    con.executemany(
        "INSERT INTO charts VALUES (?, ?)", [(i, (FIXTURES / "marts" / f"{i}.json").read_text()) for i in MART_IDS]
    )
    reg = registry if registry is not None else (FIXTURES / "registry.json").read_text()
    con.execute("INSERT INTO files VALUES ('data/registry.json', 'registry', ?)", (reg.encode(),))
    con.execute(f"PRAGMA user_version = {version}")
    con.commit()
    con.close()
    return path


@pytest.fixture
def from_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    c = TestClient(create_app(frontend_dir=tmp_path, db=offline_db(tmp_path / "offline.sqlite")))
    c.post("/api/login", json={"password": PASSWORD})
    return c


def test_the_api_serves_charts_and_registry_from_the_sqlite_file_alone(from_file):
    assert [m["id"] for m in from_file.get("/api/marts").json()] == MART_IDS
    body = from_file.get(f"/api/marts/{MART_IDS[0]}").json()
    contracts.validate("chart_spec", body)
    assert body["id"] == MART_IDS[0]
    assert {s["id"] for s in from_file.get("/api/registry").json()} == {
        s["id"] for s in json.loads((FIXTURES / "registry.json").read_text())["sources"]
    }
    assert from_file.get("/api/marts/no.such_chart").status_code == 404
    assert from_file.get("/api/marts/..%2Fetc").status_code == 404


def test_freshness_from_the_file_is_judged_by_todays_clock_not_pinned_to_the_pack_date(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    reg = json.loads((FIXTURES / "registry.json").read_text())
    long_ago = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for s in reg["sources"]:
        if s["id"] in ("greenhouse_jobs", "ashby_jobs"):
            s["retrieved_at"] = long_ago
    c = TestClient(create_app(frontend_dir=tmp_path, db=offline_db(tmp_path / "o.sqlite", registry=json.dumps(reg))))
    c.post("/api/login", json={"password": PASSWORD})
    assert (
        c.get("/api/freshness").json()["greenhouse_jobs"]["freshness"] == "stale"
    )  # sla_days 2, ten days without a collection
    assert {s["freshness"] for s in c.get("/api/marts/sample.open_roles").json()["sources"]} == {"stale"}


def test_the_sqlite_file_is_read_only_behind_the_session(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("DATA_DB", str(offline_db(tmp_path / "offline.sqlite")))
    c = TestClient(create_app(frontend_dir=tmp_path))  # DATA_DB in the environment is enough
    assert c.get("/api/marts").status_code == 401
    c.post("/api/login", json={"password": PASSWORD})
    assert len(c.get("/api/marts").json()) == len(MART_IDS)
    con = sqlite3.connect(tmp_path / "offline.sqlite")
    assert con.execute("SELECT COUNT(*) FROM charts").fetchone()[0] == len(MART_IDS)  # untouched


def test_a_missing_or_wrong_version_sqlite_file_stops_the_app_with_a_clear_message(monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        create_app(frontend_dir=tmp_path, db=tmp_path / "nope.sqlite")
    with pytest.raises(RuntimeError, match="another schema version"):
        create_app(frontend_dir=tmp_path, db=offline_db(tmp_path / "old.sqlite", version=99))


def refresh_client(monkeypatch, tmp_path, work):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    c = TestClient(create_app(frontend_dir=tmp_path, db=offline_db(tmp_path / "offline.sqlite"), refresh=work))
    c.post("/api/login", json={"password": PASSWORD})
    return c


def until_idle(c):
    for _ in range(200):
        status = c.get("/api/refresh").json()
        if not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("the refresh never ended")


def test_the_refresh_button_exists_only_behind_the_session_and_only_when_serving_from_a_file(monkeypatch, tmp_path):
    c = refresh_client(monkeypatch, tmp_path, lambda db: {"state": "ok", "swapped": True, "message": "done"})
    assert TestClient(c.app).get("/api/refresh").status_code == 401  # a fresh client has no session
    assert TestClient(c.app).post("/api/refresh").status_code == 401
    status = c.get("/api/refresh").json()
    assert status["running"] is False and status["last"] is None
    assert status["data"] == {
        "packed_at": "2026-10-07T06:45:00Z",
        "observations": "70788",
        "charts": "26",
    }  # no git_head
    plain = TestClient(create_app(frontend_dir=tmp_path))  # fixtures, no file
    plain.post("/api/login", json={"password": PASSWORD})
    assert plain.get("/api/refresh").status_code == 404 and plain.post("/api/refresh").status_code == 404


def test_a_refresh_runs_in_the_background_one_at_a_time_and_reports_how_it_ended(monkeypatch, tmp_path):
    gate = threading.Event()

    def work(db):
        gate.wait(5)
        return {
            "state": "partial",
            "swapped": True,
            "message": "Updated 18 sources; 1 failed",
            "failed": {"x": ["boom"]},
        }

    c = refresh_client(monkeypatch, tmp_path, work)
    assert c.post("/api/refresh").status_code == 202
    assert c.get("/api/refresh").json()["running"] is True
    assert c.post("/api/refresh").status_code == 409  # a second press while one runs
    assert len(c.get("/api/marts").json()) == len(MART_IDS)  # the dashboard keeps serving meanwhile
    gate.set()
    last = until_idle(c)["last"]
    assert (
        last["state"] == "partial" and last["failed"] == {"x": ["boom"]} and last["finished_at"] >= last["started_at"]
    )
    assert json.loads((tmp_path / "offline.sqlite.refresh.json").read_text())["message"] == last["message"]
    assert c.post("/api/refresh").status_code == 202  # free again
    until_idle(c)


def test_a_refresh_that_crashes_is_reported_not_lost_and_the_next_one_can_start(monkeypatch, tmp_path):
    calls = []

    def work(db):
        calls.append(db)
        if len(calls) == 1:
            raise RuntimeError("collector blew up")
        raise SystemExit("no source matches")

    c = refresh_client(monkeypatch, tmp_path, work)
    c.post("/api/refresh")
    last = until_idle(c)["last"]
    assert last["state"] == "failed" and last["swapped"] is False and "collector blew up" in last["message"]
    c.post("/api/refresh")
    assert "no source matches" in until_idle(c)["last"]["message"]
    assert len(c.get("/api/marts").json()) == len(MART_IDS)


def test_the_last_refresh_report_survives_a_restart(monkeypatch, tmp_path):
    c = refresh_client(
        monkeypatch, tmp_path, lambda db: {"state": "offline", "swapped": False, "message": "No internet"}
    )
    c.post("/api/refresh")
    until_idle(c)
    again = TestClient(create_app(frontend_dir=tmp_path, db=tmp_path / "offline.sqlite", refresh=lambda db: {}))
    again.post("/api/login", json={"password": PASSWORD})
    assert again.get("/api/refresh").json()["last"]["message"] == "No internet"
