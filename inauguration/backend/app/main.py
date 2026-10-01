"""Application factory.

Everything the app depends on is passed in rather than imported as a global, so the
test suite can stand the whole system up against a temporary directory — including
the Nginx include path and the decommission script — and drive the failure paths
without a VM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__, audit
from .auth import AdminStore, LoginRateLimiter, SessionManager
from .config import Settings
from .nginx_mode import NginxController
from .routes_admin import router as admin_router
from .routes_cleanup import router as cleanup_router
from .routes_public import router as public_router
from .security import security_headers_for, set_session_cookies


@dataclass
class AppContext:
    settings: Settings
    sessions: SessionManager
    admin_store: AdminStore
    rate_limiter: LoginRateLimiter
    nginx: NginxController
    #: Injectable so tests can simulate a failing decommission without root.
    decommission_runner: Callable[[Settings], tuple[bool, str]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def create_app(
    settings: Settings | None = None,
    *,
    nginx_run: Callable[..., object] | None = None,
    secret: bytes | None = None,
    decommission_runner: Callable[[Settings], tuple[bool, str]] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()

    if secret is None:
        secret = settings.load_secret()

    ctx = AppContext(
        settings=settings,
        sessions=SessionManager(settings, secret),
        admin_store=AdminStore(settings),
        rate_limiter=LoginRateLimiter(
            max_attempts=settings.login_max_attempts,
            lockout_seconds=settings.login_lockout_minutes * 60,
        ),
        nginx=NginxController(settings, run=nginx_run),
        decommission_runner=decommission_runner,
    )

    app = FastAPI(
        title="NSS IIIT-NR inauguration administration",
        version=__version__,
        # The admin API is not a public surface; no interactive docs are exposed.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = ctx

    app.include_router(public_router)
    app.include_router(admin_router)
    app.include_router(cleanup_router)

    @app.middleware("http")
    async def session_and_headers(request: Request, call_next):
        # Collected by require_admin and applied here, so the idle window slides on
        # every authenticated call without each route having to remember to do it.
        request.state.refresh_cookies = []

        response = await call_next(request)

        # A route that has just cleared the session must not have it handed straight
        # back by the refresh below — logout would appear to work and not.
        if not getattr(request.state, "skip_cookie_refresh", False):
            for username, csrf in request.state.refresh_cookies:
                record = ctx.admin_store.load()
                if record is not None and record.username == username:
                    set_session_cookies(
                        response, settings, ctx.sessions, username, record.session_epoch, csrf
                    )

        # The policy depends on which surface is answering, so it is chosen from the
        # request path rather than being one fixed string for the whole application.
        for header, value in security_headers_for(request.url.path).items():
            response.headers.setdefault(header, value)
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Liveness plus the facts the smoke script needs, with no secrets in it."""
        state = None
        try:
            from .state import read_state

            state = read_state(settings)["site_mode"]
        except Exception:  # noqa: BLE001 - health must answer even when state is broken
            state = "unreadable"
        return {
            "ok": True,
            "version": __version__,
            "site_mode": state,
            "nginx_include": str(settings.nginx_mode_inc),
        }

    audit.record(settings, "service.start", detail={"version": __version__})
    return app


def __getattr__(name: str):
    """Build ``app`` on first attribute access.

    Deliberately not a module-level ``app = create_app()``: importing this module in
    a test must not reach for the production paths under /opt and /etc.
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app", "AppContext"]
