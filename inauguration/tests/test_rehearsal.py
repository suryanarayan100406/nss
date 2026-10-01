"""The rehearsal harness itself.

``dev/rehearse.py`` is the only place the ceremony can be practised end to end, so
when the harness is subtly wrong the ceremony looks broken instead. Its settings are
built here rather than hardcoded, which is what these tests pin down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DEV = Path(__file__).resolve().parents[1] / "dev"
if str(DEV) not in sys.path:
    sys.path.insert(0, str(DEV))

import rehearse  # noqa: E402


def test_the_rehearsal_allows_the_origin_it_is_served_on(tmp_path: Path):
    """A browser sends the port whenever it is not the default.

    Matching only ``http://127.0.0.1`` passes every read — the ceremony page loads,
    the state is fetched, the admin signs in — and then refuses the one request that
    matters, with a 403 that reads as a broken button rather than a misconfigured
    rehearsal. Production is on 443, where the bare origin is correct, so this only
    ever bites the harness.
    """
    settings = rehearse.build_settings(tmp_path, "127.0.0.1", 8787)

    assert "http://127.0.0.1:8787" in settings.allowed_origins
    assert "http://localhost:8787" in settings.allowed_origins
    # The portless forms stay, so a run behind a proxy on 80 still works.
    assert "http://127.0.0.1" in settings.allowed_origins


def test_the_port_is_not_baked_in(tmp_path: Path):
    """--port is a documented flag; the origins must follow it."""
    settings = rehearse.build_settings(tmp_path, "127.0.0.1", 9100)
    assert "http://127.0.0.1:9100" in settings.allowed_origins
    assert "http://127.0.0.1:8787" not in settings.allowed_origins


@pytest.mark.parametrize("as_admin", [True, False])
def test_the_rehearsal_can_actually_commit_a_cut(tmp_path: Path, as_admin: bool):
    """The whole point of the harness: an admin cuts, and the mode really moves.

    Run as a signed-in admin it must succeed; run with no session it must be refused
    and leave the state alone. Both through the real app, over the real origin.
    """
    from fastapi.testclient import TestClient

    from app.state import read_state

    root = tmp_path / "rehearsal"
    app, settings = rehearse.build_app(root, "127.0.0.1", 8787)
    origin = "http://127.0.0.1:8787"

    with TestClient(app) as client:
        if as_admin:
            login = client.post(
                "/api/admin/login",
                json={"username": rehearse.REHEARSAL_USER, "password": rehearse.REHEARSAL_PASSWORD},
            )
            assert login.status_code == 200, login.text
            client.headers.update(
                {"X-CSRF-Token": login.json()["state"]["csrf"], "Origin": origin}
            )

        response = client.post("/api/ceremony/cut", json={"by": "Rehearsal Director"})

    state = read_state(settings)
    if as_admin:
        assert response.status_code == 200, response.text
        assert state["site_mode"] == "permanent"
        assert state["inauguration_completed"] is True
    else:
        assert response.status_code in (401, 403), response.text
        assert state["inauguration_completed"] is False
        assert state["site_mode"] != "permanent"


def test_the_rehearsal_never_runs_the_cleanup(tmp_path: Path):
    """Stated in the harness's own docstring; asserted so it stays true."""
    ok, message = rehearse.refuse_cleanup(None)
    assert ok is False
    assert "disabled" in message.lower()
