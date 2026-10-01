"""Public routes: what the coming-soon page and the ceremony page are allowed to read,
and the one state-changing route the ceremony page calls.

None of these are open by accident. The cut route is admin-only and CSRF-checked; the
read routes expose nothing that is not already public.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from . import audit
from .auth import AdminStore, NotAuthenticated, SessionManager
from .models import CeremonyState, CutRequest, PublicConfig
from .security import AdminSession, client_ip, load_session, require_admin_with_csrf
from .service import AlreadyCompleted, ModeApplyFailed, complete_inauguration
from .state import read_state

router = APIRouter(prefix="/api", tags=["public"])

SITE_NAME = "NSS IIIT Naya Raipur"


def _is_admin(request: Request) -> bool:
    """True when the caller holds a valid session. Never raises."""
    try:
        load_session(request)
        return True
    except HTTPException:
        return False


@router.get("/public/config", response_model=PublicConfig)
def public_config(request: Request) -> PublicConfig:
    """Everything the coming-soon page needs, and nothing more."""
    state = read_state(request.app.state.ctx.settings)
    return PublicConfig(
        site=SITE_NAME,
        mode=state["site_mode"],
        scheduled_at=state["scheduled_at"],
        countdown_enabled=state["countdown_enabled"],
        ceremony_completed=state["inauguration_completed"],
        ceremony_enabled=state["inauguration_enabled"],
    )


@router.get("/ceremony/state", response_model=CeremonyState)
def ceremony_state(request: Request) -> CeremonyState:
    """What the ceremony page should display.

    ``can_cut`` is the server's answer to 'may this caller commit the inauguration'.
    The page uses it to label itself honestly; it is enforced again on the cut route,
    so a client that ignores it gains nothing.
    """
    ctx = request.app.state.ctx
    state = read_state(ctx.settings)

    is_admin = _is_admin(request)
    completed = state["inauguration_completed"]
    can_cut = bool(
        is_admin and not completed and state["site_mode"] in ("inauguration", "coming_soon")
    )

    return CeremonyState(
        mode=state["site_mode"],
        ceremony_enabled=state["inauguration_enabled"],
        completed=completed,
        completed_at=state["completed_at"],
        completed_by=state["completed_by"],
        scheduled_at=state["scheduled_at"],
        countdown_enabled=state["countdown_enabled"],
        preview=not is_admin,
        can_cut=can_cut,
        decommissioned=state["decommissioned"],
    )


@router.post("/ceremony/cut", response_model=CeremonyState)
def cut_ribbon(request: Request, body: CutRequest) -> CeremonyState:
    """Commit the inauguration. The only route that can complete the ceremony.

    Idempotent in the sense that matters: a second call records nothing and cannot
    rewrite the original director or timestamp. It is rejected with 409 rather than
    silently accepted, so a replay is visible in the audit log.
    """
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)
    ip = client_ip(request)

    state = read_state(ctx.settings)
    if state["inauguration_completed"]:
        audit.record(
            ctx.settings, "ceremony.cut.rejected",
            actor=session.username, ip=ip, detail={"reason": "already completed"},
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "the inauguration has already been held")

    if state["site_mode"] not in ("inauguration", "coming_soon"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot cut while the site is in {state['site_mode']} mode"
        )

    by = (body.by or "").strip() or session.username

    try:
        updated = complete_inauguration(ctx, by=by)
    except AlreadyCompleted as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ModeApplyFailed as exc:
        audit.record(
            ctx.settings, "ceremony.cut.failed",
            actor=session.username, ip=ip, detail={"message": exc.result.message},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"the site could not be moved to its permanent routing: {exc.result.message}",
        ) from exc

    audit.record(
        ctx.settings, "ceremony.cut",
        actor=session.username, ip=ip,
        detail={"by": by, "completed_at": updated["completed_at"]},
    )

    return CeremonyState(
        mode=updated["site_mode"],
        ceremony_enabled=updated["inauguration_enabled"],
        completed=updated["inauguration_completed"],
        completed_at=updated["completed_at"],
        completed_by=updated["completed_by"],
        scheduled_at=updated["scheduled_at"],
        countdown_enabled=updated["countdown_enabled"],
        preview=False,
        can_cut=False,
        decommissioned=updated["decommissioned"],
    )


__all__ = ["router", "SITE_NAME"]
