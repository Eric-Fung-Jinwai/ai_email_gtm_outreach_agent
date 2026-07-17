"""FastAPI application entrypoint for the multi-user web app.

Run with: ``uvicorn server.main:app --reload`` (single worker — the SSE job
registry in ``server.runs`` is in-process; see its docstring).
"""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from backend.config import get_settings
from backend.observability import configure_logging
from server import auth, runs
from server.deps import current_user
from server.schemas import ConfigOut

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.require_web_config()  # fail fast on a default secret in production

    app = FastAPI(title="GTM Outreach — multi-user")

    # Signed-cookie sessions. httponly is on by default; require HTTPS + stricter
    # handling outside development (point 7).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.is_production,
        same_site="lax",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Baseline hardening headers. The self-contained same-origin frontend
        needs no external/inline resources, so a strict CSP is safe. Clickjacking
        is blocked via frame-ancestors; HSTS is only meaningful over HTTPS (prod)."""
        resp = await call_next(request)
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if settings.is_production:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return resp

    # IMPORTANT: register /api routes BEFORE mounting the static frontend at "/",
    # so the catch-all mount can't shadow the API (point 9).
    app.include_router(auth.router)
    app.include_router(runs.router)

    @app.get("/api/config", response_model=ConfigOut)
    def config(_: int = Depends(current_user)) -> ConfigOut:
        s = get_settings()
        # Booleans only — never leak configuration values or secrets (point 10).
        return ConfigOut(has_openai=s.has_openai_key, has_exa=s.has_exa_key)

    if _FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
