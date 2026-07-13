"""Designating a booking calendar is a CHOICE, and two of them cannot both win (PostgreSQL only).

``db``-marked, and for the same reason as ``test_booking_concurrency.py``: the guarantee lives in
server-side concurrency control, which SQLite cannot exercise (it serialises writers anyway).

The partial unique index only guarantees ONE booking target per CONNECTION. A host may have several
connected accounts, so two designations that interleave — two admin tabs, two API calls — can each
read "nobody else holds the target", each promote their own, and leave the host with TWO write
targets on two different connections. The database says nothing: the rows are on different
connections, so the index is satisfied.

The damage is not theoretical. With two targets, ``resolve_calendar_target`` refuses (correctly, it
will not guess) and EVERY booking of that host dead-letters — while the operator, who did nothing
but click twice, has no idea why. So the choice is serialised on the HOST: the write path locks the
host's ``users`` row before it retires the old target and promotes the new one, and the loser of the
race re-reads the committed state and demotes what the winner just wrote.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.guc import tenant_scope
from aethercal.server.db.models import ExternalCalendarLink, ExternalConnection, Tenant, User
from aethercal.server.services.calendars import (
    GoogleCredential,
    link_booking_calendar,
    resolve_calendar_target,
    store_google_connection,
)

pytestmark = pytest.mark.db

FERNET = Fernet(derive_fernet_key("test-app-secret"))


async def _seed_two_connections(
    owner_maker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """One host with TWO connected Google accounts. Returns (tenant, host, connection_a, b).

    ==On the OWNER engine== — arrangement, not behaviour. The RACE below is the behaviour, and it
    runs on the app engine under RLS, with the business bound the way the product binds it.
    """
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Studio")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@studio.test", name="Host", timezone="UTC")
        session.add(host)
        await session.flush()
        first = await store_google_connection(
            session,
            tenant_id=tenant.id,
            user_id=host.id,
            credential=GoogleCredential(account_email="work@agency.test", token_json='{"t": "a"}'),
            fernet=FERNET,
        )
        second = await store_google_connection(
            session,
            tenant_id=tenant.id,
            user_id=host.id,
            credential=GoogleCredential(account_email="ops@agency.test", token_json='{"t": "b"}'),
            fernet=FERNET,
        )
        return tenant.id, host.id, first.id, second.id


async def _designate(
    app: FastAPI,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    calendar_id: str,
    barrier: asyncio.Barrier,
) -> None:
    """One designation, in its OWN session and transaction — a second admin tab.

    The barrier makes the race DETERMINISTIC instead of a matter of scheduling luck: both
    transactions are open, and have already read, before either is allowed to designate. Without it
    the interleaving that produces two targets happens only sometimes, and a test that only
    sometimes reproduces the bug is not a proof of anything.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    with tenant_scope(tenant_id):
        async with sessionmaker() as session, session.begin():
            connection = await session.get(ExternalConnection, connection_id)
            assert connection is not None
            await barrier.wait()
            await link_booking_calendar(session, connection=connection, calendar_id=calendar_id)


async def test_two_concurrent_designations_leave_exactly_one_booking_target(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    tenant_id, host_id, first, second = await _seed_two_connections(owner_maker)

    # Two designations, fully concurrent, on DIFFERENT connections of the SAME host. The partial
    # unique index cannot catch this: the rows live on different connections.
    barrier = asyncio.Barrier(2)
    await asyncio.gather(
        _designate(
            app,
            tenant_id=tenant_id,
            connection_id=first,
            calendar_id="work@cal",
            barrier=barrier,
        ),
        _designate(
            app,
            tenant_id=tenant_id,
            connection_id=second,
            calendar_id="ops@cal",
            barrier=barrier,
        ),
    )

    async with owner_maker() as session:
        targets = (
            await session.scalars(
                select(ExternalCalendarLink)
                .join(
                    ExternalConnection,
                    ExternalCalendarLink.connection_id == ExternalConnection.id,
                )
                .where(
                    ExternalConnection.user_id == host_id,
                    ExternalCalendarLink.is_booking_target.is_(True),
                )
            )
        ).all()
        assert len(targets) == 1, "a host must never end up with two write targets"

        # And the resolver agrees: the host's bookings have exactly one place to go, so nothing
        # dead-letters on an ambiguity the operator never intended.
        resolved = await resolve_calendar_target(session, tenant_id=tenant_id, user_id=host_id)
        assert resolved is not None
        assert resolved.calendar_id == targets[0].external_calendar_id


async def test_a_race_between_two_calendars_of_one_connection_also_settles(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """The same race within a single connection, where the partial index DOES apply: it must resolve
    to one target rather than raising an IntegrityError at whoever clicked second."""
    tenant_id, host_id, first, _second = await _seed_two_connections(owner_maker)

    barrier = asyncio.Barrier(2)
    await asyncio.gather(
        _designate(
            app,
            tenant_id=tenant_id,
            connection_id=first,
            calendar_id="one@cal",
            barrier=barrier,
        ),
        _designate(
            app,
            tenant_id=tenant_id,
            connection_id=first,
            calendar_id="two@cal",
            barrier=barrier,
        ),
    )

    async with owner_maker() as session:
        targets = (
            await session.scalars(
                select(ExternalCalendarLink)
                .join(
                    ExternalConnection,
                    ExternalCalendarLink.connection_id == ExternalConnection.id,
                )
                .where(
                    ExternalConnection.user_id == host_id,
                    ExternalCalendarLink.is_booking_target.is_(True),
                )
            )
        ).all()
        assert len(targets) == 1
