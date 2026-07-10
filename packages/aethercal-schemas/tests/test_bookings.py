"""Offline contract tests for the booking schemas (F1-05, RF-07/RF-16).

Pure Pydantic — no database. They pin the wire contract: a create payload validates its guest
timezone and email at the edge; a read model maps the ``start_at`` / ``end_at`` columns to the wire
names ``start`` / ``end`` and never carries the internal ``external_event_id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingCreate, BookingRead, BookingReschedule


def _valid_create_kwargs() -> dict[str, object]:
    return {
        "event_type_id": uuid.uuid4(),
        "start": datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "America/New_York",
    }


class _Row:
    """A stand-in ORM row carrying the booking columns under their storage names."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.event_type_id = uuid.uuid4()
        self.start_at = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
        self.end_at = datetime(2026, 7, 6, 9, 30, tzinfo=UTC)
        self.status = BookingStatus.CONFIRMED
        self.guest_name = "Ada"
        self.guest_email = "ada@example.com"
        self.guest_timezone = "UTC"
        self.guest_notes = None
        self.answers: dict[str, object] = {"topic": "roadmap"}
        self.meeting_url = "https://meet.example/abc"
        self.external_event_id = "google-evt-123"  # internal — must NOT surface
        self.rescheduled_from_id = None
        self.cancelled_at = None
        self.created_at = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def test_booking_create_accepts_a_valid_payload() -> None:
    payload = BookingCreate.model_validate(_valid_create_kwargs())
    assert payload.guest_email == "ada@example.com"
    assert payload.locale is None  # server applies the default


def test_booking_create_rejects_an_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({**_valid_create_kwargs(), "guest_timezone": "Mars/Phobos"})


def test_booking_create_rejects_a_malformed_email() -> None:
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({**_valid_create_kwargs(), "guest_email": "not-an-email"})


def test_booking_read_maps_columns_and_hides_internal_fields() -> None:
    read = BookingRead.model_validate(_Row())
    dumped = read.model_dump()
    assert dumped["start"] == datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    assert dumped["end"] == datetime(2026, 7, 6, 9, 30, tzinfo=UTC)
    assert dumped["status"] == BookingStatus.CONFIRMED
    assert dumped["meeting_url"] == "https://meet.example/abc"
    # The internal Google event id is never part of the contract (RF-16).
    assert "external_event_id" not in dumped
    assert "start_at" not in dumped and "end_at" not in dumped


def test_booking_reschedule_carries_the_new_start() -> None:
    body = BookingReschedule.model_validate({"new_start": "2026-07-06T11:00:00+00:00"})
    assert body.new_start == datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
