"""FastAPI app: password session, read-only marts/registry/freshness API, and the built frontend.

Routes and shapes are defined in docs/contracts.md section 8.
"""

import hmac
import json
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 3600
MART_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


class Login(BaseModel):
    password: str


def create_app(
    marts_dir: Path | None = None,
    sources_file: Path | None = None,
    observations_file: Path | None = None,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """Paths default from the environment; unset means the Phase 0 fixtures until the data core lands."""
    marts_dir = Path(marts_dir or os.environ.get("MARTS_DIR") or FIXTURES / "marts")
    sources_file = Path(sources_file or os.environ.get("SOURCES_FILE") or FIXTURES / "sources.json")
    observations_file = Path(
        observations_file or os.environ.get("OBSERVATIONS_FILE") or FIXTURES / "observations.jsonl"
    )
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
    # ponytail: no login rate limit; one shared password behind Render's TLS. Add one if the URL leaks.
    @app.post("/api/login")
    def login(body: Login, response: Response):
        if not password or not secret:
            raise HTTPException(503, "Set DASHBOARD_PASSWORD and SESSION_SECRET to enable sign-in")
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            raise HTTPException(401, "Wrong password")
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
    def load_marts() -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(marts_dir.glob("*.json"))]

    @app.get("/api/marts", dependencies=[Depends(require_session)])
    def list_marts():
        return [{k: m[k] for k in ("id", "page", "title", "status", "as_of")} for m in load_marts()]

    @app.get("/api/marts/{mart_id}", dependencies=[Depends(require_session)])
    def get_mart(mart_id: str):
        path = marts_dir / f"{mart_id}.json"
        if not MART_ID.match(mart_id) or not path.is_file():  # the pattern also blocks path traversal
            raise HTTPException(404, "No such chart")
        return json.loads(path.read_text())

    def source_state() -> dict[str, dict]:
        """Latest freshness per source, taken from the marts, where the core already applied the SLA rule."""
        state: dict[str, dict] = {}
        for mart in load_marts():
            for s in mart["sources"]:
                cur = state.get(s["source"])
                if cur is None or (s["retrieved_at"] or "") > (cur["retrieved_at"] or ""):
                    state[s["source"]] = {k: s[k] for k in ("freshness", "retrieved_at", "as_of")}
        return state

    @app.get("/api/freshness", dependencies=[Depends(require_session)])
    def freshness():
        return source_state()

    @app.get("/api/registry", dependencies=[Depends(require_session)])
    def registry():
        sources = json.loads(sources_file.read_text()) if sources_file.is_file() else []
        counts: dict[str, int] = {}
        latest: dict[str, str] = {}
        if observations_file.is_file():
            for line in observations_file.read_text().splitlines():
                if line.strip():
                    o = json.loads(line)
                    counts[o["source"]] = counts.get(o["source"], 0) + 1
                    latest[o["source"]] = max(latest.get(o["source"], ""), o["retrieved_at"])
        state = source_state()
        return [
            {
                **s,
                "retrieved_at": latest.get(s["id"]) or state.get(s["id"], {}).get("retrieved_at"),
                "row_count": counts.get(s["id"], 0),
                "freshness": state.get(s["id"], {}).get("freshness", "never"),
            }
            for s in sources
        ]

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
