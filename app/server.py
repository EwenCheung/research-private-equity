"""FastAPI app: password session, read-only marts/registry/freshness API, and the built frontend.

Routes and shapes are defined in docs/contracts.md section 8.
"""

import hmac
import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field, StringConstraints

from app.db_store import DbStore
from app.refresh import Refresher
from contracts import freshness as freshness_rule
from pipeline.core.companies import load_companies

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 3600
MART_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
LOGIN_FAILURES, LOGIN_WINDOW = 10, 15 * 60  # wrong passwords allowed per client IP per window


class Login(BaseModel):
    password: str


class RefreshRequest(BaseModel):
    only: list[Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=64)]] = Field(
        default=[], max_length=64
    )


def refresh_everything(db: Path, progress: Callable[[dict], None], only: list[str] | None) -> dict:
    """What the refresh button runs: every source again (or just `only`), then a new SQLite file that replaces this one only if it is sound."""
    from pipeline import offline  # imported on the first press: reading the file needs no collectors

    return offline.refresh(ROOT, db, only=only, rebuild_web=False, progress=progress)


def create_app(
    data_dir: Path | None = None,
    frontend_dir: Path | None = None,
    db: Path | None = None,
    refresh: Callable[[Path, Callable[[dict], None], list[str] | None], dict] | None = None,
) -> FastAPI:
    """data_dir (or DATA_DIR) is the pipeline's data/: marts/ plus registry.json, written by `pipeline.build`.

    Unset means the Phase 0 fixtures. Their clock is pinned to the registry's generated_at so the Sample page keeps
    showing every freshness state; with real data, freshness is recomputed against the current time on every request.

    db (or DATA_DB) is the offline SQLite file written by `python -m pipeline.offline`: the charts and the registry are read from it
    instead of from data_dir, so a machine with no internet needs nothing else. With a file, the dashboard's Refresh button fetches
    every source again and swaps in a new file (`refresh` is that job; the default is pipeline.offline.refresh).
    """
    store = DbStore(db or os.environ["DATA_DB"]) if db or os.environ.get("DATA_DB") else None
    data_dir = Path(data_dir or os.environ.get("DATA_DIR") or FIXTURES)
    refresher = Refresher(store.path, refresh or refresh_everything) if store else None
    pinned_clock = not store and data_dir.resolve() == FIXTURES.resolve()
    marts_dir, registry_file = data_dir / "marts", data_dir / "registry.json"
    frontend_dir = Path(frontend_dir or os.environ.get("FRONTEND_DIR") or ROOT / "frontend" / "dist")

    password = os.environ.get("DASHBOARD_PASSWORD", "")
    secret = os.environ.get("SESSION_SECRET", "")
    secure_cookie = os.environ.get("COOKIE_SECURE", "") == "1"
    signer = URLSafeTimedSerializer(secret or "unset", salt="session")

    app = FastAPI(title="Signal Monitor", docs_url=None, redoc_url=None, openapi_url=None)

    def authenticated(request: Request) -> bool:
        token = request.cookies.get(COOKIE)
        if not token or not secret:
            return False
        try:
            return signer.loads(token, max_age=SESSION_MAX_AGE).get("ok") is True
        except BadSignature:
            return False

    def require_session(request: Request) -> None:
        if not authenticated(request):
            raise HTTPException(401, "Sign in required")

    # --- session ---
    # ponytail: failures are counted in memory per process (Render runs one); use a shared store if it scales out.
    failures: dict[str, list[float]] = {}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if not password or not secret:
            raise HTTPException(503, "Set DASHBOARD_PASSWORD and SESSION_SECRET to enable sign-in")
        ip, now = (request.client.host if request.client else "unknown"), time.monotonic()
        recent = [t for t in failures.get(ip, []) if now - t < LOGIN_WINDOW]
        if len(recent) >= LOGIN_FAILURES:
            raise HTTPException(429, "Too many wrong passwords; try again in 15 minutes")
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            failures[ip] = [*recent, now]
            raise HTTPException(401, "Wrong password")
        failures.pop(ip, None)
        response.set_cookie(
            COOKIE,
            signer.dumps({"ok": True}),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=secure_cookie,
        )
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"authenticated": False}

    @app.get("/api/session")
    def session(request: Request):
        return {"authenticated": authenticated(request)}

    # --- data (all behind the session) ---
    def load_registry() -> dict[str, dict]:
        """Sources from registry.json with freshness recomputed now, so a stalled pipeline shows without a rebuild."""
        reg = store.registry() if store else json.loads(registry_file.read_text()) if registry_file.is_file() else None
        if not reg:
            return {}
        now = datetime.fromisoformat(reg["generated_at"]) if pinned_clock else datetime.now(UTC)
        return {
            s["id"]: {
                **s,
                "freshness": freshness_rule(
                    datetime.fromisoformat(s["retrieved_at"]) if s["retrieved_at"] else None, s["sla_days"], now
                ),
            }
            for s in reg["sources"]
        }

    def live(mart: dict, reg: dict[str, dict]) -> dict:
        for s in mart["sources"]:
            if s["source"] in reg:
                s["freshness"] = reg[s["source"]]["freshness"]
        return mart

    def load_marts() -> list[dict]:
        return store.charts() if store else [json.loads(p.read_text()) for p in sorted(marts_dir.glob("*.json"))]

    @app.get("/api/marts", dependencies=[Depends(require_session)])
    def list_marts():
        return [{k: m[k] for k in ("id", "page", "title", "status", "as_of")} for m in load_marts()]

    @app.get("/api/marts/{mart_id}", dependencies=[Depends(require_session)])
    def get_mart(mart_id: str):
        path = marts_dir / f"{mart_id}.json"
        valid = MART_ID.match(mart_id)  # the pattern also blocks path traversal
        mart = (
            (store.chart(mart_id) if store else json.loads(path.read_text()) if path.is_file() else None)
            if valid
            else None
        )
        if mart is None:
            raise HTTPException(404, "No such chart")
        return live(mart, load_registry())

    @app.get("/api/freshness", dependencies=[Depends(require_session)])
    def freshness():
        return {sid: {k: s[k] for k in ("freshness", "retrieved_at", "as_of")} for sid, s in load_registry().items()}

    # --- the Refresh button: only when serving from a SQLite file ---
    if refresher:

        @app.get("/api/refresh", dependencies=[Depends(require_session)])
        def refresh_status():
            data = {k: v for k, v in store.meta().items() if k in ("packed_at", "observations", "charts")}
            return {**refresher.status(), "data": data}

        @app.post("/api/refresh", status_code=202, dependencies=[Depends(require_session)])
        def refresh_start(body: RefreshRequest | None = None):
            if not refresher.start(body.only if body else None):
                raise HTTPException(409, "A refresh is already running")
            return refresh_status()

    @app.get("/api/companies", dependencies=[Depends(require_session)])
    def companies():
        """Targets first, then each target's peers in config order. The frontend gives each a fixed colour slot."""
        cos = load_companies(ROOT)
        order = [c for c in cos.values() if c.role == "target"]
        order += [cos[p] for t in order for p in t.peers]
        order += [c for c in cos.values() if c not in order]
        return [{"slug": c.slug, "name": c.name, "role": c.role} for c in order]

    @app.get("/api/registry", dependencies=[Depends(require_session)])
    def registry():
        return list(load_registry().values())

    # --- built frontend (public: it holds no data, and the login page has to load) ---
    if (frontend_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            target = (frontend_dir / path).resolve()
            if path and target.is_file() and frontend_dir.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
