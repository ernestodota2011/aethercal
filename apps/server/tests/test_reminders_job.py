"""Reminder-job tests (RF-10) on an in-memory session.

``run_booking_reminder`` is the pure, testable job body: it loads the booking, sends the reminder
only for a still-``confirmed`` booking, records the ledger, and is idempotent (a second run is a
no-op) — a cancelled booking is skipped with no email. ``schedule_reminder`` hands a fake
:class:`TaskRunner` the 24 h-before job (run time, deterministic id, and the ``booking_id`` kwarg).
The live APScheduler runner is not exercised here (it sits behind a ``# pragma: no cover`` seam).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.models import Booking, EventType, Schedule, SentNotification, Tenant, User
from aethercal.server.jobs.reminders import (
    reminder_job_id,
    run_booking_reminder,
    run_booking_reminder_job,
    schedule_reminder,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)


class RecordingEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


@dataclass
class _ScheduledCall:
    func: Callable[..., object]
    run_at: datetime
    job_id: str
    kwargs: Mapping[str, object]


class RecordingTaskRunner:
    """A fake :class:`TaskRunner` that records the scheduled call instead of using APScheduler."""

    def __init__(self) -> None:
        self.calls: list[_ScheduledCall] = []

    def schedule(
        self,
        func: Callable[..., object],
        *,
        run_at: datetime,
        job_id: str,
        kwargs: Mapping[str, object] | None = None,
    ) -> None:
        self.calls.append(_ScheduledCall(func, run_at, job_id, dict(kwargs or {})))


async def _seed_booking(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
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
    )
    session.add(booking)
    await session.flush()
    return booking


async def _reminder_count(session: AsyncSession, booking: Booking) -> int:
    stmt = (
        select(func.count())
        .select_from(SentNotification)
        .where(SentNotification.booking_id == booking.id, SentNotification.kind == "reminder")
    )
    return int((await session.scalar(stmt)) or 0)


async def test_run_booking_reminder_sends_a_confirmed_reminder_once(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    sent = await run_booking_reminder(sqlite_session, booking_id=booking.id, sender=sender, now=NOW)

    assert sent is True
    assert len(sender.sent) == 1
    assert "Recordatorio" in sender.sent[0]["Subject"]
    assert await _reminder_count(sqlite_session, booking) == 1


async def test_run_booking_reminder_is_idempotent(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    sender = RecordingEmailSender()

    first = await run_booking_reminder(
        sqlite_session, booking_id=booking.id, sender=sender, now=NOW
    )
    second = await run_booking_reminder(
        sqlite_session, booking_id=booking.id, sender=sender, now=NOW
    )

    assert first is True
    assert second is False
    assert len(sender.sent) == 1
    assert await _reminder_count(sqlite_session, booking) == 1


async def test_run_booking_reminder_skips_a_cancelled_booking(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory, status=BookingStatus.CANCELLED)
    sender = RecordingEmailSender()

    sent = await run_booking_reminder(sqlite_session, booking_id=booking.id, sender=sender, now=NOW)

    assert sent is False
    assert sender.sent == []
    assert await _reminder_count(sqlite_session, booking) == 0


async def test_run_booking_reminder_returns_false_for_a_missing_booking(
    sqlite_session: AsyncSession,
) -> None:
    sender = RecordingEmailSender()
    sent = await run_booking_reminder(
        sqlite_session, booking_id=uuid.uuid4(), sender=sender, now=NOW
    )
    assert sent is False
    assert sender.sent == []


def test_schedule_reminder_hands_the_runner_the_24h_before_job() -> None:
    booking_id = uuid.uuid4()
    booking = Booking(id=booking_id, start_at=_START, end_at=_START + timedelta(minutes=30))
    runner = RecordingTaskRunner()
    send_at = _START - timedelta(hours=24)

    schedule_reminder(runner, booking=booking, send_at=send_at)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.run_at == send_at
    assert call.job_id == reminder_job_id(booking_id)
    assert call.kwargs == {"booking_id": str(booking_id)}
    assert call.func is run_booking_reminder_job


def test_reminder_job_id_is_deterministic_per_booking() -> None:
    booking_id = uuid.uuid4()
    assert reminder_job_id(booking_id) == reminder_job_id(booking_id)
    assert str(booking_id) in reminder_job_id(booking_id)
