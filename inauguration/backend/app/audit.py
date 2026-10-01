"""The append-only audit trail.

Every state-changing action is recorded as one JSON object per line, with who, what,
when, from where. The file lives in ``/var/log/nss-inauguration/`` — outside the
installation directory and on no cleanup allowlist — so it survives decommissioning
and is still on disk for the institute afterwards.

Writing must never take the site down: a logging failure is reported to stderr and
swallowed rather than turned into a 500 on an otherwise valid request.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    settings: Settings,
    event: str,
    *,
    actor: str | None = None,
    ip: str | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    """Append one event. Returns the record that was written."""
    entry: dict[str, Any] = {"at": _utc_now(), "event": event}
    if actor is not None:
        entry["actor"] = actor
    if ip is not None:
        entry["ip"] = ip
    if detail is not None:
        entry["detail"] = detail

    try:
        path: Path = settings.audit_log
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, sort_keys=True) + "\n"
        # O_APPEND so concurrent writers cannot interleave a partial line.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        print(f"[audit] could not write {event!r}: {exc}", file=sys.stderr)

    return entry


def read_recent(settings: Settings, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent entries, newest first — for the dashboard activity list.

    Reads the tail rather than the whole file: the log is append-only and unbounded,
    so parsing all of it on every dashboard load would get slower every day.
    """
    path: Path = settings.audit_log
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, 64 * 1024)
            fh.seek(size - block)
            data = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []

    lines = [ln for ln in data.splitlines() if ln.strip()]
    # The first line may be a partial record when the read window split it.
    if block < size and lines:
        lines = lines[1:]

    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


__all__ = ["record", "read_recent"]
