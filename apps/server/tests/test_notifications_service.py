"""Async service tests for ``send_booking_notification`` (RF-08) on an in-memory session.

A fake recording :class:`EmailSender` captures every composed message. The tests prove: the guest is
mailed with the right localized subject and a parseable ``.ics``; a :class:`SentNotification` ledger
row is written; and the call is **idempotent** per ``(booking, kind)`` — a repeat is a no-op (the
sender fires once, one ledger row), while a different kind sends again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from icalendar import Calendar
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.models import Booking, EventType, Schedule, SentNotification, Tenant, User
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.notifications import send_booking_notification

TenantFactory = Callable[..., Awaitable[Tenant]]

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)


class RecordingEmailSender:
    """A fake :class:`EmailSender` that records every message instead of hitting the network."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class LedgerProbingSender:
    """A fake sender that records whether the ledger row already exists at the moment of sending.

    Reserve-first (RF-08) must INSERT the ``(tenant, booking, kind)`` row *before* the send, so the
    row is already visible here; the previous SELECT-then-send-then-INSERT order left it absent
    until after the send.
    """

    def __init__(self, session: AsyncSession, booking: Booking) -> None:
        self._session = session
        self._booking = booking
        self.reserved_at_send_time: list[bool] = []

    async def send(self, message: EmailMessage) -> None:
        self.reserved_at_send_time.append(await _ledger_count(self._session, self._booking) > 0)


async def _seed_booking(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
    """Seed a Tenant + host User + Schedule + EventType and one Booking; return the booking."""
    tenant = await tenant_factory(
        session, email="host@example.com", name="Grace Host", timezone="America/New_York"
    )
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="America/New_York", rules={})
    session.add(schedule)
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="consulta",
        title="Consulta 30 min",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )
    session.add(event_type)
    await session.flush()
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_START,
        end_at=_START + timedelta(minutes=30),
        status=status,
        guest_name="Ada Guest",
        guest_email="guest@example.com",
        guest_timezone="America/New_York",
        meeting_url="https://meet.example.com/abc",
    )
    session.add(booking)
    await session.flush()
    return booking


async def _ledger_count(session: AsyncSession, booking: Booking, kind: str | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(SentNotification)
        .where(SentNotification.booking_id == booking.id)
    )
    if kind is not None:
        stmt = stmt.where(SentNotification.kind == kind)
    return int((await session.scalar(stmt)) or 0)


async def test_sends_to_guest_and_records_the_ledger_row(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    sent = await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url="https://cal.example.com/cancel/x",
        reschedule_url="https://cal.example.com/reschedule/x",
        sender=sender,
        now=NOW,
    )

    assert sent is True
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert "Reserva confirmada" in message["Subject"]
    assert "guest@example.com" in message["To"]
    assert await _ledger_count(sqlite_session, booking, "confirmation") == 1


async def test_composed_message_carries_the_ics_invite(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )

    ics_parts = [p for p in sender.sent[0].walk() if p.get_content_type() == "text/calendar"]
    assert len(ics_parts) == 1
    cal: Any = Calendar.from_ical(ics_parts[0].get_content())
    vevent = cal.walk("VEVENT")[0]
    assert str(vevent["SUMMARY"]) == "Consulta 30 min"
    assert "guest@example.com" in str(vevent["ATTENDEE"])
    assert "host@example.com" in str(vevent["ORGANIZER"])


async def test_is_idempotent_per_booking_and_kind(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    first = await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )
    second = await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )

    assert first is True
    assert second is False  # skipped: the ledger already had (booking, confirmation)
    assert len(sender.sent) == 1  # sent exactly once
    assert await _ledger_count(sqlite_session, booking, "confirmation") == 1


async def test_distinct_kinds_each_send_once(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    for kind in (NotificationKind.CONFIRMATION, NotificationKind.REMINDER):
        await send_booking_notification(
            sqlite_session,
            kind=kind,
            booking=booking,
            cancel_url=None,
            reschedule_url=None,
            sender=sender,
            now=NOW,
        )

    assert len(sender.sent) == 2
    assert await _ledger_count(sqlite_session, booking) == 2


async def test_locale_english_subject(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
        locale="en",
    )
    assert "Booking confirmed" in sender.sent[0]["Subject"]


async def test_reservation_is_recorded_before_the_send(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Reserve-first (RF-08): the ledger row exists before the sender fires, exactly once."""
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = LedgerProbingSender(sqlite_session, booking)

    sent = await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )

    assert sent is True
    assert sender.reserved_at_send_time == [True]  # reserved BEFORE (and only once across) the send


async def test_skips_send_when_the_row_was_already_reserved(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Reserve-first (RF-08): a concurrent winner's row makes our INSERT fail, so we skip."""
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()
    # Simulate a concurrent caller that already reserved (and committed) the (booking, kind) row.
    sqlite_session.add(
        SentNotification(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            kind=NotificationKind.CONFIRMATION.value,
            sent_at=NOW,
        )
    )
    await sqlite_session.flush()

    sent = await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )

    assert sent is False  # the unique constraint arbitrated: someone else already reserved it
    assert sender.sent == []  # so the guest is not mailed twice
    assert await _ledger_count(sqlite_session, booking, "confirmation") == 1  # no duplicate row


async def test_cancellation_notification_carries_a_cancel_method_ics(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """RF-08: the kind threads through to the invite — a cancellation ships METHOD:CANCEL."""
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    await send_booking_notification(
        sqlite_session,
        kind=NotificationKind.CANCELLATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=NOW,
    )

    ics_part = next(p for p in sender.sent[0].walk() if p.get_content_type() == "text/calendar")
    cal: Any = Calendar.from_ical(ics_part.get_content())
    assert str(cal["METHOD"]) == "CANCEL"
    assert str(cal.walk("VEVENT")[0]["STATUS"]) == "CANCELLED"
