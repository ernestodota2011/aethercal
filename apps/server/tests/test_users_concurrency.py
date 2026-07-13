"""Two hosts, one address, one race — and only the DATABASE can settle it (PostgreSQL only).

``db``-marked, and for the same reason as ``test_booking_concurrency.py``: the guarantee lives in
server-side concurrency control, which SQLite cannot exercise (it serialises writers anyway).

``services/users.py`` refuses a second host on the same address case-insensitively — and that guard
is a **check-then-act**. It reads, finds nobody, and then writes. Two creates that interleave — two
admin tabs, an admin and a ``create-tenant``, a retried request — can each read "nobody has that
address", each write, and leave the business with ``Ana@example.com`` AND ``ana@example.com``: two
hosts, one person. From there the selector offers both, an event type lands on whichever was
clicked, and mail goes to whichever row is read first. The guard cannot fix this. Nothing in the
application layer can: the window between the read and the write is where the second writer lives.

So the invariant belongs to the database — a functional unique index on
``(tenant_id, lower(email))``
(migration 0006) — and the service's job is reduced to what it can actually do: make the refusal
READABLE. The loser of the race must get the same domain error the guard gives, never a raw
``IntegrityError`` traceback.

==The barrier is what makes this a proof.== Without it the two coroutines interleave by luck: one
can finish and COMMIT before the other has even read, and then the ordinary guard catches the
duplicate — the test goes green with no index at all, having proved nothing. The barrier holds both
transactions open, and past their read, before either is allowed to write. Both then discover an
empty table, both go to write, and exactly one is allowed to.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.db.models import Tenant, User
from aethercal.server.services import users as users_service
from aethercal.server.services.users import (
    DuplicateUserEmailError,
    UserData,
    _ensure_email_available,
    create_user,
)

pytestmark = pytest.mark.db


async def _seed_tenant(app: FastAPI) -> uuid.UUID:
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Studio")
        session.add(tenant)
        await session.flush()
        return tenant.id


async def _create(
    app: FastAPI,
    *,
    tenant_id: uuid.UUID,
    name: str,
    email: str,
    barrier: asyncio.Barrier,
) -> User:
    """One create, in its OWN session and transaction — a second admin tab.

    The read before the barrier is the service's own guard, run explicitly: it puts this transaction
    exactly where a racing one really is — open, and already satisfied that the address is free —
    before any writer is released. Both are there when the barrier drops, so neither can see the
    other's row (nobody has committed), and the database is left to settle it. That is the race,
    made deterministic instead of left to scheduling luck.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        await _ensure_email_available(session, tenant_id=tenant_id, email=email)
        await barrier.wait()
        return await create_user(
            session,
            tenant_id=tenant_id,
            data=UserData(name=name, email=email, timezone="UTC"),
        )


async def test_two_concurrent_hosts_differing_only_in_case_leave_exactly_one(app: FastAPI) -> None:
    tenant_id = await _seed_tenant(app)
    barrier = asyncio.Barrier(2)

    results = await asyncio.gather(
        _create(app, tenant_id=tenant_id, name="Ana", email="Ana@example.com", barrier=barrier),
        _create(
            app, tenant_id=tenant_id, name="Ana Ruiz", email="ana@example.com", barrier=barrier
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, User)]
    refused = [r for r in results if isinstance(r, DuplicateUserEmailError)]
    assert len(winners) == 1, f"expected exactly one host to survive, got {results!r}"
    assert len(refused) == 1, f"expected exactly one refusal, got {results!r}"

    # ==The refusal is the DOMAIN error, not a traceback.== The guard could not prevent this write;
    # the database did. What the service still owes the operator is a sentence they can act on —
    # "that address is already taken" — not `IntegrityError: duplicate key value violates unique
    # constraint "uq_users_tenant_id_email_lower"`, which is a database's sentence, not a person's.
    assert not any(isinstance(r, IntegrityError) for r in results), (
        f"raw IntegrityError: {results!r}"
    )
    assert "already exists" in str(refused[0])

    # And the business has ONE host, which is the fact that actually matters.
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session:
        rows = list((await session.scalars(select(User).where(User.tenant_id == tenant_id))).all())
    assert len(rows) == 1, f"a case-variant pair landed: {[row.email for row in rows]}"
    assert rows[0].id == winners[0].id


async def test_the_index_refusal_does_not_poison_the_callers_transaction(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is scoped to the ACTION, not to the caller's transaction.

    ==On PostgreSQL this is not a nicety, it is the reason the SAVEPOINT exists.== A statement that
    fails aborts the ENTIRE transaction — every command after it is refused with "current
    transaction is aborted" until somebody rolls back. ``create-tenant`` creates the tenant and its
    first host in ONE transaction, so a refused host with no savepoint under it would take the
    tenant with it, and there would be nothing left to retry INTO.

    The guard is blinded here on purpose: with it awake it refuses before any write, so the database
    is never reached and this proves nothing. Blinding it is exactly what a concurrent create does —
    the other transaction has not committed, so there is nothing for the guard to find — and it is
    the only way to make the INDEX be the thing that refuses, which is the path under test.
    """
    tenant_id = await _seed_tenant(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    async def _blind(*_args: object, **_kwargs: object) -> None:
        return None

    async with sessionmaker() as session, session.begin():
        await create_user(
            session,
            tenant_id=tenant_id,
            data=UserData(name="Ana", email="Ana@example.com", timezone="UTC"),
        )
        await session.flush()

        monkeypatch.setattr(users_service, "_ensure_email_available", _blind)
        with pytest.raises(DuplicateUserEmailError):
            await create_user(
                session,
                tenant_id=tenant_id,
                data=UserData(name="Ana Ruiz", email="ana@example.com", timezone="UTC"),
            )
        monkeypatch.undo()

        # The transaction is ALIVE: a DIFFERENT host still lands, in the SAME transaction — and it
        # commits. Without the savepoint, PostgreSQL would have refused this write outright.
        bruno = await create_user(
            session,
            tenant_id=tenant_id,
            data=UserData(name="Bruno", email="bruno@example.com", timezone="UTC"),
        )

    async with sessionmaker() as session:
        emails = {
            row.email
            for row in (
                await session.scalars(select(User).where(User.tenant_id == tenant_id))
            ).all()
        }
    # Ana landed once, the duplicate did not, and Bruno survived the refusal that sat between them.
    assert emails == {"Ana@example.com", "bruno@example.com"}
    assert bruno.id is not None


async def test_an_integrity_error_that_is_not_a_duplicate_email_is_not_dressed_up_as_one(
    app: FastAPI,
) -> None:
    """==Translating an IntegrityError is not the same as swallowing every IntegrityError.==

    A blanket ``except IntegrityError: raise DuplicateUserEmailError`` would tell the operator that
    "a host with the email 'ana@example.com' already exists" when what actually happened is that the
    TENANT does not exist — a lie, and one that sends them looking for a host that was never there.
    Only the case-insensitive uniqueness index is translated; anything else the database refuses is
    somebody else's bug, and it travels intact.

    (PostgreSQL-only on purpose: SQLite does not enforce foreign keys unless asked, so this
    particular violation cannot even be provoked offline.)
    """
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    with pytest.raises(IntegrityError):
        async with sessionmaker() as session, session.begin():
            await create_user(
                session,
                tenant_id=uuid.uuid4(),  # no such tenant → a foreign-key violation
                data=UserData(name="Ana", email="ana@example.com", timezone="UTC"),
            )
