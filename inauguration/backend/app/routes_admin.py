"""Admin portal routes.

Every route here except ``login`` requires a live admin session; the state-changing
ones additionally require a CSRF token and a same-origin request. ``login`` is the
only route with no authorization at all, so it is the one that carries the rate limit.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from . import audit
from .auth import (
    AdminStore,
    LockedOut,
    SessionManager,
    new_csrf_token,
)
from .models import ActionResponse, LoginRequest, ScheduleRequest
from .security import (
    AdminSession,
    clear_session_cookies,
    client_ip,
    require_admin,
    require_admin_with_csrf,
    set_session_cookies,
)
from .service import ModeApplyFailed, set_site_mode
from .state import InvalidState, InvalidTransition, read_state, update_state

router = APIRouter(prefix="/api/admin", tags=["admin"])

CLEANUP_PHRASE = "DELETE INAUGURATION SYSTEM"


def _dashboard_state(request: Request, session: AdminSession) -> dict:
    ctx = request.app.state.ctx
    state = read_state(ctx.settings)
    return {
        **state,
        "admin": session.username,
        "csrf": session.csrf,
        "cleanup_phrase": CLEANUP_PHRASE,
        "cleanup_available": bool(
            state["inauguration_completed"] and not state["decommissioned"]
        ),
        "activity": audit.read_recent(ctx.settings, limit=15),
    }


@router.post("/login", response_model=ActionResponse)
def login(request: Request, response: Response, body: LoginRequest) -> ActionResponse:
    ctx = request.app.state.ctx
    store: AdminStore = ctx.admin_store
    sessions: SessionManager = ctx.sessions
    limiter = ctx.rate_limiter
    ip = client_ip(request)

    try:
        limiter.check(ip)
    except LockedOut as exc:
        audit.record(ctx.settings, "admin.login.locked", ip=ip,
                     detail={"retry_after": exc.retry_after})
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"too many failed attempts; try again in {exc.retry_after}s",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    record = store.authenticate(body.username, body.password)
    if record is None:
        limiter.record_failure(ip)
        # The username is recorded only as a hash-free presence flag: a failed login
        # is worth auditing, but not worth storing whatever was typed into the field.
        audit.record(ctx.settings, "admin.login.failed", ip=ip,
                     detail={"username_supplied": bool(body.username)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    limiter.record_success(ip)
    csrf = new_csrf_token()
    set_session_cookies(response, ctx.settings, sessions, record.username, record.session_epoch, csrf)
    audit.record(ctx.settings, "admin.login", actor=record.username, ip=ip)

    return ActionResponse(
        ok=True,
        message="signed in",
        state=_dashboard_state(request, AdminSession(record.username, csrf, "")),
    )


@router.post("/logout", response_model=ActionResponse)
def logout(request: Request, response: Response) -> ActionResponse:
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)
    request.state.skip_cookie_refresh = True
    clear_session_cookies(response)
    audit.record(ctx.settings, "admin.logout", actor=session.username, ip=client_ip(request))
    return ActionResponse(ok=True, message="signed out")


@router.post("/logout-all", response_model=ActionResponse)
def logout_all(request: Request, response: Response) -> ActionResponse:
    """Revoke every outstanding session by advancing the epoch."""
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)
    request.state.skip_cookie_refresh = True
    epoch = ctx.admin_store.bump_epoch()
    clear_session_cookies(response)
    audit.record(ctx.settings, "admin.logout_all", actor=session.username,
                 ip=client_ip(request), detail={"session_epoch": epoch})
    return ActionResponse(ok=True, message="all sessions revoked")


@router.get("/verify")
def verify(request: Request) -> Response:
    """Whether the caller holds a live session. Used by the smoke script.

    Returns 200 with an empty body when the caller holds a session, 401 otherwise.
    No detail is returned, because the consumer is a health check, not a browser.
    """
    try:
        require_admin(request)
    except HTTPException:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    return Response(status_code=status.HTTP_200_OK)


@router.get("/preview")
def preview(request: Request) -> Response:
    """The ceremony page, for an admin only.

    Lets the director walk the ceremony before the public can see it. Served from the
    backend rather than straight off disk so the gate is an ordinary dependency; the
    page's relative asset paths still resolve, because ``/preview`` is a single path
    segment and the base stays ``/``.
    """
    ctx = request.app.state.ctx
    require_admin(request)

    page = ctx.settings.web_root / "inauguration.html"
    try:
        # Read as bytes, not text: the public copy is served by Nginx straight off
        # disk, and a text-mode read would silently normalise CRLF to LF and hand the
        # admin a file that is not quite the one the audience will get. What is
        # previewed should be byte-for-byte what is published.
        body = page.read_bytes()
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "the ceremony page is not deployed"
        ) from None

    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/state", response_model=ActionResponse)
def dashboard(request: Request) -> ActionResponse:
    session: AdminSession = require_admin(request)
    return ActionResponse(ok=True, state=_dashboard_state(request, session))


@router.post("/schedule", response_model=ActionResponse)
def set_schedule(request: Request, body: ScheduleRequest) -> ActionResponse:
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)

    def mutate(state: dict) -> dict:
        state["scheduled_at"] = body.scheduled_at
        if body.countdown_enabled is not None:
            state["countdown_enabled"] = body.countdown_enabled
        return state

    try:
        updated = update_state(ctx.settings, mutate)
    except InvalidState as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(ctx.settings, "admin.schedule", actor=session.username,
                 ip=client_ip(request),
                 detail={"scheduled_at": updated["scheduled_at"],
                         "countdown_enabled": updated["countdown_enabled"]})
    return ActionResponse(ok=True, message="schedule updated",
                          state=_dashboard_state(request, session))


@router.post("/ceremony/enable", response_model=ActionResponse)
def enable_ceremony(request: Request) -> ActionResponse:
    return _move(request, "inauguration", "admin.ceremony.enable", "ceremony enabled")


@router.post("/ceremony/disable", response_model=ActionResponse)
def disable_ceremony(request: Request) -> ActionResponse:
    return _move(request, "coming_soon", "admin.ceremony.disable", "ceremony disabled")


def _move(request: Request, mode: str, event: str, message: str) -> ActionResponse:
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)
    ip = client_ip(request)

    try:
        set_site_mode(ctx, mode)
    except ModeApplyFailed as exc:
        audit.record(ctx.settings, f"{event}.failed", actor=session.username, ip=ip,
                     detail={"message": exc.result.message})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"routing could not be updated: {exc.result.message}",
        ) from exc
    except (InvalidTransition, InvalidState) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    audit.record(ctx.settings, event, actor=session.username, ip=ip, detail={"mode": mode})
    return ActionResponse(ok=True, message=message,
                          state=_dashboard_state(request, session))


__all__ = ["router", "CLEANUP_PHRASE"]
