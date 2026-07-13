"""The no-show transition (RF-25).

Only from ``confirmed``, and only once the appointment has ENDED — "no-show" is a statement about an
event that already happened, so allowing it earlier would be a cancellation by another name (and,
since a no-show KEEPS its slot, one that destroys the booking without even freeing the time).

==A no-show keeps occupying its slot.== The appointment time has passed; freeing it would corrupt
history and let a booking be written retroactively over it. That is precisely why the ``WHERE status
<> 'cancelled'`` partial unique index needs no change at all — "everything except
cancelled occupies" already covers the new status. The offline proof is here; the REAL proof (the
database actually rejecting the second INSERT) is the Postgres test in ``test_booking_concurrency``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import Booking as CoreBooking
from aethercal.core.model import BookingStatus, TimeInterval
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.db.models import (
    Booking,
    EventType,
    Schedule,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.services.bookings import (
    BookingNotActiveError,
    BookingNotEndedError,
    mark_no_show,
)
from aethercal.server.services.event_types import create_event_type

_START = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_END = _START + timedelta(minutes=30)
_DURING = _START + timedelta(minutes=10)
_AFTER = _END + timedelta(minutes=1)


async def _seed(
    session: AsyncSession, tenant_factory: Any, *, status: BookingStatus
) -> tuple[Tenant, Booking]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules={})
    session.add(schedule)
    await session.flush()
    event_type: EventType = await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        ),
    )
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_START,
        end_at=_END,
        status=status,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return tenant, booking


async def test_a_finished_confirmed_booking_can_be_marked_a_no_show(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)

    marked = await mark_no_show(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER
    )

    assert marked.status is BookingStatus.NO_SHOW
    assert marked.no_show_at == _AFTER


async def test_marking_a_no_show_is_idempotent(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)
    first = await mark_no_show(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER
    )

    again = await mark_no_show(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER + timedelta(hours=1)
    )

    assert again.id == first.id
    # The original stamp stands; it is not re-stamped. (The second call re-reads the row under the
    # lock, and SQLite hands the timestamp back naive — normalise before comparing the instant.)
    assert again.no_show_at is not None
    assert again.no_show_at.replace(tzinfo=UTC) == _AFTER


async def test_a_booking_that_has_not_ended_cannot_be_marked_a_no_show(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """ "No-show" is a statement about an appointment that already happened. Allowing it early would
    be a cancellation by another name — and since a no-show KEEPS its slot, it would destroy the
    guest's booking without even freeing the time."""
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)

    with pytest.raises(BookingNotEndedError):
        await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_DURING)

    assert booking.status is BookingStatus.CONFIRMED


@pytest.mark.parametrize("status", [BookingStatus.CANCELLED, BookingStatus.PENDING])
async def test_only_a_confirmed_booking_can_be_marked_a_no_show(
    sqlite_session: AsyncSession, tenant_factory: Any, status: BookingStatus
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=status)

    with pytest.raises(BookingNotActiveError):
        await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER)


async def test_a_no_show_still_occupies_its_slot(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The domain rule, offline. The DATABASE-level proof (the partial index actually refusing the
    second booking) is ``test_a_no_show_booking_still_blocks_its_slot`` in
    ``test_booking_concurrency.py``, which runs against real PostgreSQL."""
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)

    await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER)

    assert CoreBooking(
        interval=TimeInterval(start=_START, end=_END), status=BookingStatus.NO_SHOW
    ).occupies
    persisted = await sqlite_session.get(Booking, booking.id)
    assert persisted is not None and persisted.status is BookingStatus.NO_SHOW


# --------------------------------------------------------------------------------------
# The no-show WEBHOOK (RF-25). A transition nobody can observe is half a feature: the host's
# CRM/automation learns about a cancellation and a reschedule, but a guest who simply never turned
# up would be invisible to it.
# --------------------------------------------------------------------------------------


async def _subscribe(session: AsyncSession, *, tenant: Tenant, events: list[str]) -> Webhook:
    """An active subscriber for ``events``. ``secret`` is opaque bytes here — the fan-out never
    decrypts it (only the delivery worker does), so the test needs no Fernet key."""
    webhook = Webhook(
        tenant_id=tenant.id,
        url="https://consumer.test/hook",
        secret=b"opaque",
        events=events,
        active=True,
    )
    session.add(webhook)
    await session.flush()
    return webhook


async def _deliveries(session: AsyncSession, event: str) -> list[WebhookDelivery]:
    return list(
        (await session.scalars(select(WebhookDelivery).where(WebhookDelivery.event == event))).all()
    )


async def test_marking_a_no_show_fans_out_the_booking_no_show_webhook(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)
    await _subscribe(sqlite_session, tenant=tenant, events=["booking.no_show"])

    await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER)

    queued = await _deliveries(sqlite_session, "booking.no_show")
    assert len(queued) == 1
    data = queued[0].payload["data"]
    assert data["id"] == str(booking.id)
    # The envelope carries the status the transition just wrote — not the one it had on the way in.
    assert data["status"] == BookingStatus.NO_SHOW.value


async def test_a_repeated_no_show_does_not_fan_out_a_second_webhook(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The idempotent no-op must be silent on the wire too, exactly like the repeated cancel.

    A second delivery would tell the subscriber the guest failed to show up TWICE for one
    appointment — and every retry of a flaky admin click would inflate the host's no-show stats."""
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)
    await _subscribe(sqlite_session, tenant=tenant, events=["booking.no_show"])

    await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER)
    await mark_no_show(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER + timedelta(hours=1)
    )

    assert len(await _deliveries(sqlite_session, "booking.no_show")) == 1


async def test_a_subscriber_that_did_not_ask_for_no_show_receives_nothing(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)
    await _subscribe(sqlite_session, tenant=tenant, events=["booking.cancelled"])

    await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_AFTER)

    assert await _deliveries(sqlite_session, "booking.no_show") == []


async def test_a_refused_no_show_fans_out_nothing(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The appointment has not ended: no state change, and therefore no event about one."""
    tenant, booking = await _seed(sqlite_session, tenant_factory, status=BookingStatus.CONFIRMED)
    await _subscribe(sqlite_session, tenant=tenant, events=["booking.no_show"])

    with pytest.raises(BookingNotEndedError):
        await mark_no_show(sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_DURING)

    assert await _deliveries(sqlite_session, "booking.no_show") == []
