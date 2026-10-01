"""Request and response schemas.

Strict where a value reaches the filesystem or the state document; permissive only
where the field is informational and the server overwrites it anyway.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class ScheduleRequest(BaseModel):
    scheduled_at: str = Field(min_length=1, max_length=64)
    countdown_enabled: bool | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _must_parse(cls, value: str) -> str:
        from .state import InvalidState, _parse_scheduled_at

        try:
            _parse_scheduled_at(value)
        except InvalidState as exc:
            raise ValueError(str(exc)) from exc
        return value.strip()


class CutRequest(BaseModel):
    """The ceremony page reports who cut the ribbon.

    ``at`` is accepted for display but never trusted: the server records its own
    timestamp, because the projector laptop's clock is not the record of truth.
    """

    by: str | None = Field(default=None, max_length=120)
    at: str | None = Field(default=None, max_length=64)


class CleanupRequest(BaseModel):
    phrase: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class CeremonyState(BaseModel):
    mode: str
    ceremony_enabled: bool
    completed: bool
    completed_at: str | None = None
    completed_by: str | None = None
    scheduled_at: str
    countdown_enabled: bool
    preview: bool = False
    can_cut: bool = False
    decommissioned: bool = False


class PublicConfig(BaseModel):
    site: str = "NSS IIIT Naya Raipur"
    mode: str
    scheduled_at: str
    countdown_enabled: bool
    ceremony_completed: bool
    ceremony_enabled: bool


class ActionResponse(BaseModel):
    ok: bool = True
    message: str | None = None
    state: dict[str, Any] | None = None


__all__ = [
    "LoginRequest",
    "ScheduleRequest",
    "CutRequest",
    "CleanupRequest",
    "CeremonyState",
    "PublicConfig",
    "ActionResponse",
]
