"""Authorization, CSRF and origin enforcement.

These are the tests that matter most for the ceremony: they are the difference between
'only the admin can inaugurate the site' and 'anyone who opens the page can'.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.security import content_security_policy
from conftest import ADMIN_PASSWORD, ADMIN_USER

#: Every route that changes state, plus the authenticated read. None may answer an
#: anonymous caller. Kept as a literal list so adding a route without guarding it is
#: a visible omission here rather than a silent one.
STATE_CHANGING = [
    ("POST", "/api/ceremony/cut", {"by": "Intruder"}),
    ("POST", "/api/admin/logout", None),
    ("POST", "/api/admin/logout-all", None),
    ("POST", "/api/admin/schedule", {"scheduled_at": "2026-10-02T10:15:00+05:30"}),
    ("POST", "/api/admin/ceremony/enable", None),
    ("POST", "/api/admin/ceremony/disable", None),
    ("POST", "/api/cleanup/run", {"phrase": "DELETE INAUGURATION SYSTEM", "password": "x"}),
]

AUTHENTICATED_READS = [
    ("GET", "/api/admin/state", None),
    ("GET", "/api/admin/verify", None),
    ("GET", "/api/admin/preview", None),
]


@pytest.mark.parametrize("method,path,body", STATE_CHANGING)
def test_anonymous_callers_are_refused(client, method, path, body):
    response = client.request(
        method, path, json=body, headers={"Origin": "https://nss.iiitnr.ac.in"}
    )
    assert response.status_code in (401, 403), f"{path} answered {response.status_code}"


@pytest.mark.parametrize("method,path,body", AUTHENTICATED_READS)
def test_anonymous_callers_cannot_read_admin_state(client, method, path, body):
    response = client.request(method, path)
    assert response.status_code in (401, 403)


def test_the_cut_route_is_never_reachable_without_a_session(client):
    """The single most important assertion in the suite."""
    response = client.post(
        "/api/ceremony/cut",
        json={"by": "Intruder"},
        headers={"Origin": "https://nss.iiitnr.ac.in", "X-CSRF-Token": "anything"},
    )
    assert response.status_code == 401


def test_a_session_alone_cannot_change_state_without_csrf(logged_in):
    """Dropping the header must fail even though the cookie is valid."""
    response = logged_in.post("/api/admin/ceremony/enable", headers={"X-CSRF-Token": ""})
    assert response.status_code == 403
    assert "CSRF" in response.json()["error"]


def test_a_wrong_csrf_token_is_refused(logged_in):
    response = logged_in.post(
        "/api/admin/ceremony/enable", headers={"X-CSRF-Token": "not-the-token"}
    )
    assert response.status_code == 403


def test_a_cross_site_origin_is_refused(logged_in):
    response = logged_in.post(
        "/api/admin/ceremony/enable", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert "origin" in response.json()["error"].lower()


def test_a_cross_site_referer_is_refused(logged_in):
    headers = {"Origin": "", "Referer": "https://evil.example/attack"}
    response = logged_in.post("/api/admin/ceremony/enable", headers=headers)
    assert response.status_code == 403


def test_a_valid_session_passes(logged_in):
    response = logged_in.get("/api/admin/state")
    assert response.status_code == 200
    assert response.json()["state"]["admin"] == ADMIN_USER


# --- login ------------------------------------------------------------------


def test_a_wrong_password_is_refused(client):
    response = client.post(
        "/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"}
    )
    assert response.status_code == 401


def test_a_wrong_username_is_refused(client):
    response = client.post(
        "/api/admin/login", json={"username": "someone", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 401


def test_repeated_failures_lock_the_address_out(client):
    for _ in range(3):
        client.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})

    response = client.post(
        "/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_a_successful_login_clears_the_failure_count(client):
    client.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})
    client.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})
    ok = client.post(
        "/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert ok.status_code == 200

    # The counter reset, so two more failures must not lock the address out.
    client.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})
    client.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong"})
    still_ok = client.post(
        "/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert still_ok.status_code == 200


def test_logout_ends_the_session(logged_in):
    assert logged_in.post("/api/admin/logout").status_code == 200
    assert logged_in.get("/api/admin/state").status_code == 401


def test_logout_all_revokes_a_stolen_cookie(app, logged_in):
    """Advancing the epoch must invalidate cookies that are already in the wild."""
    stolen = logged_in.cookies.get("nss_admin_session")
    assert stolen

    assert logged_in.post("/api/admin/logout-all").status_code == 200

    # Replay the old cookie on a client that never logs in again.
    from fastapi.testclient import TestClient

    with TestClient(app) as replay:
        replay.cookies.set("nss_admin_session", stolen)
        assert replay.get("/api/admin/state").status_code == 401


def test_the_admin_page_is_never_publicly_registrable(client):
    """There is no route that creates an account."""
    for path in ("/api/admin/register", "/api/register", "/api/admin/signup", "/api/signup"):
        assert client.post(path, json={"username": "x", "password": "y"}).status_code == 404


def test_the_csrf_cookie_is_readable_but_the_session_cookie_is_not(client):
    response = client.post(
        "/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    cookies = response.headers.get_list("set-cookie")
    session = [c for c in cookies if c.startswith("nss_admin_session")]
    csrf = [c for c in cookies if c.startswith("nss_admin_csrf")]
    assert session and "HttpOnly" in session[0]
    assert csrf and "HttpOnly" not in csrf[0]


def test_security_headers_are_present(client):
    headers = client.get("/api/public/config").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in headers


# --- the policy is chosen per surface ------------------------------------------------
#
# The public pages and the portal need opposite things from `script-src`, and getting it
# wrong is silent: a blocked inline script raises no error a visitor can see, the page
# just never starts. These assert the split directly, because nothing else would.


def _directives(policy):
    out = {}
    for part in policy.split(";"):
        bits = part.split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def _script_src(policy):
    return _directives(policy).get("script-src", [])


def test_the_public_site_may_run_its_own_inline_scripts():
    assert "'unsafe-inline'" in _script_src(content_security_policy("/coming-soon.html"))
    assert "'unsafe-inline'" in _script_src(content_security_policy("/index.html"))
    assert "'unsafe-inline'" in _script_src(content_security_policy("/inauguration.html"))


def test_the_portal_may_not():
    assert "'unsafe-inline'" not in _script_src(content_security_policy("/admin/"))
    assert _script_src(content_security_policy("/admin/")) == ["'self'"]
    assert _script_src(content_security_policy("/api/admin/state")) == ["'self'"]


def test_the_site_policy_still_forbids_off_site_script():
    script = _script_src(content_security_policy("/"))
    assert "'self'" in script
    assert not any(s.startswith("http") for s in script)


def test_the_homepage_may_frame_instagram_but_nothing_else_off_site():
    # index.html opens its post preview in a cross-origin iframe; under a bare
    # `default-src 'self'` that embed is blocked and the feature quietly dies.
    frame = _directives(content_security_policy("/index.html"))["frame-src"]
    assert "https://www.instagram.com" in frame
    assert frame[0] == "'self'"


def test_the_portal_frames_nothing():
    assert _directives(content_security_policy("/admin/"))["frame-src"] == ["'none'"]


def test_the_public_pages_the_rehearsal_serves_carry_the_site_policy(client):
    # The rehearsal serves the real pages through this same middleware, so this is the
    # header the browser actually gets — and the header the Nginx config must match.
    policy = client.get("/coming-soon.html").headers.get("Content-Security-Policy", "")
    if not policy:
        pytest.skip("no static mount in this app instance")
    assert "'unsafe-inline'" in _script_src(policy)


def test_the_strict_policy_would_break_the_real_pages():
    """A guard on the guard: if the site pages ever stop needing inline script, this
    test should be deleted deliberately rather than the policy loosened by accident
    staying in place unnoticed."""
    root = Path(__file__).resolve().parents[2]
    inline = 0
    for path in list(root.glob("*.html")) + list((root / "inauguration" / "web").rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        inline += len(re.findall(r"<script(?![^>]*\bsrc=)", text))
        inline += len(re.findall(r"\son(?:click|load|change|submit|input)\s*=", text))
    assert inline > 0, "no inline script left anywhere — the site CSP can now be strict"
