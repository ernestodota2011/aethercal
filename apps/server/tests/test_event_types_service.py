"""Async service tests for EventType CRUD against an in-memory session (RF-14).

Offline service-layer TDD: every operation is scoped by ``tenant_id``, slug is unique per tenant,
referenced host/schedule must belong to the same tenant, and the ``to_core_event_type`` bridge maps
integer seconds onto the pure ``aethercal.core`` value objects the slots engine (F1-04) consumes.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import EventType as CoreEventType
from aethercal.schemas.event_types import EventTypeCreate, EventTypeUpdate
from aethercal.server.db.models import Schedule, Tenant, User
from aethercal.server.services.event_types import (
    DuplicateSlugError,
    EventTypeError,
    InvalidReferenceError,
    create_event_type,
    deactivate_event_type,
    get_event_type,
    list_event_types,
    to_core_event_type,
    update_event_type,
)

TenantFactory = Callable[..., Awaitable[Tenant]]


async def _host_id(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).first()
    assert host is not None
    return host.id


async def _schedule_id(
    session: AsyncSession, tenant: Tenant, *, name: str = "Default"
) -> uuid.UUID:
    schedule = Schedule(tenant_id=tenant.id, name=name, timezone="UTC", rules={})
    session.add(schedule)
    await session.flush()
    return schedule.id


async def _make_payload(
    session: AsyncSession, tenant: Tenant, *, slug: str = "intro-call", **overrides: object
) -> EventTypeCreate:
    data: dict[str, object] = {
        "host_id": await _host_id(session, tenant),
        "schedule_id": await _schedule_id(session, tenant, name=f"sched-{uuid.uuid4().hex[:12]}"),
        "slug": slug,
        "title": "Intro Call",
        "duration_seconds": 1800,
        "max_advance_seconds": 60 * 60 * 24 * 30,
    }
    data.update(overrides)
    return EventTypeCreate(**data)


def test_duplicate_slug_error_is_an_event_type_error() -> None:
    assert issubclass(DuplicateSlugError, EventTypeError)
    assert issubclass(InvalidReferenceError, EventTypeError)


async def test_create_then_get_roundtrip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    payload = await _make_payload(sqlite_session, tenant)

    created = await create_event_type(sqlite_session, tenant_id=tenant.id, data=payload)
    assert created.id is not None
    assert created.tenant_id == tenant.id
    assert created.slug == "intro-call"
    assert created.active is True

    fetched = await get_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=created.id)
    assert fetched is not None
    assert fetched.id == created.id


async def test_list_returns_only_tenant_rows(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session, slug="a")
    other = await tenant_factory(sqlite_session, slug="b")
    await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="one"),
    )
    await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="two"),
    )
    await create_event_type(
        sqlite_session,
        tenant_id=other.id,
        data=await _make_payload(sqlite_session, other, slug="x"),
    )

    rows = await list_event_types(sqlite_session, tenant_id=tenant.id)
    assert {r.slug for r in rows} == {"one", "two"}


async def test_update_applies_only_provided_fields(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    created = await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=await _make_payload(sqlite_session, tenant)
    )

    updated = await update_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=created.id,
        data=EventTypeUpdate(title="Renamed", duration_seconds=3600),
    )
    assert updated is not None
    assert updated.title == "Renamed"
    assert updated.duration_seconds == 3600
    # Untouched fields keep their original values.
    assert updated.slug == "intro-call"
    assert updated.max_advance_seconds == 60 * 60 * 24 * 30


async def test_update_unknown_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    result = await update_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=uuid.uuid4(),
        data=EventTypeUpdate(title="nope"),
    )
    assert result is None


async def test_deactivate_soft_deletes(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    created = await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=await _make_payload(sqlite_session, tenant)
    )

    ok = await deactivate_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=created.id)
    assert ok is True

    # Soft delete: the row still exists but is inactive.
    fetched = await get_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=created.id)
    assert fetched is not None
    assert fetched.active is False


async def test_deactivate_unknown_returns_false(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    ok = await deactivate_event_type(
        sqlite_session, tenant_id=tenant.id, event_type_id=uuid.uuid4()
    )
    assert ok is False


async def test_duplicate_slug_rejected_and_first_survives(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    first = await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="dup"),
    )

    with pytest.raises(DuplicateSlugError):
        await create_event_type(
            sqlite_session,
            tenant_id=tenant.id,
            data=await _make_payload(sqlite_session, tenant, slug="dup"),
        )

    # The failed insert must not have poisoned the transaction: the first row is still there.
    still = await get_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=first.id)
    assert still is not None


async def test_same_slug_allowed_across_tenants(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session, slug="a")
    other = await tenant_factory(sqlite_session, slug="b")
    await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="shared"),
    )
    # Unique is per-tenant, so the other tenant may reuse the slug.
    row = await create_event_type(
        sqlite_session,
        tenant_id=other.id,
        data=await _make_payload(sqlite_session, other, slug="shared"),
    )
    assert row.slug == "shared"


async def test_cross_tenant_isolation(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    intruder = await tenant_factory(sqlite_session, slug="intruder")
    et = await create_event_type(
        sqlite_session, tenant_id=owner.id, data=await _make_payload(sqlite_session, owner)
    )

    # The intruder tenant can neither see nor mutate the owner's row.
    assert await get_event_type(sqlite_session, tenant_id=intruder.id, event_type_id=et.id) is None
    assert await list_event_types(sqlite_session, tenant_id=intruder.id) == []
    assert (
        await update_event_type(
            sqlite_session,
            tenant_id=intruder.id,
            event_type_id=et.id,
            data=EventTypeUpdate(title="hijacked"),
        )
        is None
    )
    assert (
        await deactivate_event_type(sqlite_session, tenant_id=intruder.id, event_type_id=et.id)
        is False
    )

    # The owner's row is untouched.
    owned = await get_event_type(sqlite_session, tenant_id=owner.id, event_type_id=et.id)
    assert owned is not None
    assert owned.title == "Intro Call"
    assert owned.active is True


async def test_create_rejects_unknown_host(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    payload = await _make_payload(sqlite_session, tenant)
    bad = payload.model_copy(update={"host_id": uuid.uuid4()})

    with pytest.raises(InvalidReferenceError):
        await create_event_type(sqlite_session, tenant_id=tenant.id, data=bad)


async def test_create_rejects_schedule_from_another_tenant(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session, slug="a")
    other = await tenant_factory(sqlite_session, slug="b")
    foreign_schedule = await _schedule_id(sqlite_session, other, name="foreign")

    payload = await _make_payload(sqlite_session, tenant)
    bad = payload.model_copy(update={"schedule_id": foreign_schedule})

    with pytest.raises(InvalidReferenceError):
        await create_event_type(sqlite_session, tenant_id=tenant.id, data=bad)


async def test_update_rejects_bad_reference(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    created = await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=await _make_payload(sqlite_session, tenant)
    )

    with pytest.raises(InvalidReferenceError):
        await update_event_type(
            sqlite_session,
            tenant_id=tenant.id,
            event_type_id=created.id,
            data=EventTypeUpdate(schedule_id=uuid.uuid4()),
        )


async def test_update_to_duplicate_slug_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="first"),
    )
    second = await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="second"),
    )

    with pytest.raises(DuplicateSlugError):
        await update_event_type(
            sqlite_session,
            tenant_id=tenant.id,
            event_type_id=second.id,
            data=EventTypeUpdate(slug="first"),
        )


async def test_to_core_event_type_maps_seconds_to_timedeltas(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(
            sqlite_session,
            tenant,
            duration_seconds=1800,
            buffer_before_seconds=300,
            buffer_after_seconds=600,
            min_notice_seconds=3600,
            max_advance_seconds=60 * 60 * 24 * 30,
            increment_seconds=900,
        ),
    )

    core = to_core_event_type(row)
    assert isinstance(core, CoreEventType)
    assert core.duration == timedelta(minutes=30)
    assert core.buffer.before == timedelta(minutes=5)
    assert core.buffer.after == timedelta(minutes=10)
    assert core.min_notice == timedelta(hours=1)
    assert core.max_advance == timedelta(days=30)
    assert core.increment == timedelta(minutes=15)


async def test_to_core_event_type_maps_absent_increment_to_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_event_type(
        sqlite_session, tenant_id=tenant.id, data=await _make_payload(sqlite_session, tenant)
    )
    core = to_core_event_type(row)
    assert core.increment is None
