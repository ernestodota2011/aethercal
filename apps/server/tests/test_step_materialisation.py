"""The step PRODUCER: a booking's workflow steps are queued inside the booking's own transaction.

This is what carries RF-10 now that the APScheduler reminder is gone. Retiring that scheduler
without this would leave every NEW booking with no reminder at all — the migration only rescues
the bookings that were already live, so ``main`` would ship a real functional regression: book,
and never be reminded.

The four transitions are exercised end-to-end through the real booking service, against the schema
the REAL migration builds (not ``create_all``), so "a booking made after the migration gets its
reminder" is asserted against the thing that actually runs in production.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.migrate import run_migrations
from aethercal.server.db.models import (
    Booking,
    EventType,
    ExternalCalendarLink,
    Outbox,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.db.models.workflows import Workflow, WorkflowStep, WorkflowTrigger
from aethercal.server.services.bookings import (
    BookingParams,
    cancel_booking,
    create_booking,
    mark_no_show,
    reschedule_booking,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.workflows import seed_default_workflows

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}
_NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)  # a week out: comfortably outside the 24 h window
_LATER = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def migrated(tmp_path: Path) -> AsyncIterator[Sessionmaker]:
    """A database built by the REAL migration chain, not by ``create_all``.

    That is the point of the fixture: the reminder rule is seeded by migration 0005, so a test that
    built its schema from the models would be asserting against a world that does not exist.
    """
    path = tmp_path / "materialisation.sqlite"
    sync_engine = sa.create_engine(f"sqlite:///{path}")
    run_migrations(sync_engine)
    sync_engine.dispose()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Tenant, EventType]:
    """A tenant created AFTER the migration — so its reminder rule comes from the tenant-creation
    seeding, not from 0005 (which only ever saw the tenants that already existed)."""
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Acme")
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_ALWAYS_OPEN)
    session.add_all([host, schedule])
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="intro",
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 60,
    )
    session.add(event_type)
    await session.flush()
    await seed_default_workflows(session, tenant_id=tenant.id)
    return tenant, event_type


def _params(event_type_id: uuid.UUID, start: datetime) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )


async def _steps(session: AsyncSession, booking_id: uuid.UUID) -> list[Outbox]:
    return list(
        (
            await session.scalars(
                select(Outbox).where(
                    Outbox.booking_id == booking_id, Outbox.effect == OutboxEffect.NOTIFY.value
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------------------
# The regression the merge was blocked on.
# --------------------------------------------------------------------------------------


async def test_a_booking_created_after_the_migration_gets_its_reminder(
    migrated: Sessionmaker,
) -> None:
    """The blocker. The APScheduler jobstore is gone; if nothing materialises the reminder inside
    the booking's transaction, a guest books and is simply never reminded."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session:
        steps = await _steps(session, booking_id)

    assert len(steps) == 1, "the new booking got NO reminder"
    step = steps[0]
    assert step.payload["trigger"] == WorkflowTrigger.BEFORE_START.value
    assert step.payload["kind"] == "reminder"
    assert step.payload["channel"] == "email"
    assert step.status == "pending"
    # Due 24 h before the start — the outbox's next_retry_at IS the send time.
    assert step.next_retry_at is not None
    assert step.next_retry_at.replace(tzinfo=UTC) == _SLOT - timedelta(hours=24)


async def test_the_step_commits_atomically_with_its_booking(migrated: Sessionmaker) -> None:
    """It is queued in the booking's OWN transaction, so a rolled-back booking cannot leave a step
    behind — the same guarantee the webhook and the email intent already have."""
    async with migrated() as session:
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id
        assert len(await _steps(session, booking_id)) == 1
        await session.rollback()

    async with migrated() as session:
        assert await session.get(Booking, booking_id) is None
        assert await _steps(session, booking_id) == []


async def test_a_booking_inside_the_reminder_window_is_skipped_not_sent_late(
    migrated: Sessionmaker,
) -> None:
    """Booked less than 24 h out: the send time is already past. A ``next_retry_at`` in the past
    drains IMMEDIATELY, so a "reminder" would land after the fact — noise. It is skipped."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        soon = _NOW + timedelta(hours=3)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, soon), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session:
        assert await _steps(session, booking_id) == []


# --------------------------------------------------------------------------------------
# The transition table, driven through the real service.
# --------------------------------------------------------------------------------------


async def test_rescheduling_voids_the_predecessors_steps_and_materialises_the_successors(
    migrated: Sessionmaker,
) -> None:
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        first = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        old_id = first.id
        second = await reschedule_booking(
            session, tenant_id=tenant.id, booking_id=old_id, new_start=_LATER, now=_NOW
        )
        new_id = second.id

    async with migrated() as session:
        old_steps = await _steps(session, old_id)
        new_steps = await _steps(session, new_id)

    # The replaced booking's step is RETIRED — it can never come due again.
    assert [step.status for step in old_steps] == ["voided"]
    assert old_steps[0].next_retry_at is None
    # The successor is a NEW row, so its step is a plain INSERT, timed off the NEW start.
    assert len(new_steps) == 1
    assert new_steps[0].status == "pending"
    assert new_steps[0].next_retry_at is not None
    assert new_steps[0].next_retry_at.replace(tzinfo=UTC) == _LATER - timedelta(hours=24)


async def test_cancelling_retires_the_pending_reminder(migrated: Sessionmaker) -> None:
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id
        await cancel_booking(session, tenant_id=tenant.id, booking_id=booking_id, now=_NOW)

    async with migrated() as session:
        steps = await _steps(session, booking_id)

    assert [step.status for step in steps] == ["voided"], (
        "a cancelled booking kept a live reminder — the guest would be reminded of a booking that "
        "no longer exists"
    )


async def test_a_no_show_voids_the_pending_after_end_follow_up(migrated: Sessionmaker) -> None:
    """Otherwise the guest who did NOT show up receives "thanks for meeting with us"."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        tenant_id = tenant.id
        follow_up = Workflow(
            tenant_id=tenant_id,
            event_type_id=None,
            name="follow-up",
            trigger=WorkflowTrigger.AFTER_END.value,
            offset_minutes=60,
            active=True,
        )
        session.add(follow_up)
        await session.flush()
        session.add(
            WorkflowStep(
                tenant_id=tenant_id,
                workflow_id=follow_up.id,
                channel="email",
                kind="follow_up",
                position=0,
            )
        )
        await session.flush()

        booking = await create_booking(
            session, tenant_id=tenant_id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session, session.begin():
        queued = await _steps(session, booking_id)
        assert {step.payload["trigger"] for step in queued} == {
            WorkflowTrigger.BEFORE_START.value,
            WorkflowTrigger.AFTER_END.value,
        }
        await mark_no_show(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            now=_SLOT + timedelta(days=1),
        )

    async with migrated() as session:
        by_trigger = {step.payload["trigger"]: step for step in await _steps(session, booking_id)}
        booking = await session.get(Booking, booking_id)

    assert by_trigger[WorkflowTrigger.AFTER_END.value].status == "voided", (
        "the no-show kept its follow-up: the guest who never showed up gets 'thanks for meeting'"
    )
    assert booking is not None and booking.status is BookingStatus.NO_SHOW


# --------------------------------------------------------------------------------------
# The write-target rule belongs to the DATABASE, not to the code.
# --------------------------------------------------------------------------------------


async def test_a_connection_cannot_have_two_booking_targets(migrated: Sessionmaker) -> None:
    """ "Which calendar do we write to?" must never be answered by whichever row was read first. The
    partial unique index turns a second write target into an INTEGRITY ERROR."""
    async with migrated() as session, session.begin():
        tenant, _event_type = await _seed(session)
        tenant_id = tenant.id
        host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).one()
        connection = await store_google_connection(
            session,
            tenant_id=tenant_id,
            user_id=host.id,
            credential=GoogleCredential(account_email="h@gmail.com", token_json='{"token": "t"}'),
            fernet=Fernet(derive_fernet_key("test-app-secret")),
        )
        await session.flush()
        connection_id = connection.id
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="primary",
                is_booking_target=True,
            )
        )
        await session.flush()

    async with migrated() as session, session.begin():
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="second-calendar",
                is_booking_target=True,  # a SECOND write target on the same connection
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()

    # A second NON-target calendar on the same connection is fine — the index is partial.
    async with migrated() as session, session.begin():
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="second-calendar",
                is_booking_target=False,
            )
        )
        await session.flush()
