"""Async service tests for Schedules + Date Overrides (RF-15) against an in-memory session.

Offline TDD on ``sqlite_session`` + ``tenant_factory``: CRUD, per-tenant name uniqueness, core
validation surfaced as ``ScheduleValidationError`` (bad IANA tz, overlapping ranges), per-date
override uniqueness, empty-ranges = closed day, cross-tenant isolation, and the lossless bridge
back into the pure ``aethercal.core`` objects that the F1-04 slots engine consumes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import DateOverride as CoreDateOverride
from aethercal.core.model import LocalTimeRange, Weekday
from aethercal.core.model import Schedule as CoreSchedule
from aethercal.schemas.schedules import (
    DateOverrideCreate,
    ScheduleCreate,
    ScheduleUpdate,
    TimeRangeSchema,
)
from aethercal.server.db.models import Tenant
from aethercal.server.services.schedules import (
    DateOverrideNotFoundError,
    DuplicateDateOverrideError,
    DuplicateScheduleNameError,
    ScheduleNotFoundError,
    ScheduleValidationError,
    add_date_override,
    create_schedule,
    delete_date_override,
    delete_schedule,
    get_schedule,
    list_date_overrides,
    list_schedules,
    to_core_overrides,
    to_core_schedule,
    update_schedule,
)

TenantFactory = Callable[..., Awaitable[Tenant]]


def _tr(start: str, end: str) -> TimeRangeSchema:
    return TimeRangeSchema(start=start, end=end)


# --------------------------------------------------------------------------------------
# Schedule CRUD
# --------------------------------------------------------------------------------------
async def test_create_then_get_roundtrip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Main", timezone="UTC", rules={0: [_tr("09:00", "17:00")]}),
    )
    fetched = await get_schedule(sqlite_session, tenant_id=tenant.id, schedule_id=row.id)
    assert fetched.id == row.id
    assert fetched.name == "Main"
    assert fetched.timezone == "UTC"


async def test_list_returns_tenant_schedules(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="A", timezone="UTC")
    )
    await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="B", timezone="UTC")
    )
    rows = await list_schedules(sqlite_session, tenant_id=tenant.id)
    assert [r.name for r in rows] == ["A", "B"]


async def test_duplicate_name_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="Main", timezone="UTC")
    )
    with pytest.raises(DuplicateScheduleNameError):
        await create_schedule(
            sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="Main", timezone="UTC")
        )


async def test_name_unique_per_tenant_not_globally(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    a = await tenant_factory(sqlite_session, slug="a")
    b = await tenant_factory(sqlite_session, slug="b")
    await create_schedule(
        sqlite_session, tenant_id=a.id, data=ScheduleCreate(name="Main", timezone="UTC")
    )
    # Same name under a different tenant is allowed.
    row = await create_schedule(
        sqlite_session, tenant_id=b.id, data=ScheduleCreate(name="Main", timezone="UTC")
    )
    assert row.tenant_id == b.id


async def test_invalid_timezone_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    with pytest.raises(ScheduleValidationError):
        await create_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            data=ScheduleCreate(name="X", timezone="Mars/Phobos"),
        )


async def test_overlapping_ranges_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    with pytest.raises(ScheduleValidationError):
        await create_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            data=ScheduleCreate(
                name="X",
                timezone="UTC",
                rules={0: [_tr("09:00", "12:00"), _tr("11:00", "13:00")]},
            ),
        )


async def test_update_name_and_rules(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="Old", timezone="UTC")
    )
    updated = await update_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=row.id,
        data=ScheduleUpdate(name="New", rules={2: [_tr("10:00", "14:00")]}),
    )
    assert updated.name == "New"
    core = to_core_schedule(updated)
    assert core.ranges_for(Weekday.WEDNESDAY) == (
        LocalTimeRange(start=time(10, 0), end=time(14, 0)),
    )


async def test_update_rejects_duplicate_name(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="Taken", timezone="UTC")
    )
    row = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="Free", timezone="UTC")
    )
    with pytest.raises(DuplicateScheduleNameError):
        await update_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            schedule_id=row.id,
            data=ScheduleUpdate(name="Taken"),
        )


async def test_update_invalid_timezone_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    with pytest.raises(ScheduleValidationError):
        await update_schedule(
            sqlite_session,
            tenant_id=tenant.id,
            schedule_id=row.id,
            data=ScheduleUpdate(timezone="Nowhere/Nope"),
        )


async def test_delete_schedule(sqlite_session: AsyncSession, tenant_factory: TenantFactory) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    await delete_schedule(sqlite_session, tenant_id=tenant.id, schedule_id=row.id)
    with pytest.raises(ScheduleNotFoundError):
        await get_schedule(sqlite_session, tenant_id=tenant.id, schedule_id=row.id)


# --------------------------------------------------------------------------------------
# Cross-tenant isolation
# --------------------------------------------------------------------------------------
async def test_get_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    a = await tenant_factory(sqlite_session, slug="a")
    b = await tenant_factory(sqlite_session, slug="b")
    row = await create_schedule(
        sqlite_session, tenant_id=a.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    with pytest.raises(ScheduleNotFoundError):
        await get_schedule(sqlite_session, tenant_id=b.id, schedule_id=row.id)


async def test_delete_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    a = await tenant_factory(sqlite_session, slug="a")
    b = await tenant_factory(sqlite_session, slug="b")
    row = await create_schedule(
        sqlite_session, tenant_id=a.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    with pytest.raises(ScheduleNotFoundError):
        await delete_schedule(sqlite_session, tenant_id=b.id, schedule_id=row.id)
    # Still there for its real owner.
    assert (await get_schedule(sqlite_session, tenant_id=a.id, schedule_id=row.id)).id == row.id


# --------------------------------------------------------------------------------------
# Date overrides
# --------------------------------------------------------------------------------------
async def test_add_and_list_date_override(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    sched = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    ov = await add_date_override(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 12, 24), ranges=[_tr("09:00", "13:00")]),
    )
    rows = await list_date_overrides(sqlite_session, tenant_id=tenant.id, schedule_id=sched.id)
    assert [r.id for r in rows] == [ov.id]


async def test_date_override_unique_per_date(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    sched = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    await add_date_override(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 12, 25)),
    )
    with pytest.raises(DuplicateDateOverrideError):
        await add_date_override(
            sqlite_session,
            tenant_id=tenant.id,
            schedule_id=sched.id,
            data=DateOverrideCreate(date=date(2026, 12, 25)),
        )


async def test_empty_ranges_is_closed_day(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    sched = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    ov = await add_date_override(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 12, 25), ranges=[]),
    )
    cores = to_core_overrides([ov])
    assert cores[0].date == date(2026, 12, 25)
    assert cores[0].ranges == ()


async def test_override_overlap_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    sched = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    with pytest.raises(ScheduleValidationError):
        await add_date_override(
            sqlite_session,
            tenant_id=tenant.id,
            schedule_id=sched.id,
            data=DateOverrideCreate(
                date=date(2026, 12, 24), ranges=[_tr("09:00", "12:00"), _tr("11:00", "13:00")]
            ),
        )


async def test_add_override_to_foreign_schedule_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    a = await tenant_factory(sqlite_session, slug="a")
    b = await tenant_factory(sqlite_session, slug="b")
    sched = await create_schedule(
        sqlite_session, tenant_id=a.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    with pytest.raises(ScheduleNotFoundError):
        await add_date_override(
            sqlite_session,
            tenant_id=b.id,
            schedule_id=sched.id,
            data=DateOverrideCreate(date=date(2026, 1, 1)),
        )


async def test_delete_date_override_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    a = await tenant_factory(sqlite_session, slug="a")
    b = await tenant_factory(sqlite_session, slug="b")
    sched = await create_schedule(
        sqlite_session, tenant_id=a.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    ov = await add_date_override(
        sqlite_session,
        tenant_id=a.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 1, 1)),
    )
    with pytest.raises(DateOverrideNotFoundError):
        await delete_date_override(sqlite_session, tenant_id=b.id, override_id=ov.id)
    # Owner can delete it.
    await delete_date_override(sqlite_session, tenant_id=a.id, override_id=ov.id)
    assert await list_date_overrides(sqlite_session, tenant_id=a.id, schedule_id=sched.id) == []


# --------------------------------------------------------------------------------------
# Bridges into aethercal.core (consumed by F1-04 slots) — lossless round-trip
# --------------------------------------------------------------------------------------
async def test_to_core_schedule_equals_intended_core(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(
            name="Weekdays",
            timezone="America/New_York",
            rules={
                0: [_tr("09:00", "12:00"), _tr("13:00", "17:00")],
                4: [_tr("09:00", "15:00")],
            },
        ),
    )
    core = to_core_schedule(row)
    expected = CoreSchedule(
        timezone="America/New_York",
        by_weekday={
            Weekday.MONDAY: (
                LocalTimeRange(start=time(9, 0), end=time(12, 0)),
                LocalTimeRange(start=time(13, 0), end=time(17, 0)),
            ),
            Weekday.FRIDAY: (LocalTimeRange(start=time(9, 0), end=time(15, 0)),),
        },
    )
    assert core == expected


async def test_empty_weekday_rule_is_dropped_as_closed(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    row = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="X", timezone="UTC", rules={0: [_tr("09:00", "17:00")], 5: []}),
    )
    core = to_core_schedule(row)
    # Weekday 5 (Saturday) had an empty list → treated as closed → absent from the core mapping.
    assert set(core.by_weekday) == {Weekday.MONDAY}


async def test_to_core_overrides_maps_all_rows(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    sched = await create_schedule(
        sqlite_session, tenant_id=tenant.id, data=ScheduleCreate(name="X", timezone="UTC")
    )
    await add_date_override(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 12, 25)),
    )
    await add_date_override(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=sched.id,
        data=DateOverrideCreate(date=date(2026, 12, 31), ranges=[_tr("09:00", "12:00")]),
    )
    rows = await list_date_overrides(sqlite_session, tenant_id=tenant.id, schedule_id=sched.id)
    cores = to_core_overrides(rows)
    assert cores == [
        CoreDateOverride(date=date(2026, 12, 25), ranges=()),
        CoreDateOverride(
            date=date(2026, 12, 31), ranges=(LocalTimeRange(start=time(9, 0), end=time(12, 0)),)
        ),
    ]
