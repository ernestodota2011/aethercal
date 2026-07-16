"""The admin's health panel (RF-25 / R9): what THIS business's outbox and no-show rate are doing.

.. rubric:: Why the panel does not simply render ``collect_metrics``

``observability.collect_metrics`` is deliberately INSTANCE-WIDE — "no tenant id, no slug; not in a
label, not in a value" — because it feeds ``GET /metrics``, the OPERATOR's view, which carries an
operator token precisely so one business's key can never read the numbers of all of them.

The admin is the opposite: it is scoped to ONE tenant, and ``service.py`` states that administering
tenant A can never see tenant B's rows. Rendering the instance-wide snapshot in a tenant's panel
would hand that business the pipeline volume of every other business on the instance — the very leak
the metrics endpoint is locked down to prevent, walked back in through the front door. So the panel
reads the same facts with a ``tenant_id`` on them.

What is NOT re-typed is the vocabulary: ``OutboxStatus`` and ``due_filter`` are imported, because
the drain WRITES those states and this COUNTS them — and a backlog gauge counting a status nobody
writes any more reports a reassuring zero for ever.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.schemas.schedules import ScheduleCreate, TimeRangeSchema
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.admin.service import (
    EventTypeForm,
    HostForm,
    create_event_type_action,
    create_host_action,
    create_schedule_action,
    mark_no_show_action,
    metrics_view,
)
from aethercal.server.db import Base
from aethercal.server.db.models import Outbox, OutboxStatus, Tenant
from aethercal.server.services.bookings import BookingParams, create_booking
from aethercal.server.services.rbac import Principal

# ==The instance's OPERATOR.== These tests drive the panels as the person whose credential is in the
# environment — who drove them before B-02, when they were the only person who could sign in at
# at all. WHO may do WHAT (and what a `member` is refused) is proven in `test_admin_rbac.py`; this
# module is about the panels themselves, so it runs them as the principal that holds everything.
_OPERATOR = Principal.bootstrap_operator()

Sessionmaker = async_sessionmaker[AsyncSession]


def _admin(maker: Sessionmaker) -> AdminRuntime:
    """The session accessor the admin service layer takes, over the offline sessionmaker (B-01).

    The service functions no longer accept a raw ``async_sessionmaker``. Under RLS a session opened
    without a business bound reads ZERO rows — silently — so the factory is private to the runtime
    and the only way in is ``admin_session``, which resolves the business and BINDS it before it
    yields. The suite goes through the same door the panel does: a harness that kept the old
    shortcut would be exercising a seam nobody ships.
    """
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="admin", password_hash="x", tenant_slug=None),
    )


_WEEKLY_9_TO_5 = {day: [TimeRangeSchema(start="09:00", end="17:00")] for day in range(5)}
_MAX_ADVANCE = 60 * 60 * 24 * 30
_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)  # Monday midday
_SLOT = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)  # Monday 09:00 — already OVER at _NOW
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[Sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _business(maker: Sessionmaker, *, slug: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant + host + schedule + event type; returns ``(tenant_id, event_type_id)``."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id

    host = await create_host_action(
        _admin(maker),
        principal=_OPERATOR,
        tenant_slug=slug,
        form=HostForm(name="Host", email=f"{slug}@x.com", timezone="UTC"),
    )
    schedule = await create_schedule_action(
        _admin(maker),
        principal=_OPERATOR,
        tenant_slug=slug,
        data=ScheduleCreate(name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    event_type = await create_event_type_action(
        _admin(maker),
        principal=_OPERATOR,
        tenant_slug=slug,
        form=EventTypeForm(
            host_id=host.id,
            slug="intro",
            title="Intro",
            schedule_id=schedule.id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )
    return tenant_id, event_type.id


async def _book(
    maker: Sessionmaker, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID, start: datetime
) -> uuid.UUID:
    async with maker() as session, session.begin():
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=start,
                guest_name="Guest",
                guest_email="guest@example.com",
                guest_timezone="UTC",
            ),
            now=_BEFORE,
        )
        await session.flush()
        return booking.id


async def _queue(
    maker: Sessionmaker,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    send_at: datetime,
    key: str,
) -> None:
    """An outbox intent due at ``send_at`` (the outbox doubles as the durable scheduler)."""
    async with maker() as session, session.begin():
        session.add(
            Outbox(
                tenant_id=tenant_id,
                booking_id=booking_id,
                effect="notify",
                dedupe_key=key,
                payload={},
                status=OutboxStatus.PENDING.value,
                next_retry_at=send_at,
            )
        )


# --------------------------------------------------------------------------------------
# The panel is the BUSINESS's, not the instance's.
# --------------------------------------------------------------------------------------


async def test_the_panel_never_shows_another_businesss_backlog(maker: Sessionmaker) -> None:
    """==The reason this does not simply render ``collect_metrics``.==

    That snapshot is instance-wide by design (it feeds the operator's ``/metrics``, which a tenant's
    API key deliberately cannot open). Rendered in a tenant's panel it would hand this business the
    pipeline volume of every other business on the instance.
    """
    alpha_id, alpha_event = await _business(maker, slug="alpha")
    beta_id, beta_event = await _business(maker, slug="beta")
    alpha_booking = await _book(maker, tenant_id=alpha_id, event_type_id=alpha_event, start=_SLOT)
    beta_booking = await _book(maker, tenant_id=beta_id, event_type_id=beta_event, start=_SLOT)
    # Beta has a big overdue backlog; alpha has exactly one intent.
    await _queue(maker, tenant_id=alpha_id, booking_id=alpha_booking, send_at=_BEFORE, key="a:1")
    for n in range(5):
        await _queue(
            maker, tenant_id=beta_id, booking_id=beta_booking, send_at=_BEFORE, key=f"b:{n}"
        )

    alpha = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="alpha", now=_NOW)

    assert alpha.outbox_due == 1
    assert alpha.outbox_by_status[OutboxStatus.PENDING.value] == 1


async def test_a_reminder_queued_weeks_out_is_not_backlog(maker: Sessionmaker) -> None:
    """==The number that would get the alarm switched off.==

    The outbox IS the durable scheduler, so a 24 h reminder for a booking three weeks out sits
    ``pending`` for three weeks and is in perfect health. Counting it as backlog makes a healthy
    instance look sick, the operator learns to ignore the panel — and then misses the real thing.
    """
    tenant_id, event_type_id = await _business(maker, slug="acme")
    booking_id = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await _queue(
        maker,
        tenant_id=tenant_id,
        booking_id=booking_id,
        send_at=_NOW + timedelta(days=21),
        key="future",
    )

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert metrics.outbox_by_status[OutboxStatus.PENDING.value] == 1  # it IS queued...
    assert metrics.outbox_due == 0  # ...and it is NOT backlog
    assert metrics.outbox_oldest_due_age_seconds == 0.0


async def test_an_overdue_intent_is_due_and_its_age_is_the_dead_mans_switch(
    maker: Sessionmaker,
) -> None:
    """The one gauge that stays flat on a healthy instance and grows without bound the moment
    nothing is draining."""
    tenant_id, event_type_id = await _business(maker, slug="acme")
    booking_id = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await _queue(
        maker,
        tenant_id=tenant_id,
        booking_id=booking_id,
        send_at=_NOW - timedelta(hours=2),
        key="overdue",
    )

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert metrics.outbox_due == 1
    assert metrics.outbox_oldest_due_age_seconds == 2 * 60 * 60


async def test_every_status_is_present_even_at_zero(maker: Sessionmaker) -> None:
    """==Absent and zero must never look the same.== A dashboard cannot alert on a series that does
    not exist, and "no dead intents" is not the same news as "we stopped counting dead intents"."""
    await _business(maker, slug="acme")

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert set(metrics.outbox_by_status) == {status.value for status in OutboxStatus}
    assert set(metrics.bookings_by_status) == {status.value for status in BookingStatus}


# --------------------------------------------------------------------------------------
# The no-show rate.
# --------------------------------------------------------------------------------------


async def test_the_no_show_rate_counts_the_appointments_that_were_meant_to_happen(
    maker: Sessionmaker,
) -> None:
    """==Cancelled bookings are NOT in the denominator.==

    Nobody was ever expected to attend them. Counting them would make a host's no-show rate improve
    simply because more people cancelled — a number that moves the wrong way is worse than none.
    """
    tenant_id, event_type_id = await _business(maker, slug="acme")
    no_show = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await _book(
        maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT + timedelta(hours=1)
    )
    await _book(
        maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT + timedelta(hours=2)
    )
    await mark_no_show_action(
        _admin(maker), principal=_OPERATOR, tenant_slug="acme", booking_id=no_show, now=_NOW
    )

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    # 1 no-show out of (1 no-show + 2 confirmed) — the three that were meant to happen.
    assert metrics.bookings_by_status[BookingStatus.NO_SHOW.value] == 1
    assert metrics.bookings_by_status[BookingStatus.CONFIRMED.value] == 2
    assert metrics.no_show_ratio == 1 / 3


async def test_a_business_with_no_appointments_has_no_no_show_rate(maker: Sessionmaker) -> None:
    """Zero over zero is not a rate — and it must not be a crash on the one screen an operator opens
    when something has already gone wrong."""
    await _business(maker, slug="acme")

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert metrics.no_show_ratio == 0.0


async def test_the_no_show_rate_is_this_businesss_own(maker: Sessionmaker) -> None:
    alpha_id, alpha_event = await _business(maker, slug="alpha")
    beta_id, beta_event = await _business(maker, slug="beta")
    await _book(maker, tenant_id=alpha_id, event_type_id=alpha_event, start=_SLOT)
    beta_booking = await _book(maker, tenant_id=beta_id, event_type_id=beta_event, start=_SLOT)
    await mark_no_show_action(
        _admin(maker), principal=_OPERATOR, tenant_slug="beta", booking_id=beta_booking, now=_NOW
    )

    alpha = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="alpha", now=_NOW)

    assert alpha.no_show_ratio == 0.0  # beta's no-show is beta's business
    assert alpha.bookings_by_status[BookingStatus.NO_SHOW.value] == 0


# --------------------------------------------------------------------------------------
# The denominator is the appointments that ALREADY SHOULD HAVE HAPPENED.
# --------------------------------------------------------------------------------------

#: The next day, 09:00 — inside the weekly pattern, and comfortably AFTER ``_NOW``. A booking here
#: has not happened yet, so nobody can have failed to show up to it.
_TOMORROW = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


async def test_a_future_booking_is_not_an_appointment_somebody_attended(
    maker: Sessionmaker,
) -> None:
    """==The rate must not count appointments that have not happened yet.==

    ``CONFIRMED`` includes every booking still in the diary. Put those in the denominator and one
    real no-show among a week of upcoming appointments reads as a rate near zero — the host is told
    their reminders are working, on the strength of meetings nobody has attended yet.
    """
    tenant_id, event_type_id = await _business(maker, slug="acme")
    past = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_TOMORROW)
    await mark_no_show_action(
        _admin(maker), principal=_OPERATOR, tenant_slug="acme", booking_id=past, now=_NOW
    )

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    # One appointment was meant to happen. Nobody came to it.
    assert metrics.appointments_expected == 1
    assert metrics.no_show_ratio == 1.0


async def test_the_rate_does_not_improve_just_because_the_diary_filled_up(
    maker: Sessionmaker,
) -> None:
    """==A metric that gets better on its own when business is good measures nothing.==

    This is the failure that makes the bug worse than a wrong number. With future bookings in the
    denominator, the no-show rate FALLS every time a new appointment is taken — so a reminder rule
    that does not work looks like a success on any week the diary fills. And deciding whether the
    reminders work is exactly what this batch built the number for.
    """
    tenant_id, event_type_id = await _business(maker, slug="acme")
    past = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await mark_no_show_action(
        _admin(maker), principal=_OPERATOR, tenant_slug="acme", booking_id=past, now=_NOW
    )
    before = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    # The diary fills up: nine more appointments tomorrow, not one of them yet held.
    for index in range(9):
        await _book(
            maker,
            tenant_id=tenant_id,
            event_type_id=event_type_id,
            start=_TOMORROW + timedelta(minutes=30 * index),
        )

    after = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert before.no_show_ratio == 1.0
    assert after.no_show_ratio == 1.0, (
        "the rate moved because bookings arrived, not because anyone attended"
    )
    assert after.bookings_by_status[BookingStatus.CONFIRMED.value] == 9  # they ARE on the books...
    assert after.appointments_expected == 1  # ...and not one of them has happened yet


async def test_a_confirmed_appointment_that_has_ended_counts_as_attended(
    maker: Sessionmaker,
) -> None:
    """The other half of the rule: a confirmed booking whose hour has PASSED is one the guest turned
    up to (nobody marked them absent), so it belongs in the denominator and pulls the rate down."""
    tenant_id, event_type_id = await _business(maker, slug="acme")
    absent = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await _book(
        maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT + timedelta(hours=1)
    )
    await mark_no_show_action(
        _admin(maker), principal=_OPERATOR, tenant_slug="acme", booking_id=absent, now=_NOW
    )

    metrics = await metrics_view(_admin(maker), principal=_OPERATOR, tenant_slug="acme", now=_NOW)

    assert metrics.appointments_expected == 2  # both were meant to happen; both did, in time
    assert metrics.no_show_ratio == 0.5


async def test_an_appointment_still_running_has_not_happened_yet(maker: Sessionmaker) -> None:
    """The boundary is the END, not the start: a meeting that is under way RIGHT NOW cannot yet be
    a no-show, and counting it would let a busy morning flatter the rate."""
    tenant_id, event_type_id = await _business(maker, slug="acme")
    # 11:30 → 12:00. Read the clock at 11:45 and this appointment is happening as we look at it.
    mid_appointment = datetime(2026, 7, 6, 11, 45, tzinfo=UTC)
    await _book(
        maker,
        tenant_id=tenant_id,
        event_type_id=event_type_id,
        start=datetime(2026, 7, 6, 11, 30, tzinfo=UTC),
    )
    absent = await _book(maker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT)
    await mark_no_show_action(
        _admin(maker),
        principal=_OPERATOR,
        tenant_slug="acme",
        booking_id=absent,
        now=mid_appointment,
    )

    metrics = await metrics_view(
        _admin(maker), principal=_OPERATOR, tenant_slug="acme", now=mid_appointment
    )

    assert metrics.appointments_expected == 1  # only the 09:00 one is over
    assert metrics.no_show_ratio == 1.0
