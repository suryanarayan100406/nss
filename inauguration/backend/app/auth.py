"""Admin credentials, sessions and login throttling.

There is no registration route anywhere in this application by design. The single
admin account is created once, server-side, by ``deploy/init-admin.py``.

Sessions are signed cookies rather than server-side records, so a restart does not
log the director out mid-ceremony. Revocation is handled by an epoch counter stored
beside the password: bump it and every issued cookie stops verifying.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

#: argon2-cffi's defaults are already tuned for a server; this keeps them explicit.
_hasher = PasswordHasher()

SESSION_COOKIE = "nss_admin_session"
CSRF_COOKIE = "nss_admin_csrf"
CSRF_HEADER = "X-CSRF-Token"


class AuthError(Exception):
    """Base class for authentication problems."""


class NotAuthenticated(AuthError):
    """No usable session."""


class LockedOut(AuthError):
    """Too many failed attempts from this address."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many failed login attempts")
        self.retry_after = retry_after


# --------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ admin record


@dataclass(frozen=True)
class AdminRecord:
    username: str
    password_hash: str
    created_at: str
    session_epoch: int


class AdminStore:
    """Reads and writes ``config/admin.json``."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path: Path = settings.admin_json

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> AdminRecord | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw.lstrip("﻿"))
        except json.JSONDecodeError as exc:
            raise AuthError(f"{self.path} is not valid JSON: {exc}") from exc

        missing = [k for k in ("username", "password_hash") if not data.get(k)]
        if missing:
            raise AuthError(f"{self.path} is missing {', '.join(missing)}")
        return AdminRecord(
            username=data["username"],
            password_hash=data["password_hash"],
            created_at=data.get("created_at", ""),
            session_epoch=int(data.get("session_epoch", 1)),
        )

    def save(self, record: AdminRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "username": record.username,
            "password_hash": record.password_hash,
            "created_at": record.created_at,
            "session_epoch": record.session_epoch,
        }
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(tmp, 0o640)
        os.replace(tmp, self.path)

    def create(self, username: str, password: str) -> AdminRecord:
        record = AdminRecord(
            username=username,
            password_hash=hash_password(password),
            created_at=_utc_now(),
            session_epoch=1,
        )
        self.save(record)
        return record

    def authenticate(self, username: str, password: str) -> AdminRecord | None:
        record = self.load()
        if record is None:
            return None
        # Verify against a dummy hash when the username is wrong, so the response
        # time does not reveal whether the account exists.
        if not secrets.compare_digest(username, record.username):
            verify_password(password, record.password_hash)
            return None
        return record if verify_password(password, record.password_hash) else None

    def bump_epoch(self) -> int:
        """Invalidate every existing session. Used on password change."""
        record = self.load()
        if record is None:
            raise AuthError("no admin account exists")
        new = AdminRecord(
            username=record.username,
            password_hash=record.password_hash,
            created_at=record.created_at,
            session_epoch=record.session_epoch + 1,
        )
        self.save(new)
        return new.session_epoch


# ---------------------------------------------------------------------- sessions


class SessionManager:
    """Issues and verifies signed session cookies."""

    def __init__(self, settings: Settings, secret: bytes) -> None:
        self.settings = settings
        self._serializer = URLSafeTimedSerializer(
            secret_key=secret, salt="nss-admin-session", serializer=json
        )

    def issue(self, username: str, epoch: int, csrf: str) -> str:
        return self._serializer.dumps(
            {"sub": username, "epoch": epoch, "csrf": csrf, "iat": int(time.time())}
        )

    def load(self, token: str, *, current_epoch: int) -> dict[str, Any]:
        """Verify a token or raise NotAuthenticated.

        max_age gives expiry measured from signing, not from the last request; the
        route refreshes the cookie on every authenticated call, which is what makes
        the effective policy 'idle' rather than 'absolute'.
        """
        try:
            payload = self._serializer.loads(token, max_age=self.settings.session_max_age)
        except SignatureExpired as exc:
            raise NotAuthenticated("session expired") from exc
        except BadSignature as exc:
            raise NotAuthenticated("invalid session") from exc

        if not isinstance(payload, dict) or "sub" not in payload or "csrf" not in payload:
            raise NotAuthenticated("malformed session")
        if int(payload.get("epoch", 0)) != int(current_epoch):
            raise NotAuthenticated("session revoked")
        return payload


# ------------------------------------------------------------------- throttling


class LoginRateLimiter:
    """Per-address failed-attempt tracking.

    In-process and therefore per-worker; the service runs a single worker, and the
    lockout is a speed bump in front of argon2 rather than the only defence, so this
    is an acceptable trade for having no shared store to keep alive.
    """

    def __init__(self, max_attempts: int, lockout_seconds: int, clock=time.monotonic) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> None:
        window = self._failures.get(ip)
        if window:
            cutoff = now - self.lockout_seconds
            self._failures[ip] = [t for t in window if t > cutoff]

    def check(self, ip: str) -> None:
        """Raise LockedOut if this address is currently locked out."""
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(ip)
            if until is not None:
                if now < until:
                    raise LockedOut(retry_after=int(until - now) + 1)
                del self._locked_until[ip]
            self._prune(ip, now)

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._prune(ip, now)
            window = self._failures.setdefault(ip, [])
            window.append(now)
            if len(window) >= self.max_attempts:
                self._locked_until[ip] = now + self.lockout_seconds

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)

    def locked_out(self, ip: str) -> int:
        """Seconds remaining, or 0. For the dashboard and tests."""
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(ip, 0.0)
            return max(0, int(until - now) + 1) if until > now else 0


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


__all__ = [
    "SESSION_COOKIE",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "AuthError",
    "NotAuthenticated",
    "LockedOut",
    "AdminRecord",
    "AdminStore",
    "SessionManager",
    "LoginRateLimiter",
    "hash_password",
    "verify_password",
    "new_csrf_token",
]
