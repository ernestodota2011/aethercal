"""Pure composition tests for booking emails (RF-08 / RNF-1).

Exercises :func:`build_notification_email`: the subject per kind and locale (ES primary + EN), a
parseable ``text/calendar`` invite (a single VEVENT with summary/start/end/uid/organizer/attendee),
and the cancel/reschedule links rendered into the body. No network, no scheduler, no database — the
composition is a pure function of its inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import pytest
from icalendar import Calendar

from aethercal.server.integrations.smtp.compose import (
    BookingEmailContext,
    NotificationKind,
    build_notification_email,
)

_START = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
_END = datetime(2026, 7, 10, 15, 30, tzinfo=UTC)
_CANCEL_URL = "https://cal.example.com/b/cancel/tok-abc"
_RESCHEDULE_URL = "https://cal.example.com/b/reschedule/tok-abc"


def _context(**overrides: Any) -> BookingEmailContext:
    base: dict[str, Any] = {
        "uid": "booking-123@aethercal",
        "event_title": "Consulta 30 min",
        "guest_name": "Ada Guest",
        "guest_email": "guest@example.com",
        "host_name": "Grace Host",
        "host_email": "host@example.com",
        "start_at": _START,
        "end_at": _END,
        "guest_timezone": "America/New_York",
        "sequence": 0,
        "meeting_url": "https://meet.example.com/abc",
        "cancel_url": _CANCEL_URL,
        "reschedule_url": _RESCHEDULE_URL,
    }
    base.update(overrides)
    return BookingEmailContext(**base)


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    assert part is not None
    content = part.get_content()
    assert isinstance(content, str)
    return content


def _ics_text(msg: EmailMessage) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            content = part.get_content()
            return content if isinstance(content, str) else content.decode("utf-8")
    raise AssertionError("no text/calendar part found in the message")


def _calendar(kind: NotificationKind, *, sequence: int = 0) -> Any:
    """Parse the ``.ics`` attached to the ``kind`` email for a booking context at ``sequence``."""
    context = _context(sequence=sequence)
    return Calendar.from_ical(_ics_text(build_notification_email(context, kind=kind)))


@pytest.mark.parametrize(
    ("kind", "locale", "expected"),
    [
        (NotificationKind.CONFIRMATION, "es", "Reserva confirmada"),
        (NotificationKind.CONFIRMATION, "en", "Booking confirmed"),
        (NotificationKind.CANCELLATION, "es", "Reserva cancelada"),
        (NotificationKind.CANCELLATION, "en", "Booking cancelled"),
        (NotificationKind.RESCHEDULE, "es", "Reserva reprogramada"),
        (NotificationKind.RESCHEDULE, "en", "Booking rescheduled"),
        (NotificationKind.REMINDER, "es", "Recordatorio"),
        (NotificationKind.REMINDER, "en", "Reminder"),
    ],
)
def test_subject_per_kind_and_locale(kind: NotificationKind, locale: str, expected: str) -> None:
    msg = build_notification_email(_context(), kind=kind, locale=locale)
    subject = msg["Subject"]
    assert expected in subject
    assert "Consulta 30 min" in subject  # the event title is always in the subject


def test_default_locale_is_spanish() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.CONFIRMATION)
    assert "Reserva confirmada" in msg["Subject"]


def test_unknown_locale_falls_back_to_spanish() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.CONFIRMATION, locale="fr")
    assert "Reserva confirmada" in msg["Subject"]


def test_guest_is_recipient_and_host_is_copied_on_transactional() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.CONFIRMATION)
    assert "guest@example.com" in msg["To"]
    assert msg["Cc"] is not None
    assert "host@example.com" in msg["Cc"]


def test_reminder_goes_to_the_guest_only() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.REMINDER)
    assert "guest@example.com" in msg["To"]
    assert msg["Cc"] is None


def test_body_contains_cancel_and_reschedule_links() -> None:
    body = _body_text(build_notification_email(_context(), kind=NotificationKind.CONFIRMATION))
    assert _CANCEL_URL in body
    assert _RESCHEDULE_URL in body


def test_body_contains_meeting_url_and_event_title() -> None:
    body = _body_text(build_notification_email(_context(), kind=NotificationKind.CONFIRMATION))
    assert "https://meet.example.com/abc" in body
    assert "Consulta 30 min" in body


def test_body_omits_links_when_not_provided() -> None:
    ctx = _context(cancel_url=None, reschedule_url=None)
    body = _body_text(build_notification_email(ctx, kind=NotificationKind.REMINDER))
    assert "https://cal.example.com" not in body


def test_ics_attachment_is_a_single_parseable_vevent() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.CONFIRMATION)
    cal: Any = Calendar.from_ical(_ics_text(msg))
    vevents: list[Any] = cal.walk("VEVENT")
    assert len(vevents) == 1
    vevent = vevents[0]
    assert str(vevent["UID"]) == "booking-123@aethercal"
    assert str(vevent["SUMMARY"]) == "Consulta 30 min"
    assert vevent["DTSTART"].dt == _START
    assert vevent["DTEND"].dt == _END
    assert "host@example.com" in str(vevent["ORGANIZER"])
    assert "guest@example.com" in str(vevent["ATTENDEE"])


def test_ics_attachment_has_ics_filename() -> None:
    msg = build_notification_email(_context(), kind=NotificationKind.CONFIRMATION)
    calendar_parts = [p for p in msg.walk() if p.get_content_type() == "text/calendar"]
    assert len(calendar_parts) == 1
    assert calendar_parts[0].get_filename() == "invite.ics"


def test_confirmation_ics_is_a_request_to_add() -> None:
    """RF-08: a confirmation tells the calendar to ADD the event (METHOD:REQUEST, sequence 0)."""
    cal = _calendar(NotificationKind.CONFIRMATION, sequence=0)
    vevent = cal.walk("VEVENT")[0]
    assert str(cal["METHOD"]) == "REQUEST"
    assert str(vevent["STATUS"]) == "CONFIRMED"
    assert int(vevent["SEQUENCE"]) == 0


def test_cancellation_ics_is_a_cancel() -> None:
    """RF-08: a cancellation must CANCEL the event, not re-add it (METHOD:CANCEL)."""
    cal = _calendar(NotificationKind.CANCELLATION, sequence=3)
    vevent = cal.walk("VEVENT")[0]
    assert str(cal["METHOD"]) == "CANCEL"
    assert str(vevent["STATUS"]) == "CANCELLED"


def test_reschedule_ics_is_a_request_to_update() -> None:
    """RF-08: a reschedule is an UPDATE — same UID, METHOD:REQUEST, still CONFIRMED."""
    cal = _calendar(NotificationKind.RESCHEDULE, sequence=1)
    vevent = cal.walk("VEVENT")[0]
    assert str(cal["METHOD"]) == "REQUEST"
    assert str(vevent["STATUS"]) == "CONFIRMED"


def test_ics_sequence_is_the_persisted_context_sequence() -> None:
    """F1-08: SEQUENCE is the booking's persisted counter, not a by-kind constant — so successive
    updates to the same UID strictly increase (RFC 5545) instead of every reschedule emitting 1.
    """
    for kind in (
        NotificationKind.CONFIRMATION,
        NotificationKind.RESCHEDULE,
        NotificationKind.CANCELLATION,
        NotificationKind.REMINDER,
    ):
        for expected in (0, 1, 2, 7):
            vevent = _calendar(kind, sequence=expected).walk("VEVENT")[0]
            assert int(vevent["SEQUENCE"]) == expected


def test_all_lifecycle_kinds_share_the_same_uid() -> None:
    """RF-08: cancel/reschedule reference the SAME calendar event as the original confirmation."""
    uids = {
        str(_calendar(kind).walk("VEVENT")[0]["UID"])
        for kind in (
            NotificationKind.CONFIRMATION,
            NotificationKind.RESCHEDULE,
            NotificationKind.CANCELLATION,
        )
    }
    assert uids == {"booking-123@aethercal"}
