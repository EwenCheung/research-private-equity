import json
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
