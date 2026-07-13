"""THE SILENCE OF AN UNPAID HOLD (B-05a) — a booking that was never CONFIRMED emits nothing.

We are about to start writing ``BookingStatus.PENDING`` (a hold awaiting payment). Today nobody
writes that status, and three independent paths would announce an appointment **nobody has paid
for**: ``apply_booking_transition`` (which never reads ``booking.status`` at all), the unconditional
``enqueue_event("booking.created")``, and a workflow-rule edit re-materialising steps for a live
hold. A Google Calendar event with the guest as an attendee makes **Google** mail them the
invitation, so an unpaid hold would announce itself.

The rule this wave installs: ==a booking that has never been CONFIRMED produces not one outbound.==

The belt goes in the **FUNNELS**, never in a caller — ``Outbox(...)`` is constructed in exactly ONE
place in the whole source tree (``enqueue_effect``) and ``WebhookDelivery(...)`` in exactly one
(``enqueue_event``). So the tests below call the **funnel directly** with a synthetic PENDING
booking. Going through ``create_booking`` would only prove that the one caller we happened to fix is
fixed — and the next enqueue path nobody foresaw would sail straight past.

Covers §6 criteria 20, 20b, 20c, 20d, 21, 22, 23 and 23b of the Tanda-B spec.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.services.bookings import (
    BookingParams,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import as_utc

_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}

# 2026-07-06 is a Monday; midnight before it opens leaves every weekday slot bookable.
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)
_SLOT_9 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_SLOT_11 = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, EventType]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5)
    session.add(schedule)
    await session.flush()
    event_type = await create_event_type(
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
    return tenant, event_type


def _stamp(booking: Booking) -> datetime:
    """``booking.confirmed_at``, normalised — SQLite drops tzinfo on the round-trip (``as_utc``)."""
    assert booking.confirmed_at is not None
    return as_utc(booking.confirmed_at)


def _params(event_type_id: uuid.UUID, start: datetime) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )


# --------------------------------------------------------------------------------------
# ``confirmed_at`` — the switch the whole belt reads.
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_created_booking_is_stamped_confirmed_at(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A booking that IS confirmed carries the instant it became so. Today that is creation time.

    This is the switch every gate below reads. ``status`` cannot be that switch: a cancelled booking
    was confirmed once (its cancellation notice must go out), and a cancelled HOLD never was (its
    must not) — and both rows read ``cancelled``. Only a stamp of the FIRST confirmation tells those
    two apart, which is exactly the question every outbound has to answer.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
    )

    assert booking.status is BookingStatus.CONFIRMED
    assert _stamp(booking) == _BEFORE


@pytest.mark.asyncio
async def test_a_reschedule_successor_inherits_the_original_confirmation(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The successor of a confirmed booking is confirmed — it INHERITS the stamp, never re-mints it.

    ``reschedule_booking`` does not mutate a booking: it opens a NEW row and cancels the old one. A
    successor left unstamped would make moving a confirmed appointment **silent** — no reschedule
    email, no calendar move, no webhook — with nothing raised anywhere. And the stamp is inherited
    rather than re-taken because it records when this appointment was FIRST confirmed, which a
    reschedule does not change (B-05b hangs the payment on that same chain).
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    later = _BEFORE + timedelta(minutes=5)
    successor = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=later,
    )

    assert successor.id != original.id
    assert successor.status is BookingStatus.CONFIRMED
    assert _stamp(successor) == _BEFORE  # the ORIGINAL confirmation...
    assert _stamp(successor) != later  # ...inherited, not re-minted at the reschedule


@pytest.mark.asyncio
async def test_cancelling_a_confirmed_booking_keeps_its_confirmation_stamp(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A cancelled booking REMEMBERS it was once confirmed — that is what lets its notice go out.

    The stamp is write-once and never cleared. Clearing it on cancellation would silence the
    cancellation email itself: the guest would be told nothing about the appointment they had just
    called off.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    cancelled = await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        now=_BEFORE + timedelta(minutes=5),
    )

    assert cancelled.status is BookingStatus.CANCELLED
    assert _stamp(cancelled) == _BEFORE
