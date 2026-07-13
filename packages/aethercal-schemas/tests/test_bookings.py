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
from aethercal.core.tz import require_iana_zone as core_require_iana_zone
from aethercal.schemas.bookings import (
    BookingCreate,
    BookingRead,
    BookingReschedule,
    normalize_phone,
    require_iana_zone,
)


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


# --------------------------------------------------------------------------------------
# Guest phone + consent (RF-24). The phone is E.164 on the wire; the consent is a BOOLEAN the
# server stamps into ``bookings.guest_phone_consent_at``. A checkbox whose answer is discarded is
# not consent, so the contract has to carry it — these tests pin that it does.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+13054131728", "+13054131728"),  # already canonical
        ("+1 (305) 413-1728", "+13054131728"),  # the formatting a human actually types
        ("  +34 600 123 456  ", "+34600123456"),  # surrounding whitespace
        ("+1.305.413.1728", "+13054131728"),  # dot-separated
    ],
)
def test_normalize_phone_strips_human_formatting_to_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "3054131728",  # no country code: we must NEVER guess one
        "+0305413172",  # E.164 country codes never start at 0
        "+1305413172812345",  # > 15 digits
        "+",  # no digits
        "",  # empty
        "not a phone",
        "+1-305-413-1728 ext 4",  # trailing junk is not silently dropped
        "+1305413172a",  # a letter hiding among the digits
    ],
)
def test_normalize_phone_rejects_anything_that_is_not_e164(raw: str) -> None:
    assert normalize_phone(raw) is None


def test_booking_create_defaults_to_no_phone_and_no_consent() -> None:
    payload = BookingCreate.model_validate(_valid_create_kwargs())
    assert payload.guest_phone is None
    assert payload.guest_phone_consent is False  # never pre-ticked, never assumed


def test_booking_create_normalizes_and_carries_a_consented_phone() -> None:
    payload = BookingCreate.model_validate(
        {
            **_valid_create_kwargs(),
            "guest_phone": "+1 (305) 413-1728",
            "guest_phone_consent": True,
        }
    )
    assert payload.guest_phone == "+13054131728"
    assert payload.guest_phone_consent is True


def test_booking_create_accepts_a_phone_without_consent() -> None:
    """A number with no consent is a REAL state the outbox gate is built to refuse — not an error.

    The guest typed a number but did not tick the box: we may hold it, we may never message it.
    """
    payload = BookingCreate.model_validate(
        {**_valid_create_kwargs(), "guest_phone": "+13054131728"}
    )
    assert payload.guest_phone == "+13054131728"
    assert payload.guest_phone_consent is False


def test_booking_create_rejects_a_malformed_phone() -> None:
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({**_valid_create_kwargs(), "guest_phone": "305-413-1728"})


def test_booking_create_rejects_consent_for_a_phone_that_was_never_given() -> None:
    """Consent that references no number consents to nothing. Fail loudly rather than stamp it."""
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({**_valid_create_kwargs(), "guest_phone_consent": True})


# --------------------------------------------------------------------------------------
# The IANA-zone rule itself — the ONE owner. ``services/users.py`` (the host) and ``api/slots.py``
# (the ``tz`` query param) both delegate here, so a hole in this function is a hole in every
# surface at once — which is the whole argument for there being only one of it.
# --------------------------------------------------------------------------------------
#: Keys that ``ZoneInfo`` refuses with a raw ``OSError`` instead of ``ZoneInfoNotFoundError``,
#: because it resolves a key to a PATH and opens it. ``"America"`` and ``"Etc"`` are DIRECTORIES of
#: the tz database (folders, not zones); the rest cannot be filenames at all. Every one of them used
#: to escape this function uncaught — a 500 on ``GET /slots?tz=America``, and on a booking whose
#: ``guest_timezone`` is ``"Etc"``, both of which the contract answers with a clean refusal.
UNNAMEABLE_ZONE_KEYS = ["America", "Etc", " ", "UTC\n", "A" * 300]


@pytest.mark.parametrize("key", UNNAMEABLE_ZONE_KEYS)
def test_require_iana_zone_refuses_a_key_the_filesystem_chokes_on(key: str) -> None:
    """Refused in the rule's own currency — a ``ValueError`` — never an escaping ``OSError``.

    The currency matters as much as the refusal: ``BookingCreate`` needs a ``ValueError`` for
    Pydantic to turn it into a 422, and ``services/users.py`` catches exactly ``ValueError`` to
    word its ``InvalidUserError``. An ``OSError`` sails straight through all three call sites.
    """
    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone(key)


@pytest.mark.parametrize("key", UNNAMEABLE_ZONE_KEYS)
def test_booking_create_refuses_a_key_the_filesystem_chokes_on(key: str) -> None:
    """And the refusal reaches the wire as a validation error, not a crash."""
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({**_valid_create_kwargs(), "guest_timezone": key})


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Etc/GMT+5", "Pacific/Kiritimati"])
def test_require_iana_zone_still_accepts_a_real_zone(zone: str) -> None:
    """The refusal was widened, not the net: a real zone is still returned unchanged."""
    assert require_iana_zone(zone) == zone


def test_the_schema_layer_re_exports_the_domain_rule_it_does_not_own_one() -> None:
    """One owner, and this is not it — ``schemas`` imports the rule, it does not re-implement it.

    The rule is domain logic about time, so it lives in ``aethercal.core.tz``, where the two core
    models (``Event``, ``Schedule``) can also reach it — ``core`` may not import ``schemas`` (the
    "core is pure" import contract), so the reverse arrangement was never available. This identity
    assertion is what stops another copy from growing back here: a re-implementation, however
    faithful on the day it is written, would fail this line.
    """
    assert require_iana_zone is core_require_iana_zone


@pytest.mark.parametrize("zone", ["utc", "UTC ", "america/new_york"])
def test_require_iana_zone_refuses_a_zone_only_a_case_insensitive_filesystem_would_accept(
    zone: str,
) -> None:
    """``"utc"`` used to be a valid guest timezone on Windows/macOS and invalid on Linux.

    ``ZoneInfo`` opened the file ``UTC`` for the key ``"utc"`` on a case-insensitive volume; Windows
    even trimmed a trailing space. Production is Linux and refused both — so a booking that
    validated on a developer's machine was a 422 in production. The rule is now set membership: the
    filesystem gets no vote on what an IANA zone is.
    """
    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone(zone)
