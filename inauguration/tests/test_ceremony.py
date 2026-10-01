"""The cut: who may perform it, that it happens once, and what it leaves behind."""

from __future__ import annotations

import pytest

from app.nginx_mode import render_include
from app.state import read_state
from conftest import FakeNginx


def _state(app):
    return read_state(app.state.ctx.settings)


# --- authorization ----------------------------------------------------------


def test_an_anonymous_visitor_cannot_commit_the_cut(client):
    response = client.post("/api/ceremony/cut", json={"by": "Intruder"})
    assert response.status_code in (401, 403)


def test_a_failed_cut_leaves_the_state_untouched(app, client):
    client.post("/api/ceremony/cut", json={"by": "Intruder"})
    assert _state(app)["inauguration_completed"] is False


def test_the_state_route_reports_can_cut_honestly(app, logged_in, anon_client):
    anonymous = anon_client.get("/api/ceremony/state").json()
    assert anonymous["can_cut"] is False
    assert anonymous["preview"] is True

    admin = logged_in.get("/api/ceremony/state").json()
    assert admin["can_cut"] is True
    assert admin["preview"] is False


def test_the_preview_is_admin_only(app, anon_client, logged_in):
    assert anon_client.get("/api/admin/preview").status_code in (401, 403)

    app.state.ctx.settings.web_root.mkdir(parents=True, exist_ok=True)
    (app.state.ctx.settings.web_root / "inauguration.html").write_text(
        "<html>The Unveiling</html>", encoding="utf-8"
    )
    response = logged_in.get("/api/admin/preview")
    assert response.status_code == 200
    assert "The Unveiling" in response.text


def test_the_preview_is_byte_for_byte_the_published_page(app, logged_in):
    """The admin approves what they were shown. The public copy is served by Nginx
    straight off disk, so if the preview normalises line endings they are not the same
    file — and the difference would never show up in a rendered check."""
    page = b"<html>\r\n<body>\r\n<p>The Unveiling</p>\r\n</body>\r\n</html>"
    app.state.ctx.settings.web_root.mkdir(parents=True, exist_ok=True)
    (app.state.ctx.settings.web_root / "inauguration.html").write_bytes(page)

    response = logged_in.get("/api/admin/preview")
    assert response.status_code == 200
    assert response.content == page, "the preview rewrote the page it served"


# --- committing -------------------------------------------------------------


def test_the_admin_can_cut_the_ribbon(app, logged_in, web_root, nginx):
    response = logged_in.post("/api/ceremony/cut", json={"by": "Prof. Director"})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["completed"] is True
    assert body["completed_by"] == "Prof. Director"
    assert body["mode"] == "permanent"
    assert body["can_cut"] is False

    stored = _state(app)
    assert stored["inauguration_completed"] is True
    assert stored["site_mode"] == "permanent"

    # Routing moved with it, and the include is the permanent one.
    assert settings_include(app) == render_include("permanent")
    assert nginx.reloads >= 1


def settings_include(app) -> str:
    return app.state.ctx.settings.nginx_mode_inc.read_text(encoding="utf-8")


def test_the_server_records_its_own_timestamp(app, logged_in, web_root):
    """The projector laptop's clock is not the record of truth."""
    logged_in.post("/api/ceremony/cut", json={"by": "Director", "at": "1999-01-01T00:00:00Z"})
    assert not _state(app)["completed_at"].startswith("1999")


def test_the_director_name_falls_back_to_the_account(app, logged_in, web_root):
    logged_in.post("/api/ceremony/cut", json={})
    assert _state(app)["completed_by"] == "director"


def test_cutting_twice_is_refused_and_changes_nothing(app, logged_in, web_root):
    first = logged_in.post("/api/ceremony/cut", json={"by": "First Director"})
    assert first.status_code == 200
    original = _state(app)

    second = logged_in.post("/api/ceremony/cut", json={"by": "Second Director"})
    assert second.status_code == 409

    after = _state(app)
    assert after["completed_by"] == "First Director"
    assert after["completed_at"] == original["completed_at"]


def test_a_replay_does_not_reload_nginx_again(app, logged_in, web_root, nginx):
    logged_in.post("/api/ceremony/cut", json={"by": "Director"})
    reloads = nginx.reloads
    logged_in.post("/api/ceremony/cut", json={"by": "Director"})
    assert nginx.reloads == reloads


def test_a_failed_routing_change_does_not_record_a_completion(app, settings, web_root):
    """If the site cannot be moved to permanent routing, nothing is marked done."""
    failing = FakeNginx(test_ok=False)
    from fastapi.testclient import TestClient

    from app.main import create_app

    app2 = create_app(settings, nginx_run=failing, secret=b"test-secret-key-0123456789")
    app2.state.ctx.admin_store.create("director", "correct-horse-battery-staple")

    with TestClient(app2) as c:
        login = c.post(
            "/api/admin/login",
            json={"username": "director", "password": "correct-horse-battery-staple"},
        )
        c.headers.update(
            {
                "X-CSRF-Token": login.json()["state"]["csrf"],
                "Origin": "https://nss.iiitnr.ac.in",
            }
        )
        response = c.post("/api/ceremony/cut", json={"by": "Director"})

    assert response.status_code == 503
    stored = read_state(settings)
    assert stored["inauguration_completed"] is False
    assert stored["site_mode"] == "coming_soon"


# --- the public reads -------------------------------------------------------


def test_public_config_exposes_only_public_facts(client):
    body = client.get("/api/public/config").json()
    assert body["scheduled_at"].endswith("+05:30")
    assert body["ceremony_completed"] is False
    assert "csrf" not in body
    assert "admin" not in body


def test_the_ceremony_state_does_not_leak_the_session(anon_client, logged_in):
    body = anon_client.get("/api/ceremony/state").json()
    assert "csrf" not in body
    assert "token" not in body


def test_enabling_and_disabling_the_ceremony(app, logged_in, web_root, nginx):
    assert logged_in.post("/api/admin/ceremony/enable").status_code == 200
    assert _state(app)["site_mode"] == "inauguration"
    assert settings_include(app) == render_include("inauguration")

    assert logged_in.post("/api/admin/ceremony/disable").status_code == 200
    assert _state(app)["site_mode"] == "coming_soon"
    assert settings_include(app) == render_include("coming_soon")


def test_scheduling_validates_the_offset(app, logged_in):
    bad = logged_in.post("/api/admin/schedule", json={"scheduled_at": "2026-10-02T10:15:00"})
    assert bad.status_code in (400, 422)

    good = logged_in.post(
        "/api/admin/schedule",
        json={"scheduled_at": "2026-10-02T10:15:00+05:30", "countdown_enabled": True},
    )
    assert good.status_code == 200
    assert _state(app)["scheduled_at"] == "2026-10-02T10:15:00+05:30"
