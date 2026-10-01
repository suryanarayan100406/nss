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


# ---------------------------------------------------------------------------
# Starting clean
# ---------------------------------------------------------------------------


def _seed_an_inaugurated_rehearsal(state_dir: Path) -> None:
    """Leave the state directory as a finished rehearsal leaves it.

    ``state_dir`` is the directory main() uses — ``<repo>/.rehearsal`` — not the
    repository itself. Seeding the wrong directory makes these tests pass without
    exercising anything, because main() resets a location the assertion never reads.
    """
    from app.state import default_state, write_state

    state_dir.mkdir(parents=True, exist_ok=True)
    settings = rehearse.build_settings(state_dir, "127.0.0.1", 8787)
    state = default_state()
    state.update(
        {
            "site_mode": "permanent",
            "countdown_enabled": False,
            # The cut disables the ceremony in the same step — a completed
            # inauguration that is still enabled is rejected by validate().
            "inauguration_enabled": False,
            "inauguration_completed": True,
            "completed_at": "2026-10-01T08:09:00+05:30",
            "completed_by": "Prof. (Dr.) Om Prakash Vyas",
        }
    )
    write_state(settings, state)


def _run_main(monkeypatch, repo: Path, argv: list[str]) -> None:
    """Run main() for real, with the server and the repository redirected."""
    import uvicorn

    monkeypatch.setattr(rehearse, "REPO", repo)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    assert rehearse.main(argv) == 0


def _state_after_run(tmp_path: Path, monkeypatch, argv: list[str]):
    from app.state import read_state

    repo = tmp_path / "repo"
    state_dir = repo / ".rehearsal"
    _seed_an_inaugurated_rehearsal(state_dir)
    _run_main(monkeypatch, repo, argv)
    return read_state(rehearse.build_settings(state_dir, "127.0.0.1", 8787))


def test_a_plain_run_discards_the_previous_rehearsal(tmp_path: Path, monkeypatch):
    """Running it again is the common case, and the point is to watch the ceremony
    from the beginning.

    Resuming is what surprises: a site left in its inaugurated state answers the
    ceremony page with "this ceremony has already been held", which is exactly the
    page the harness exists to demonstrate, and it reads as a broken rehearsal rather
    than a stale one. So a plain run starts clean and --resume is the opt-in.
    """
    state = _state_after_run(tmp_path, monkeypatch, [])

    assert state["site_mode"] == "coming_soon"
    assert state["inauguration_completed"] is False
    assert state["completed_by"] is None
    assert state["inauguration_enabled"] is False


def test_resume_keeps_the_previous_rehearsal(tmp_path: Path, monkeypatch):
    """The other half: --resume is how you inspect what a cut left behind."""
    state = _state_after_run(tmp_path, monkeypatch, ["--resume"])

    assert state["site_mode"] == "permanent"
    assert state["inauguration_completed"] is True
    assert state["completed_by"] == "Prof. (Dr.) Om Prakash Vyas"


def test_reset_is_still_accepted(tmp_path: Path, monkeypatch):
    """--reset used to be how you asked for this. It now names the default, but a
    command written from the older README must not start failing with a usage error."""
    state = _state_after_run(tmp_path, monkeypatch, ["--reset"])

    assert state["site_mode"] == "coming_soon"
