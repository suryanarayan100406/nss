"""The site state machine.

One JSON document, read and modified under an exclusive lock and replaced atomically,
so a crash mid-write can never leave a truncated file that the next boot reads as
truth. ``site_mode`` is *derived and validated* here from the flags — the client never
supplies it, and a hand-edited file that contradicts itself is rejected rather than
obeyed.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import Settings

MODES = ("coming_soon", "inauguration", "permanent")

#: The inauguration this system was built for. Kept as the default so a fresh install
#: is correct without configuration; the admin portal can move it.
DEFAULT_SCHEDULED_AT = "2026-10-02T10:15:00+05:30"


class StateError(Exception):
    """Base class for anything wrong with the state document."""


class InvalidState(StateError):
    """The stored document is not self-consistent."""


class InvalidTransition(StateError):
    """The requested move is not allowed from the current mode."""


def default_state(scheduled_at: str = DEFAULT_SCHEDULED_AT) -> dict[str, Any]:
    return {
        "site_mode": "coming_soon",
        "countdown_enabled": True,
        "inauguration_enabled": False,
        "inauguration_completed": False,
        "scheduled_at": scheduled_at,
        "completed_at": None,
        "completed_by": None,
        "decommissioned": False,
    }


# --------------------------------------------------------------------------- lock


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """An exclusive lock that works on both POSIX and Windows.

    flock is unavailable on Windows, so the Windows branch uses msvcrt's byte-range
    locking. The single lock byte is written first because locking a zero-length file
    is an error on some Windows versions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fh.fileno()).st_size == 0:
                fh.write(b"\0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# --------------------------------------------------------------------------- io


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-site-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_scheduled_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InvalidState("scheduled_at must be a non-empty string")
    try:
        # A stray BOM is what Notepad adds, and it would otherwise be read as a
        # corrupt timestamp rather than a stray byte.
        parsed = datetime.fromisoformat(value.strip().lstrip("﻿"))
    except ValueError as exc:
        raise InvalidState(f"scheduled_at is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # Without an offset the countdown would depend on the visitor's clock, which
        # is exactly the bug the coming-soon page must not have.
        raise InvalidState(
            "scheduled_at must carry a UTC offset (e.g. 2026-10-02T10:15:00+05:30)"
        )
    return parsed


def validate(state: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised copy, or raise InvalidState.

    The invariants are the point: several of these flags can express contradictory
    things, and a document that does so is a bug somewhere upstream, not a state to
    be served to the public.
    """
    if not isinstance(state, dict):
        raise InvalidState("site state must be a JSON object")

    out = default_state()
    out.update(state)

    for key in ("countdown_enabled", "inauguration_enabled",
                "inauguration_completed", "decommissioned"):
        if not isinstance(out[key], bool):
            raise InvalidState(f"{key} must be a boolean, got {type(out[key]).__name__}")

    if out["site_mode"] not in MODES:
        raise InvalidState(f"site_mode must be one of {MODES}, got {out['site_mode']!r}")

    _parse_scheduled_at(out["scheduled_at"])

    for key in ("completed_at", "completed_by"):
        if out[key] is not None and not isinstance(out[key], str):
            raise InvalidState(f"{key} must be a string or null")

    if out["inauguration_completed"]:
        if out["site_mode"] != "permanent":
            raise InvalidState("a completed inauguration implies site_mode 'permanent'")
        if out["inauguration_enabled"]:
            raise InvalidState("a completed inauguration cannot still be enabled")
        if not out["completed_at"] or not out["completed_by"]:
            raise InvalidState("a completed inauguration must record when and by whom")

    if out["site_mode"] == "permanent" and not (
        out["inauguration_completed"] or out["decommissioned"]
    ):
        raise InvalidState(
            "site_mode 'permanent' requires either a completed inauguration or decommissioning"
        )

    if out["site_mode"] == "inauguration" and not out["inauguration_enabled"]:
        raise InvalidState("site_mode 'inauguration' requires inauguration_enabled")

    if out["site_mode"] == "coming_soon" and out["inauguration_enabled"]:
        raise InvalidState("site_mode 'coming_soon' cannot have the ceremony enabled")

    if out["decommissioned"] and out["inauguration_enabled"]:
        raise InvalidState("a decommissioned system cannot have the ceremony enabled")

    return out


def read_state(settings: Settings) -> dict[str, Any]:
    """Read the document, creating the default on first run."""
    path = settings.site_json
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        state = validate(default_state())
        _atomic_write_json(path, state)
        return state
    try:
        parsed = json.loads(raw.lstrip("﻿"))
    except json.JSONDecodeError as exc:
        raise InvalidState(f"{path} is not valid JSON: {exc}") from exc
    return validate(parsed)


def write_state(settings: Settings, state: dict[str, Any]) -> dict[str, Any]:
    clean = validate(state)
    _atomic_write_json(settings.site_json, clean)
    return clean


def update_state(
    settings: Settings, mutate: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    """Read-modify-write under the lock, so two admins cannot interleave.

    The mutator receives a copy and returns the new document; it is validated before
    anything is written, so a bad mutation leaves the file untouched.
    """
    with file_lock(settings.lock_file):
        current = read_state(settings)
        updated = mutate(dict(current))
        if updated is None:
            raise InvalidState("state mutator returned None")
        clean = validate(updated)
        _atomic_write_json(settings.site_json, clean)
        return clean


# -------------------------------------------------------------------- transitions

#: Which modes may follow which. Before the ribbon is cut the mode is reversible, so
#: an admin who enables the ceremony early can pull it back. 'permanent' is entered
#: only by completing the inauguration or by decommissioning, and is terminal — the
#: site never leaves the permanent homepage once it is live.
_ALLOWED_NEXT = {
    "coming_soon": {"coming_soon", "inauguration", "permanent"},
    "inauguration": {"coming_soon", "inauguration", "permanent"},
    "permanent": {"permanent"},
}


def transition_mode(state: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise InvalidTransition(f"unknown mode {mode!r}")
    current = state.get("site_mode", "coming_soon")
    if mode not in _ALLOWED_NEXT.get(current, set()):
        raise InvalidTransition(f"cannot move from {current!r} to {mode!r}")
    if state.get("decommissioned") and mode != "permanent":
        raise InvalidTransition("a decommissioned system is permanently in 'permanent' mode")

    new = dict(state)
    new["site_mode"] = mode
    if mode == "coming_soon":
        new["inauguration_enabled"] = False
    elif mode == "inauguration":
        new["inauguration_enabled"] = True
    return validate(new)


def complete_ceremony(state: dict[str, Any], by: str, at: str) -> dict[str, Any]:
    """The one irreversible, idempotent transition: the ribbon has been cut.

    Called by the admin cut endpoint. Re-completing an already-complete state is not
    an error here — the route decides that — but it must not change the recorded
    timestamp or director, so the original record is preserved.
    """
    if state.get("inauguration_completed"):
        return dict(state)

    new = dict(state)
    new["inauguration_completed"] = True
    new["inauguration_enabled"] = False
    new["site_mode"] = "permanent"
    new["completed_at"] = at
    new["completed_by"] = by
    return validate(new)


def mark_decommissioned(state: dict[str, Any]) -> dict[str, Any]:
    new = dict(state)
    new["decommissioned"] = True
    new["inauguration_enabled"] = False
    new["site_mode"] = "permanent"
    return validate(new)


__all__ = [
    "MODES",
    "DEFAULT_SCHEDULED_AT",
    "StateError",
    "InvalidState",
    "InvalidTransition",
    "default_state",
    "validate",
    "read_state",
    "write_state",
    "update_state",
    "file_lock",
    "transition_mode",
    "complete_ceremony",
    "mark_decommissioned",
]
