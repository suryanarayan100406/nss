"""The state document: validation, atomicity and the transition rules."""

from __future__ import annotations

import json
import threading

import pytest

from app import state as st


def test_default_state_is_valid():
    assert st.validate(st.default_state())["site_mode"] == "coming_soon"


def test_first_read_creates_the_document(settings):
    assert not settings.site_json.exists()
    created = st.read_state(settings)
    assert settings.site_json.exists()
    assert created["scheduled_at"] == st.DEFAULT_SCHEDULED_AT


# --- invariants -------------------------------------------------------------


def test_completed_but_not_permanent_is_rejected():
    bad = st.default_state()
    bad["inauguration_completed"] = True
    bad["site_mode"] = "inauguration"
    with pytest.raises(st.InvalidState):
        st.validate(bad)


def test_permanent_without_completion_or_decommission_is_rejected():
    bad = st.default_state()
    bad["site_mode"] = "permanent"
    with pytest.raises(st.InvalidState):
        st.validate(bad)


def test_inauguration_mode_requires_the_flag():
    bad = st.default_state()
    bad["site_mode"] = "inauguration"
    with pytest.raises(st.InvalidState):
        st.validate(bad)


def test_coming_soon_cannot_have_the_ceremony_enabled():
    bad = st.default_state()
    bad["inauguration_enabled"] = True
    with pytest.raises(st.InvalidState):
        st.validate(bad)


def test_decommissioned_cannot_still_be_enabled():
    bad = st.default_state()
    bad["decommissioned"] = True
    bad["site_mode"] = "permanent"
    bad["inauguration_completed"] = True
    bad["completed_at"] = "2026-10-02T04:45:00+00:00"
    bad["completed_by"] = "Director"
    bad["inauguration_enabled"] = True
    with pytest.raises(st.InvalidState):
        st.validate(bad)


@pytest.mark.parametrize("value", ["2026-10-02T10:15:00", "not a date", ""])
def test_scheduled_at_must_carry_an_offset(value):
    """Without an offset the countdown would depend on the visitor's own clock."""
    bad = st.default_state()
    bad["scheduled_at"] = value
    with pytest.raises(st.InvalidState):
        st.validate(bad)


def test_scheduled_at_accepts_an_explicit_offset():
    good = st.default_state()
    good["scheduled_at"] = "2026-10-02T10:15:00+05:30"
    assert st.validate(good)["scheduled_at"] == "2026-10-02T10:15:00+05:30"


# --- reading and writing ----------------------------------------------------


def test_a_bom_does_not_corrupt_the_document(settings):
    """Notepad writes a BOM by default; the record must survive an edit by hand."""
    payload = st.default_state()
    payload["countdown_enabled"] = False
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.site_json.write_text("﻿" + json.dumps(payload), encoding="utf-8")

    assert st.read_state(settings)["countdown_enabled"] is False


def test_a_corrupt_document_raises_rather_than_being_ignored(settings):
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.site_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(st.InvalidState):
        st.read_state(settings)


def test_writing_leaves_no_temporary_files(settings):
    st.write_state(settings, st.default_state())
    leftovers = [p.name for p in settings.config_dir.iterdir() if p.name.startswith(".tmp")]
    assert leftovers == []


def test_a_rejected_mutation_does_not_touch_the_file(settings):
    st.write_state(settings, st.default_state())
    before = settings.site_json.read_bytes()

    def corrupt(state):
        state["site_mode"] = "permanent"  # invalid without a completion
        return state

    with pytest.raises(st.InvalidState):
        st.update_state(settings, corrupt)

    assert settings.site_json.read_bytes() == before


def test_concurrent_updates_do_not_lose_a_write(settings):
    """Read-modify-write is serialised by the lock, so no update is dropped."""
    st.write_state(settings, st.default_state())
    seen: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=10)

            def mutate(state):
                # Read the current counter, then write it back incremented. Without
                # the lock these would interleave and lose increments.
                value = state.setdefault("test_counter", 0) + 1
                state["test_counter"] = value
                return state

            # test_counter is not part of the schema, so go through the raw primitives.
            with st.file_lock(settings.lock_file):
                current = st.read_state(settings)
                current["test_counter"] = current.get("test_counter", 0) + 1
                st._atomic_write_json(settings.site_json, st.validate(current))
            seen.append(n)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    assert len(seen) == 4
    assert st.read_state(settings)["test_counter"] == 4


# --- transitions ------------------------------------------------------------


def test_the_expected_path_is_walkable():
    s = st.default_state()
    s = st.transition_mode(s, "inauguration")
    assert s["inauguration_enabled"] is True
    s = st.complete_ceremony(s, by="Director", at="2026-10-02T04:45:00+00:00")
    assert s["site_mode"] == "permanent"
    assert s["inauguration_completed"] is True
    assert s["completed_by"] == "Director"


def test_permanent_is_terminal():
    s = st.default_state()
    s = st.transition_mode(s, "inauguration")
    s = st.complete_ceremony(s, by="Director", at="2026-10-02T04:45:00+00:00")
    with pytest.raises(st.InvalidTransition):
        st.transition_mode(s, "coming_soon")


def test_completing_twice_preserves_the_original_record():
    """The record of who inaugurated the site is written exactly once."""
    s = st.default_state()
    s = st.complete_ceremony(s, by="First Director", at="2026-10-02T04:45:00+00:00")
    s = st.complete_ceremony(s, by="Second Director", at="2026-10-03T09:00:00+00:00")
    assert s["completed_by"] == "First Director"
    assert s["completed_at"] == "2026-10-02T04:45:00+00:00"


def test_decommissioning_lands_in_permanent():
    s = st.default_state()
    s = st.transition_mode(s, "inauguration")
    s = st.complete_ceremony(s, by="Director", at="2026-10-02T04:45:00+00:00")
    s = st.mark_decommissioned(s)
    assert s["site_mode"] == "permanent"
    assert s["decommissioned"] is True
