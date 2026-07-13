"""Offline tests for the admin's hosts, the host selector, and the booking-calendar target (RF-30).

Multi-host has been supported by the MODEL for a while and has never worked from the admin, for one
reason: ``resolve_admin_context`` took the tenant's FIRST user and injected it as the host of every
event type it created, and the form had no host field at all. A business with two hosts therefore
watched every event type it authored land on whichever of them happened to be created first — no
error, no warning. The tests below are about the host that ACTUALLY ends up on the row.

A host field also turns the form into a write surface for a foreign id, so the tenancy of every id
the operator can submit — the host, the schedule's owner, the calendar connection — is asserted here
too.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.schemas.schedules import ScheduleCreate, TimeRangeSchema
from aethercal.server.admin.service import (
    AdminActionError,
    EventTypeForm,
    HostForm,
    create_event_type_action,
    create_host_action,
    create_schedule_action,
    delete_host_action,
    designate_calendar_action,
    list_connections_view,
    list_hosts_view,
    update_host_action,
)
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.db import Base
from aethercal.server.db.models import EventType, ExternalCalendarLink, ExternalConnection, Tenant
from aethercal.server.services.calendars import resolve_calendar_target

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
_NOW = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
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


async def _tenant(maker: Sessionmaker, *, slug: str = "acme") -> uuid.UUID:
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        return tenant.id


def _host(name: str, email: str) -> HostForm:
    return HostForm(name=name, email=email, timezone="UTC")


async def _connection(
    maker: Sessionmaker, *, tenant_id: uuid.UUID, user_id: uuid.UUID, email: str
) -> uuid.UUID:
    """An active Google connection for a host (the OAuth dance is not what is under test here)."""
    async with maker() as session, session.begin():
        row = ExternalConnection(
            tenant_id=tenant_id,
            user_id=user_id,
            provider="google",
            account_email=email,
            encrypted_credentials=b"encrypted",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _weekly(maker: Sessionmaker, *, slug: str = "acme", name: str = "Weekly") -> uuid.UUID:
    created = await create_schedule_action(
        _admin(maker),
        tenant_slug=slug,
        data=ScheduleCreate(name=name, timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    return created.id


# --------------------------------------------------------------------------------------
# The host selector — the defect this requirement is named after.
# --------------------------------------------------------------------------------------


async def test_an_event_type_lands_on_the_host_the_operator_chose(
    sessionmaker: Sessionmaker,
) -> None:
    """==The bug, stated as a test.==

    ``resolve_admin_context`` took the tenant's FIRST user, so with two hosts on the books every
    event type the admin created was quietly assigned to Ana — including the ones the operator
    authored for Bruno. Asserted on the ``host_id`` the ROW carries, because that is the only thing
    that decides whose availability the event type is offered against.
    """
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    bruno = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Bruno", "bruno@example.com")
    )
    assert ana.id != bruno.id
    schedule_id = await _weekly(sessionmaker)

    created = await create_event_type_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        form=EventTypeForm(
            host_id=bruno.id,  # the SECOND host — the one the old code could never reach
            slug="bruno-intro",
            title="Intro with Bruno",
            schedule_id=schedule_id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )

    async with sessionmaker() as session:
        row = await session.get(EventType, created.id)
        assert row is not None
        assert row.host_id == bruno.id


async def test_an_event_type_cannot_be_assigned_to_another_tenants_host(
    sessionmaker: Sessionmaker,
) -> None:
    """The host is a form-submitted foreign id, so it is a cross-tenant write surface until its
    tenancy is checked. It is refused, and nothing is written."""
    await _tenant(sessionmaker, slug="alpha")
    await _tenant(sessionmaker, slug="beta")
    await create_host_action(
        _admin(sessionmaker), tenant_slug="alpha", form=_host("Ana", "ana@example.com")
    )
    intruder = await create_host_action(
        _admin(sessionmaker), tenant_slug="beta", form=_host("Beto", "beto@example.com")
    )
    schedule_id = await _weekly(sessionmaker, slug="alpha")

    with pytest.raises(AdminActionError):
        await create_event_type_action(
            _admin(sessionmaker),
            tenant_slug="alpha",
            form=EventTypeForm(
                host_id=intruder.id,  # beta's host, submitted into alpha's form
                slug="intro",
                title="Intro",
                schedule_id=schedule_id,
                duration_seconds=1800,
                max_advance_seconds=_MAX_ADVANCE,
            ),
        )

    assert await list_hosts_view(_admin(sessionmaker), tenant_slug="alpha") != []


async def test_a_host_may_not_be_given_another_hosts_private_schedule(
    sessionmaker: Sessionmaker,
) -> None:
    """RF-30's other half. A schedule owned by ONE host is not the business's: without the check,
    Ana's event type quietly runs on Bruno's weekly pattern and starts taking bookings at his
    hours."""
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    bruno = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Bruno", "bruno@example.com")
    )
    brunos_own = await create_schedule_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        data=ScheduleCreate(
            name="Bruno's hours", timezone="UTC", rules=_WEEKLY_9_TO_5, user_id=bruno.id
        ),
    )

    with pytest.raises(AdminActionError):
        await create_event_type_action(
            _admin(sessionmaker),
            tenant_slug="acme",
            form=EventTypeForm(
                host_id=ana.id,
                slug="ana-intro",
                title="Intro",
                schedule_id=brunos_own.id,  # Bruno's private pattern
                duration_seconds=1800,
                max_advance_seconds=_MAX_ADVANCE,
            ),
        )


async def test_a_shared_schedule_is_usable_by_every_host(sessionmaker: Sessionmaker) -> None:
    """``user_id IS NULL`` is the business's own pattern — the zero-config path stays open."""
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    shared = await create_schedule_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        data=ScheduleCreate(name="Business hours", timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    assert shared.user_id is None

    created = await create_event_type_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        form=EventTypeForm(
            host_id=ana.id,
            slug="intro",
            title="Intro",
            schedule_id=shared.id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )
    assert created.host_id == ana.id


# --------------------------------------------------------------------------------------
# Host CRUD.
# --------------------------------------------------------------------------------------


async def test_hosts_round_trip_through_the_admin(sessionmaker: Sessionmaker) -> None:
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )

    await update_host_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        host_id=ana.id,
        form=HostForm(name="Ana Ruiz", email="ana@example.com", timezone="Europe/Madrid"),
    )

    hosts = await list_hosts_view(_admin(sessionmaker), tenant_slug="acme")
    assert [(row.name, row.timezone) for row in hosts] == [("Ana Ruiz", "Europe/Madrid")]


async def test_a_duplicate_host_email_is_refused(sessionmaker: Sessionmaker) -> None:
    """``(tenant_id, email)`` is unique — two hosts on one address is one host with a typo."""
    await _tenant(sessionmaker)
    await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )

    with pytest.raises(AdminActionError):
        await create_host_action(
            _admin(sessionmaker), tenant_slug="acme", form=_host("Ana again", "ana@example.com")
        )


async def test_deleting_a_host_who_still_hosts_an_event_type_is_refused(
    sessionmaker: Sessionmaker,
) -> None:
    """==Loud, not a cascade.==

    Both silent outcomes are catastrophes: cascade, and the business's booking page loses an event
    type nobody asked it to remove; orphan, and it keeps taking bookings for a host who no longer
    exists. So the refusal names what is holding the host, and the row survives.
    """
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    schedule_id = await _weekly(sessionmaker)
    await create_event_type_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        form=EventTypeForm(
            host_id=ana.id,
            slug="intro",
            title="Intro",
            schedule_id=schedule_id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )

    with pytest.raises(AdminActionError) as refusal:
        await delete_host_action(_admin(sessionmaker), tenant_slug="acme", host_id=ana.id)
    assert "event type" in refusal.value.message

    assert [row.id for row in await list_hosts_view(_admin(sessionmaker), tenant_slug="acme")] == [
        ana.id
    ]


async def test_a_free_host_can_be_deleted(sessionmaker: Sessionmaker) -> None:
    await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )

    await delete_host_action(_admin(sessionmaker), tenant_slug="acme", host_id=ana.id)

    assert await list_hosts_view(_admin(sessionmaker), tenant_slug="acme") == []


async def test_deleting_an_unknown_host_is_an_error_not_a_silent_success(
    sessionmaker: Sessionmaker,
) -> None:
    await _tenant(sessionmaker)
    with pytest.raises(AdminActionError):
        await delete_host_action(_admin(sessionmaker), tenant_slug="acme", host_id=uuid.uuid4())


async def test_a_tenant_never_sees_another_tenants_hosts(sessionmaker: Sessionmaker) -> None:
    await _tenant(sessionmaker, slug="alpha")
    await _tenant(sessionmaker, slug="beta")
    await create_host_action(
        _admin(sessionmaker), tenant_slug="beta", form=_host("Beto", "b@example.com")
    )

    assert await list_hosts_view(_admin(sessionmaker), tenant_slug="alpha") == []


async def test_a_host_of_another_tenant_cannot_be_edited(sessionmaker: Sessionmaker) -> None:
    await _tenant(sessionmaker, slug="alpha")
    await _tenant(sessionmaker, slug="beta")
    beto = await create_host_action(
        _admin(sessionmaker), tenant_slug="beta", form=_host("Beto", "b@example.com")
    )

    with pytest.raises(AdminActionError):
        await update_host_action(
            _admin(sessionmaker),
            tenant_slug="alpha",
            host_id=beto.id,
            form=HostForm(name="Hijacked", email="b@example.com", timezone="UTC"),
        )


# --------------------------------------------------------------------------------------
# The booking-calendar target (the admin's `--calendar-id`).
# --------------------------------------------------------------------------------------


async def test_designating_a_calendar_makes_it_the_bookings_write_target(
    sessionmaker: Sessionmaker,
) -> None:
    """The admin's equivalent of ``--calendar-id``: send a host's bookings to a DEDICATED calendar
    instead of the connected account's ``primary``.

    Asserted through ``resolve_calendar_target`` — the function the booking path itself calls — and
    not on the link row, because a row existing is not the same thing as the booking landing there.
    """
    tenant_id = await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    connection_id = await _connection(
        sessionmaker, tenant_id=tenant_id, user_id=ana.id, email="ana@gmail.com"
    )

    await designate_calendar_action(
        _admin(sessionmaker),
        tenant_slug="acme",
        connection_id=connection_id,
        calendar_id="bookings@group.calendar.google.com",
    )

    async with sessionmaker() as session:
        target = await resolve_calendar_target(session, tenant_id=tenant_id, user_id=ana.id)
        assert target is not None
        assert target.calendar_id == "bookings@group.calendar.google.com"
        assert target.connection.id == connection_id


async def test_a_connection_of_another_tenant_cannot_be_re_pointed(
    sessionmaker: Sessionmaker,
) -> None:
    """The connection id comes off a form too. Re-pointing another business's connection would write
    this business's meetings into their calendar."""
    beta_id = await _tenant(sessionmaker, slug="beta")
    await _tenant(sessionmaker, slug="alpha")
    beto = await create_host_action(
        _admin(sessionmaker), tenant_slug="beta", form=_host("Beto", "b@example.com")
    )
    beta_connection = await _connection(
        sessionmaker, tenant_id=beta_id, user_id=beto.id, email="beto@gmail.com"
    )

    with pytest.raises(AdminActionError):
        await designate_calendar_action(
            _admin(sessionmaker),
            tenant_slug="alpha",
            connection_id=beta_connection,
            calendar_id="hijack@group.calendar.google.com",
        )

    async with sessionmaker() as session:
        assert list((await session.scalars(select(ExternalCalendarLink))).all()) == []


async def test_the_connections_of_a_host_are_listed_for_the_operator(
    sessionmaker: Sessionmaker,
) -> None:
    """The operator cannot designate a calendar on a connection they cannot see — and a host's
    SECOND connection is listed too: the ``.first()`` that used to drop it is this same defect one
    layer down."""
    tenant_id = await _tenant(sessionmaker)
    ana = await create_host_action(
        _admin(sessionmaker), tenant_slug="acme", form=_host("Ana", "ana@example.com")
    )
    first = await _connection(
        sessionmaker, tenant_id=tenant_id, user_id=ana.id, email="ana@gmail.com"
    )
    second = await _connection(
        sessionmaker, tenant_id=tenant_id, user_id=ana.id, email="ana@work.com"
    )

    listed = await list_connections_view(_admin(sessionmaker), tenant_slug="acme", host_id=ana.id)

    assert {row.id for row in listed} == {first, second}
