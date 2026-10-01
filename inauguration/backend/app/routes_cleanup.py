"""The final, irreversible cleanup.

This route is the thin authorised wrapper around the root-owned decommission script.
It re-checks everything the script will also check, because defence here is cheap and
a mistake is not: the site must be completed, the phrase must be exact, and the
password must be re-entered — a live session alone is not enough to destroy the
inauguration system.

The script is invoked by absolute path with a fixed argument vector and takes no
arguments of its own, so nothing a request contains can steer what gets deleted.
"""

from __future__ import annotations

import subprocess

from fastapi import APIRouter, HTTPException, Request, status

from . import audit
from .auth import AdminStore
from .models import ActionResponse, CleanupRequest
from .security import AdminSession, client_ip, require_admin_with_csrf
from .state import read_state
from .routes_admin import CLEANUP_PHRASE

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])

CLEANUP_TIMEOUT_SECONDS = 300


def invoke_decommission(settings) -> tuple[bool, str]:
    """Run the root-owned script. No arguments, no shell, no interpolation.

    ``sudo -n`` is deliberate: if the sudoers rule is missing or wrong, this fails
    immediately with a clear message instead of hanging on a password prompt that
    nobody is there to answer.
    """
    cmd = ["sudo", "-n", str(settings.decommission_script)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            timeout=CLEANUP_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "sudo is not available on this host"
    except subprocess.TimeoutExpired:
        return False, f"the cleanup did not finish within {CLEANUP_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return False, str(exc)

    output = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return proc.returncode == 0, output


@router.post("/run", response_model=ActionResponse)
def run_cleanup(request: Request, body: CleanupRequest) -> ActionResponse:
    ctx = request.app.state.ctx
    session: AdminSession = require_admin_with_csrf(request)
    ip = client_ip(request)

    state = read_state(ctx.settings)

    if not state["inauguration_completed"]:
        audit.record(ctx.settings, "cleanup.refused", actor=session.username, ip=ip,
                     detail={"reason": "inauguration not completed"})
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the inauguration has not been completed yet; the cleanup stays locked until it is",
        )

    if state["decommissioned"]:
        return ActionResponse(ok=True, message="the inauguration system was already removed")

    if body.phrase != CLEANUP_PHRASE:
        audit.record(ctx.settings, "cleanup.refused", actor=session.username, ip=ip,
                     detail={"reason": "wrong confirmation phrase"})
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "the confirmation phrase does not match exactly",
        )

    store: AdminStore = ctx.admin_store
    if store.authenticate(session.username, body.password) is None:
        audit.record(ctx.settings, "cleanup.refused", actor=session.username, ip=ip,
                     detail={"reason": "password re-entry failed"})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "password re-entry failed; the cleanup was not started",
        )

    audit.record(ctx.settings, "cleanup.started", actor=session.username, ip=ip)

    runner = getattr(ctx, "decommission_runner", invoke_decommission)
    ok, output = runner(ctx.settings)

    if not ok:
        audit.record(ctx.settings, "cleanup.failed", actor=session.username, ip=ip,
                     detail={"output": output[-2000:]})
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"the cleanup did not complete; nothing was left half-removed. {output[-1000:]}",
        )

    # The script has already verified the permanent site over HTTP by this point. The
    # state document may now be gone along with the installation, which is expected —
    # recording the flag is a courtesy for the case where it still exists.
    try:
        from .state import mark_decommissioned, update_state

        update_state(ctx.settings, mark_decommissioned)
    except Exception:  # noqa: BLE001 - the installation may legitimately be gone
        pass

    audit.record(ctx.settings, "cleanup.completed", actor=session.username, ip=ip)
    return ActionResponse(ok=True, message="the inauguration system was removed", state=None)


__all__ = ["router", "invoke_decommission", "CLEANUP_TIMEOUT_SECONDS"]
