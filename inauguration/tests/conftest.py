"""Shared fixtures.

Every test runs against a throwaway tree: the state document, the Nginx include and
the decommission script are all paths inside ``tmp_path``, so nothing here can reach
the production layout even by accident, and the failure paths can be driven without a
VM or root.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN_USER = "director"
ADMIN_PASSWORD = "correct-horse-battery-staple"


class FakeNginx:
    """Stands in for the nginx binary so the failure paths can be exercised."""

    def __init__(self, *, test_ok: bool = True, reload_ok: bool = True) -> None:
        self.test_ok = test_ok
        self.reload_ok = reload_ok
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        is_test = "-t" in cmd
        rc = 0 if (self.test_ok if is_test else self.reload_ok) else 1
        return subprocess.CompletedProcess(
            cmd, rc, stdout="", stderr="" if rc == 0 else "nginx: configuration test failed"
        )

    @property
    def tests(self) -> int:
        return sum(1 for c in self.calls if "-t" in c)

    @property
    def reloads(self) -> int:
        return sum(1 for c in self.calls if "-s" in c)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        base_dir=tmp_path / "opt" / "nss-inauguration",
        web_root=tmp_path / "var" / "www" / "nss",
        backup_dir=tmp_path / "var" / "backups" / "nss-inauguration",
        log_dir=tmp_path / "var" / "log" / "nss-inauguration",
        nginx_mode_inc=tmp_path / "etc" / "nginx" / "nss-mode.inc",
        nginx_sites_available=tmp_path / "etc" / "nginx" / "sites-available" / "nss",
        decommission_script=tmp_path / "usr" / "local" / "sbin" / "nss-decommission",
        allowed_origins=("https://nss.iiitnr.ac.in",),
        secure_cookies=False,
        login_max_attempts=3,
        login_lockout_minutes=15,
    )


@pytest.fixture
def nginx() -> FakeNginx:
    return FakeNginx()


@pytest.fixture
def app(settings: Settings, nginx: FakeNginx):
    application = create_app(settings, nginx_run=nginx, secret=b"test-secret-key-0123456789")
    application.state.ctx.admin_store.create(ADMIN_USER, ADMIN_PASSWORD)
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def anon_client(app):
    """A second, cookie-free client against the same app.

    ``logged_in`` wraps ``client``, so within one test ``client`` *is* the signed-in
    client. Anything asserting what an anonymous visitor sees must use this instead,
    or it will silently be testing the admin's view.
    """
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def logged_in(client):
    """A client holding a valid admin session, plus the CSRF token to send with it."""
    response = client.post(
        "/api/admin/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json()["state"]["csrf"]
    client.headers.update({"X-CSRF-Token": token, "Origin": "https://nss.iiitnr.ac.in"})
    return client


@pytest.fixture
def web_root(settings: Settings) -> Path:
    """A miniature standing of the public site, so 'permanent assets survive' is testable."""
    root = settings.web_root
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<html>permanent home</html>", encoding="utf-8")
    (root / "team.html").write_text("<html>permanent team</html>", encoding="utf-8")
    (root / "nss_logo.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    for sub in ("css", "js", "vendor", "assets"):
        (root / sub).mkdir(exist_ok=True)
        (root / sub / "placeholder.txt").write_text("permanent", encoding="utf-8")
    (root / "css" / "style.css").write_text(":root{}", encoding="utf-8")
    (root / "js" / "main.js").write_text("// main", encoding="utf-8")
    (root / "inauguration.html").write_text(
        "<html><title>The Unveiling</title>ceremony</html>", encoding="utf-8"
    )
    (root / "coming-soon.html").write_text("<html>coming soon</html>", encoding="utf-8")
    (root / "admin").mkdir(exist_ok=True)
    (root / "admin" / "index.html").write_text("<html>portal</html>", encoding="utf-8")
    return root


__all__ = [
    "ADMIN_USER",
    "ADMIN_PASSWORD",
    "FakeNginx",
    "BACKEND",
]
