"""Mode changes that have to keep routing and recorded state in agreement.

Changing the mode touches two things that can fail independently — the Nginx include
and ``site.json`` — and the site is only correct when they agree. Ordering is chosen
so the recoverable failure comes first: apply and validate routing, then record it. If
the record cannot be written, routing is put back, so what is served never drifts from
what the dashboard reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .nginx_mode import NginxResult
from .state import (
    complete_ceremony,
    read_state,
    transition_mode,
    update_state,
)


class ModeApplyFailed(Exception):
    """Routing could not be changed; nothing else was touched."""

    def __init__(self, result: NginxResult) -> None:
        super().__init__(result.message)
        self.result = result


class AlreadyCompleted(Exception):
    """The inauguration has already been held."""


class Ctx(Protocol):
    settings: Any
    nginx: Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_site_mode(ctx: Ctx, mode: str) -> NginxResult:
    """Move the site to ``mode``, or leave everything as it was."""
    current = read_state(ctx.settings)
    if current["site_mode"] == mode:
        return NginxResult(True, False, f"already in {mode} mode")

    result = ctx.nginx.apply(mode)
    if not result.ok:
        raise ModeApplyFailed(result)

    try:
        update_state(ctx.settings, lambda s: transition_mode(s, mode))
    except Exception:
        # Routing moved but the record did not. Put routing back rather than serve a
        # mode the state document disagrees with.
        ctx.nginx.apply(current["site_mode"])
        raise

    return result


def complete_inauguration(ctx: Ctx, by: str, at: str | None = None) -> dict[str, Any]:
    """Commit the cut: record it and serve the permanent site.

    Raises AlreadyCompleted rather than re-recording, so a replay cannot overwrite the
    original director or timestamp — the record of who inaugurated the site is written
    exactly once.
    """
    current = read_state(ctx.settings)
    if current["inauguration_completed"]:
        raise AlreadyCompleted("the inauguration has already been held")

    result = ctx.nginx.apply("permanent")
    if not result.ok:
        raise ModeApplyFailed(result)

    stamp = at or _now()
    try:
        return update_state(
            ctx.settings, lambda s: complete_ceremony(s, by=by, at=stamp)
        )
    except Exception:
        ctx.nginx.apply(current["site_mode"])
        raise


__all__ = [
    "set_site_mode",
    "complete_inauguration",
    "ModeApplyFailed",
    "AlreadyCompleted",
]
