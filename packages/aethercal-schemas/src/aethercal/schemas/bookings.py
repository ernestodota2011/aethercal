"""Booking request/response schemas (F1-05, RF-07/RF-16): the booking API contract.

Pure transport DTOs — they check *shape* (types, bounds, a real IANA guest timezone), never
calendar semantics; the service and the pure ``aethercal.core`` engines own slot validity and the
anti-double-booking rules. A booking is requested with only its ``start``: the server derives
``end`` from the event type's duration, so the two can never disagree.

``BookingRead`` is built straight from the ORM row (``from_attributes``); the wire names ``start`` /
``end`` map from the columns ``start_at`` / ``end_at`` via ``validation_alias``, matching the slots
contract. The internal Google ``external_event_id`` is intentionally NOT exposed (RF-16); the
guest-facing ``meeting_url`` is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aethercal.core.model import BookingStatus

GuestName = Annotated[str, Field(min_length=1, max_length=255)]
GuestEmail = Annotated[str, Field(min_length=3, max_length=320)]
GuestTimezone = Annotated[str, Field(min_length=1, max_length=64)]


def _require_iana_zone(value: str) -> str:
    """Reject a guest timezone that is not a real IANA zone (so email/ICS rendering never fails)."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {value!r}") from exc
    return value


def _require_emailish(value: str) -> str:
    """A light structural check (a single ``@`` with non-empty local/domain, no spaces).

    Deliberately not full RFC 5322 validation (no extra dependency): the transactional email either
    reaches the guest or does not — a stricter gate belongs to the sending layer, not this contract.
    """
    candidate = value.strip()
    local, _, domain = candidate.partition("@")
    if not local or not domain or " " in candidate or candidate.count("@") != 1:
        raise ValueError("guest_email is not a valid email address")
    return candidate


class BookingCreate(BaseModel):
    """Request body to book a slot (RF-07). Only ``start`` is sent; ``end`` is server-derived."""

    event_type_id: uuid.UUID
    start: datetime
    guest_name: GuestName
    guest_email: GuestEmail
    guest_timezone: GuestTimezone
    guest_notes: Annotated[str | None, Field(max_length=2000)] = None
    answers: dict[str, Any] | None = None
    locale: Annotated[str | None, Field(max_length=16)] = None

    @field_validator("guest_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return _require_iana_zone(value)

    @field_validator("guest_email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _require_emailish(value)


class BookingReschedule(BaseModel):
    """Request body to reschedule a booking to a new start (RF-07)."""

    new_start: datetime


class BookingRead(BaseModel):
    """A booking as returned by every read/write path — built from the ORM row (from_attributes).

    ``start`` / ``end`` map from the ``start_at`` / ``end_at`` columns; the internal
    ``external_event_id`` is never exposed (RF-16).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type_id: uuid.UUID
    start: datetime = Field(validation_alias="start_at")
    end: datetime = Field(validation_alias="end_at")
    status: BookingStatus
    guest_name: str
    guest_email: str
    guest_timezone: str
    guest_notes: str | None
    answers: dict[str, Any]
    meeting_url: str | None
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    created_at: datetime


__all__ = ["BookingCreate", "BookingRead", "BookingReschedule"]
