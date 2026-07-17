"""A schedule belongs to one host, or to the whole business — never to two by accident (RF-30).

``schedules`` was only tenant-scoped, so nothing stopped an event type hosted by Ana from pointing
at a weekly pattern that Bruno owns and edits. Nothing would raise, nothing would look wrong, and
the first symptom would be Ana taking bookings at Bruno's hours.

The ownership axis is ``schedules.user_id``:

* ``NULL`` → a SHARED, business-wide pattern. Any host may use it. This is the single-host business,
  and the default for everyone who never thinks about multi-host at all.
* set → the pattern belongs to that host, and binding another host's event type to it is a hard
  error, not a silent success.

The guard lives in the services (event types and schedules), because that is where both sides of the
relationship are known — and it is enforced on UPDATE as well as CREATE, from both directions: you
cannot point an event type at another host's schedule, and you cannot re-assign a schedule out from
under the event types already using it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.event_types import EventTypeCreate, EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleUpdate, TimeRangeSchema
from aethercal.server.db.models import Tenant, User
from aethercal.server.services.event_types import (
    InvalidReferenceError,
    create_event_type,
    update_event_type,
)
from aethercal.server.services.schedules import (
    ScheduleOwnershipError,
    create_schedule,
    list_schedules,
    update_schedule,
)

WORKDAY = [TimeRangeSchema(start="09:00", end="17:00")]


async def _two_hosts(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, User, User]:
    tenant = await tenant_factory(session, email="ana@studio.test")
    ana = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    bruno = User(tenant_id=tenant.id, email="bruno@studio.test", name="Bruno", timezone="UTC")
    session.add(bruno)
    await session.flush()
    return tenant, ana, bruno


def _event_type(host: User, schedule_id: uuid.UUID, *, slug: str = "intro") -> EventTypeCreate:
    return EventTypeCreate(
        host_id=host.id,
        schedule_id=schedule_id,
        slug=slug,
        title="Intro call",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )


# --------------------------------------------------------------------------------------
# Ownership on the schedule itself.
# --------------------------------------------------------------------------------------


async def test_a_schedule_defaults_to_shared(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, _ana, _bruno = await _two_hosts(sqlite_session, tenant_factory)

    schedule = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Business hours", timezone="UTC", rules={0: WORKDAY}),
    )

    # The single-host business never has to think about ownership: NULL = the whole business.
    assert schedule.user_id is None


async def test_a_schedule_can_belong_to_one_host(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, ana, _bruno = await _two_hosts(sqlite_session, tenant_factory)

    schedule = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Ana's hours", timezone="UTC", rules={0: WORKDAY}, user_id=ana.id),
    )

    assert schedule.user_id == ana.id


async def test_a_schedule_cannot_be_owned_by_someone_from_another_tenant(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, _ana, _bruno = await _two_hosts(sqlite_session, tenant_factory)
    other = await tenant_factory(sqlite_session, slug="other", email="x@other.test")
    stranger = (await sqlite_session.scalars(select(User).where(User.tenant_id == other.id))).one()

    with pytest.raises(ScheduleOwnershipError):
        await create_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            data=ScheduleCreate(
                name="Leaky", timezone="UTC", rules={0: WORKDAY}, user_id=stranger.id
            ),
        )


async def test_schedules_can_be_listed_for_one_host(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """What a host selector needs: the host's own patterns PLUS the shared ones — never another
    host's private pattern."""
    tenant, ana, bruno = await _two_hosts(sqlite_session, tenant_factory)
    for name, owner in (("Shared", None), ("Ana", ana.id), ("Bruno", bruno.id)):
        await create_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            data=ScheduleCreate(name=name, timezone="UTC", rules={0: WORKDAY}, user_id=owner),
        )

    usable = await list_schedules(sqlite_session, tenant_id=tenant.id, user_id=ana.id)

    assert [row.name for row in usable] == ["Ana", "Shared"]
    # And the unfiltered list is unchanged for every existing caller (the admin, the API).
    assert len(await list_schedules(sqlite_session, tenant_id=tenant.id)) == 3


# --------------------------------------------------------------------------------------
# The guard: an event type may not borrow another host's schedule.
# --------------------------------------------------------------------------------------


async def test_an_event_type_cannot_use_another_hosts_schedule(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, ana, bruno = await _two_hosts(sqlite_session, tenant_factory)
    bruno_hours = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(
            name="Bruno's hours", timezone="UTC", rules={0: WORKDAY}, user_id=bruno.id
        ),
    )

    # Ana's event type pointing at Bruno's private pattern: the accident RF-30 exists to stop.
    with pytest.raises(InvalidReferenceError):
        await create_event_type(
            sqlite_session, tenant_id=tenant.id, data=_event_type(ana, bruno_hours.id)
        )


async def test_an_event_type_may_use_a_shared_schedule(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, ana, bruno = await _two_hosts(sqlite_session, tenant_factory)
    shared = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Business hours", timezone="UTC", rules={0: WORKDAY}),
    )

    for index, host in enumerate((ana, bruno)):
        row = await create_event_type(
            sqlite_session,
            tenant_id=tenant.id,
            data=_event_type(host, shared.id, slug=f"intro-{index}"),
        )
        assert row.schedule_id == shared.id  # a shared pattern is exactly that: shared


async def test_reassigning_the_host_re_checks_the_schedule(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The update path is the one that rots: the event type was valid when created, and moving it to
    another host would quietly leave it running on a schedule that host does not own."""
    tenant, ana, bruno = await _two_hosts(sqlite_session, tenant_factory)
    ana_hours = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Ana's hours", timezone="UTC", rules={0: WORKDAY}, user_id=ana.id),
    )
    event_type = await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=_event_type(ana, ana_hours.id)
    )

    with pytest.raises(InvalidReferenceError):
        await update_event_type(
            sqlite_session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            data=EventTypeUpdate(host_id=bruno.id),
        )


async def test_a_schedule_cannot_be_claimed_out_from_under_its_event_types(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The other direction of the same rot: a shared schedule that Ana and Bruno both use cannot be
    quietly re-assigned to Ana — that would leave Bruno's event type on a pattern he does not own,
    the exact state RF-30 forbids."""
    tenant, ana, bruno = await _two_hosts(sqlite_session, tenant_factory)
    shared = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Business hours", timezone="UTC", rules={0: WORKDAY}),
    )
    await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=_event_type(ana, shared.id, slug="ana-intro")
    )
    await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=_event_type(bruno, shared.id, slug="bruno-intro")
    )

    with pytest.raises(ScheduleOwnershipError):
        await update_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            schedule_id=shared.id,
            data=ScheduleUpdate(user_id=ana.id),
        )


async def test_a_schedule_used_by_a_single_host_can_be_claimed_by_that_host(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, ana, _bruno = await _two_hosts(sqlite_session, tenant_factory)
    shared = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Business hours", timezone="UTC", rules={0: WORKDAY}),
    )
    await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=_event_type(ana, shared.id, slug="ana-intro")
    )

    claimed = await update_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=shared.id,
        data=ScheduleUpdate(user_id=ana.id),
    )

    assert claimed.user_id == ana.id
