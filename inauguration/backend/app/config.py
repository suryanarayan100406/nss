"""Filesystem layout and settings.

Every path is a field with a production default, and the application is constructed
with ``create_app(settings)`` rather than reaching for module-level globals. That is
what lets the test suite point the entire system at a temporary directory — including
the Nginx include and the decommission script — without patching anything.

Production layout (see the README):

    /opt/nss-inauguration/          application + config   (never web-served)
    /var/www/nss/                   the public web root
    /var/backups/nss-inauguration/  snapshots, OUTSIDE the install dir
    /var/log/nss-inauguration/      audit + cleanup logs
    /etc/nginx/nss-mode.inc         the generated routing include
"""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _env_path(env: Mapping[str, str], key: str, default: str) -> Path:
    return Path(env.get(key, default)).expanduser()


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # a typo in the unit file should be loud, not silent
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Where everything lives, and how strictly we behave."""

    base_dir: Path
    web_root: Path
    backup_dir: Path
    log_dir: Path
    nginx_mode_inc: Path
    nginx_sites_available: Path
    decommission_script: Path

    service_name: str = "nss-inauguration"
    nginx_binary: str = "nginx"

    session_idle_minutes: int = 30
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    allowed_origins: tuple[str, ...] = ("https://nss.iiitnr.ac.in",)
    secure_cookies: bool = True

    # --- derived paths -----------------------------------------------------

    @property
    def config_dir(self) -> Path:
        return self.base_dir / "config"

    @property
    def site_json(self) -> Path:
        return self.config_dir / "site.json"

    @property
    def admin_json(self) -> Path:
        return self.config_dir / "admin.json"

    @property
    def secret_key_file(self) -> Path:
        return self.config_dir / "secret.key"

    @property
    def lock_file(self) -> Path:
        return self.config_dir / "site.lock"

    @property
    def audit_log(self) -> Path:
        return self.log_dir / "audit.log"

    @property
    def cleanup_log(self) -> Path:
        return self.log_dir / "cleanup.log"

    @property
    def session_max_age(self) -> int:
        return self.session_idle_minutes * 60

    # --- construction ------------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        origins = env.get("NSS_ALLOWED_ORIGINS", "https://nss.iiitnr.ac.in")
        return cls(
            base_dir=_env_path(env, "NSS_BASE_DIR", "/opt/nss-inauguration"),
            web_root=_env_path(env, "NSS_WEB_ROOT", "/var/www/nss"),
            backup_dir=_env_path(env, "NSS_BACKUP_DIR", "/var/backups/nss-inauguration"),
            log_dir=_env_path(env, "NSS_LOG_DIR", "/var/log/nss-inauguration"),
            nginx_mode_inc=_env_path(env, "NSS_NGINX_MODE_INC", "/etc/nginx/nss-mode.inc"),
            nginx_sites_available=_env_path(
                env, "NSS_NGINX_SITES_AVAILABLE", "/etc/nginx/sites-available/nss"
            ),
            decommission_script=_env_path(
                env, "NSS_DECOMMISSION", "/usr/local/sbin/nss-decommission"
            ),
            service_name=env.get("NSS_SERVICE_NAME", "nss-inauguration"),
            nginx_binary=env.get("NSS_NGINX_BINARY", "nginx"),
            session_idle_minutes=_env_int(env, "NSS_SESSION_IDLE_MINUTES", 30),
            login_max_attempts=_env_int(env, "NSS_LOGIN_MAX_ATTEMPTS", 5),
            login_lockout_minutes=_env_int(env, "NSS_LOGIN_LOCKOUT_MINUTES", 15),
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            secure_cookies=_env_bool(env, "NSS_SECURE_COOKIES", True),
        )

    def ensure_dirs(self) -> None:
        """Create the directories the service must be able to write.

        Deliberately does *not* create the web root, the Nginx directory or the backup
        directory's parents beyond the backup directory itself: those are owned by
        install.sh, and a service that silently creates them would hide a broken
        deployment instead of failing loudly.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # --- secrets -----------------------------------------------------------

    def load_secret(self) -> bytes:
        """Return the session signing key, generating it on first use.

        The key never leaves the server and is never logged. A missing key is not an
        error on first boot: it is created 0600 so a fresh install works, but a key
        that exists and cannot be read *is* an error, so we do not silently rotate it
        and invalidate every live session.
        """
        path = self.secret_key_file
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = secrets.token_bytes(48)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return data
        if not data:
            raise RuntimeError(f"secret key at {path} is empty")
        return data

    def load_or_create_secret(self) -> bytes:
        return self.load_secret()


def _chmod_private(path: Path, mode: int) -> None:
    """Best-effort POSIX permissions; a no-op on Windows, where the tests run."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


__all__ = ["Settings", "_chmod_private", "stat"]
