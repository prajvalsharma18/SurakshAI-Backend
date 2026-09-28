"""Explicit consent response and transition shapes."""

from typing import TypedDict


class ConsentStatus(TypedDict):
    consent_type: str
    granted: bool
    timestamp: str | None


class ConsentRecord(TypedDict, total=False):
    consent_id: str
    personnel_id: str
    consent_type: str
    status: str
    granted_at: str | None
    revoked_at: str | None
    updated_at: str
    source: str
    actor_user_id: str


class ConsentHistory(TypedDict):
    items: list[ConsentRecord]
    total: int
