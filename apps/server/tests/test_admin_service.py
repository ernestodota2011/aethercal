"""Offline tests for the in-process admin service layer (F1-11, RF-18).

The admin never talks to the API over HTTP: its handlers call this service layer, which in turn
calls the real ``aethercal.server.services`` functions directly against a session. These tests drive
that layer against an in-memory aiosqlite sessionmaker (the same backend the CLI's ``run_*``
coroutines are tested against), proving:

* single-user context resolution (the one tenant + its host user; by slug; the ambiguous/empty
  failure modes);
* bookings list/cancel/reschedule delegate to the booking service and map its domain errors;
* event-type and schedule CRUD round-trip through their services;
* tenant scoping holds — administering tenant A never sees or mutates tenant B's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleUpdate, TimeRangeSchema
from aethercal.server.admin.service import (
    AdminActionError,
    AdminSetupError,
    EventTypeForm,
    cancel_booking_action,
    create_event_type_action,
    create_schedule_action,
    deactivate_event_type_action,
    delete_schedule_action,
    list_bookings_view,
    list_event_types_view,
    list_schedules_view,
    reschedule_booking_action,
    resolve_admin_context,
    update_event_type_action,
    update_schedule_action,
)
from aethercal.server.db import Base
from aethercal.server.db.models import Booking, Tenant, User
from aethercal.server.services.bookings import BookingParams, create_booking

Sessionmaker = async_sessionmaker[AsyncSession]

_WEEKLY_9_TO_5 = {day: [TimeRangeSchema(start="09:00", end="17:00")] for day in range(5)}
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)  # Monday 00:00 UTC
_SLOT_9 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_SLOT_11 = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
_MAX_ADVANCE = 60 * 60 * 24 * 30


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
    """An in-memory aiosqlite sessionmaker with the full schema (offline admin-service TDD)."""
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


async def _seed_tenant(
    maker: Sessionmaker, *, slug: str = "acme", email: str = "host@example.com"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a tenant + its single host user; return ``(tenant_id, host_user_id)``."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email=email, name="Host", timezone="UTC")
        session.add(user)
        await session.flush()
        return tenant.id, user.id


async def _schedule_id(maker: Sessionmaker, *, tenant_slug: str, name: str = "Weekly") -> uuid.UUID:
    created = await create_schedule_action(
        maker,
        tenant_slug=tenant_slug,
        data=ScheduleCreate(name=name, timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    return created.id


async def _make_event_type(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    schedule_id: uuid.UUID,
    slug: str = "intro",
) -> uuid.UUID:
    created = await create_event_type_action(
        maker,
        tenant_slug=tenant_slug,
        form=EventTypeForm(
            slug=slug,
            title="Intro",
            schedule_id=schedule_id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )
    return created.id


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


# --------------------------------------------------------------------------------------
# Context resolution.
# --------------------------------------------------------------------------------------


async def test_resolve_context_finds_the_single_tenant_and_host(sessionmaker: Sessionmaker) -> None:
    tenant_id, host_id = await _seed_tenant(sessionmaker)
    async with sessionmaker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=None)
    assert ctx.tenant_id == tenant_id
    assert ctx.host_user_id == host_id


async def test_resolve_context_by_slug_picks_the_named_tenant(sessionmaker: Sessionmaker) -> None:
    a_id, _ = await _seed_tenant(sessionmaker, slug="alpha", email="a@example.com")
    await _seed_tenant(sessionmaker, slug="beta", email="b@example.com")
    async with sessionmaker() as session:
        ctx = await resolve_admin_context(session, tenant_slug="alpha")
    assert ctx.tenant_id == a_id


async def test_resolve_context_with_no_tenant_is_a_setup_error(sessionmaker: Sessionmaker) -> None:
    async with sessionmaker() as session:
        with pytest.raises(AdminSetupError):
            await resolve_admin_context(session, tenant_slug=None)


async def test_resolve_context_is_ambiguous_with_many_tenants(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker, slug="alpha", email="a@example.com")
    await _seed_tenant(sessionmaker, slug="beta", email="b@example.com")
    async with sessionmaker() as session:
        with pytest.raises(AdminSetupError):
            await resolve_admin_context(session, tenant_slug=None)


async def test_resolve_context_unknown_slug_is_a_setup_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker, slug="alpha")
    async with sessionmaker() as session:
        with pytest.raises(AdminSetupError):
            await resolve_admin_context(session, tenant_slug="ghost")


# --------------------------------------------------------------------------------------
# Bookings.
# --------------------------------------------------------------------------------------


async def test_list_bookings_returns_the_tenants_bookings(sessionmaker: Sessionmaker) -> None:
    tenant_id, _ = await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    event_type_id = await _make_event_type(
        sessionmaker, tenant_slug="acme", schedule_id=schedule_id
    )
    booking_id = await _book(
        sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT_9
    )

    rows = await list_bookings_view(sessionmaker, tenant_slug=None)
    assert [r.id for r in rows] == [booking_id]
    assert rows[0].status is BookingStatus.CONFIRMED


async def test_cancel_booking_action_cancels(sessionmaker: Sessionmaker) -> None:
    tenant_id, _ = await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    event_type_id = await _make_event_type(
        sessionmaker, tenant_slug="acme", schedule_id=schedule_id
    )
    booking_id = await _book(
        sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT_9
    )

    cancelled = await cancel_booking_action(
        sessionmaker, tenant_slug=None, booking_id=booking_id, now=_BEFORE
    )
    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancelled_at is not None


async def test_cancel_unknown_booking_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    with pytest.raises(AdminActionError):
        await cancel_booking_action(
            sessionmaker, tenant_slug=None, booking_id=uuid.uuid4(), now=_BEFORE
        )


async def test_reschedule_booking_action_moves_to_a_new_slot(sessionmaker: Sessionmaker) -> None:
    tenant_id, _ = await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    event_type_id = await _make_event_type(
        sessionmaker, tenant_slug="acme", schedule_id=schedule_id
    )
    booking_id = await _book(
        sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT_9
    )

    moved = await reschedule_booking_action(
        sessionmaker, tenant_slug=None, booking_id=booking_id, new_start=_SLOT_11, now=_BEFORE
    )
    # SQLite drops tzinfo on round-trip, so normalize to an aware UTC instant before comparing.
    assert moved.start.replace(tzinfo=UTC) == _SLOT_11
    assert moved.rescheduled_from_id == booking_id
    assert moved.status is BookingStatus.CONFIRMED


async def test_reschedule_to_an_off_hours_slot_is_an_action_error(
    sessionmaker: Sessionmaker,
) -> None:
    tenant_id, _ = await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    event_type_id = await _make_event_type(
        sessionmaker, tenant_slug="acme", schedule_id=schedule_id
    )
    booking_id = await _book(
        sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id, start=_SLOT_9
    )

    off_hours = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)
    with pytest.raises(AdminActionError):
        await reschedule_booking_action(
            sessionmaker, tenant_slug=None, booking_id=booking_id, new_start=off_hours, now=_BEFORE
        )
    # The original booking is untouched by the failed reschedule.
    async with sessionmaker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CONFIRMED


# --------------------------------------------------------------------------------------
# Event types.
# --------------------------------------------------------------------------------------


async def test_event_type_create_list_update_deactivate(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    created = await create_event_type_action(
        sessionmaker,
        tenant_slug=None,
        form=EventTypeForm(
            slug="intro",
            title="Intro Call",
            schedule_id=schedule_id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )
    assert created.slug == "intro"

    listed = await list_event_types_view(sessionmaker, tenant_slug=None)
    assert [e.id for e in listed] == [created.id]

    updated = await update_event_type_action(
        sessionmaker,
        tenant_slug=None,
        event_type_id=created.id,
        data=EventTypeUpdate(title="Renamed"),
    )
    assert updated.title == "Renamed"

    assert await deactivate_event_type_action(
        sessionmaker, tenant_slug=None, event_type_id=created.id
    )
    after = await list_event_types_view(sessionmaker, tenant_slug=None)
    assert after[0].active is False


async def test_duplicate_event_type_slug_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    await _make_event_type(sessionmaker, tenant_slug=None, schedule_id=schedule_id, slug="dup")
    with pytest.raises(AdminActionError):
        await create_event_type_action(
            sessionmaker,
            tenant_slug=None,
            form=EventTypeForm(
                slug="dup",
                title="Second",
                schedule_id=schedule_id,
                duration_seconds=1800,
                max_advance_seconds=_MAX_ADVANCE,
            ),
        )


async def test_invalid_event_type_bounds_are_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    schedule_id = await _schedule_id(sessionmaker, tenant_slug="acme")
    with pytest.raises(AdminActionError):
        await create_event_type_action(
            sessionmaker,
            tenant_slug=None,
            form=EventTypeForm(
                slug="bad",
                title="Bad",
                schedule_id=schedule_id,
                duration_seconds=0,  # violates the gt=0 bound
                max_advance_seconds=_MAX_ADVANCE,
            ),
        )


async def test_update_unknown_event_type_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    with pytest.raises(AdminActionError):
        await update_event_type_action(
            sessionmaker,
            tenant_slug=None,
            event_type_id=uuid.uuid4(),
            data=EventTypeUpdate(title="ghost"),
        )


# --------------------------------------------------------------------------------------
# Schedules.
# --------------------------------------------------------------------------------------


async def test_schedule_create_list_update_delete(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    created = await create_schedule_action(
        sessionmaker,
        tenant_slug=None,
        data=ScheduleCreate(name="Weekdays", timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    assert created.name == "Weekdays"

    listed = await list_schedules_view(sessionmaker, tenant_slug=None)
    assert [s.id for s in listed] == [created.id]

    updated = await update_schedule_action(
        sessionmaker,
        tenant_slug=None,
        schedule_id=created.id,
        data=ScheduleUpdate(name="Renamed"),
    )
    assert updated.name == "Renamed"

    await delete_schedule_action(sessionmaker, tenant_slug=None, schedule_id=created.id)
    assert await list_schedules_view(sessionmaker, tenant_slug=None) == []


async def test_duplicate_schedule_name_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    await create_schedule_action(
        sessionmaker,
        tenant_slug=None,
        data=ScheduleCreate(name="Weekly", timezone="UTC", rules={}),
    )
    with pytest.raises(AdminActionError):
        await create_schedule_action(
            sessionmaker,
            tenant_slug=None,
            data=ScheduleCreate(name="Weekly", timezone="UTC", rules={}),
        )


async def test_bad_timezone_schedule_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    with pytest.raises(AdminActionError):
        await create_schedule_action(
            sessionmaker,
            tenant_slug=None,
            data=ScheduleCreate(name="Bad", timezone="Not/AZone", rules={}),
        )


async def test_delete_unknown_schedule_is_an_action_error(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker)
    with pytest.raises(AdminActionError):
        await delete_schedule_action(sessionmaker, tenant_slug=None, schedule_id=uuid.uuid4())


# --------------------------------------------------------------------------------------
# Tenant isolation.
# --------------------------------------------------------------------------------------


async def test_admin_scopes_every_read_to_its_tenant(sessionmaker: Sessionmaker) -> None:
    # Tenant beta has a booking; administering tenant alpha must never see it.
    await _seed_tenant(sessionmaker, slug="alpha", email="a@example.com")
    beta_id, _ = await _seed_tenant(sessionmaker, slug="beta", email="b@example.com")
    beta_schedule = await _schedule_id(sessionmaker, tenant_slug="beta")
    beta_event = await _make_event_type(sessionmaker, tenant_slug="beta", schedule_id=beta_schedule)
    await _book(sessionmaker, tenant_id=beta_id, event_type_id=beta_event, start=_SLOT_9)

    alpha_bookings = await list_bookings_view(sessionmaker, tenant_slug="alpha")
    assert alpha_bookings == []
    alpha_event_types = await list_event_types_view(sessionmaker, tenant_slug="alpha")
    assert alpha_event_types == []


async def test_admin_cannot_cancel_another_tenants_booking(sessionmaker: Sessionmaker) -> None:
    await _seed_tenant(sessionmaker, slug="alpha", email="a@example.com")
    beta_id, _ = await _seed_tenant(sessionmaker, slug="beta", email="b@example.com")
    beta_schedule = await _schedule_id(sessionmaker, tenant_slug="beta")
    beta_event = await _make_event_type(sessionmaker, tenant_slug="beta", schedule_id=beta_schedule)
    beta_booking = await _book(
        sessionmaker, tenant_id=beta_id, event_type_id=beta_event, start=_SLOT_9
    )

    # Alpha's admin asks to cancel Beta's booking id → not found for Alpha (scoping).
    with pytest.raises(AdminActionError):
        await cancel_booking_action(
            sessionmaker, tenant_slug="alpha", booking_id=beta_booking, now=_BEFORE
        )
    # Beta's booking is untouched.
    async with sessionmaker() as session:
        row = await session.get(Booking, beta_booking)
        assert row is not None
        assert row.status is BookingStatus.CONFIRMED
