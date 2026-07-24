"""Last-owner race through the DEMOTE door — the other half of RF-C01-5 (PostgreSQL only).

.. rubric:: Why this exists ALONGSIDE ``test_memberships_concurrency.py``

That file races two ``revoke_membership`` calls and proves the ``SELECT ... FOR UPDATE`` in
``_owner_memberships_for_update`` serialises them, so a business is never left ownerless. But the
last-owner refusal guards TWO doors, not one: ``revoke_membership`` (DELETE the row) and
``set_role`` (UPDATE the role away from ``owner``). Both call the same ``_refuse_if_last_owner``,
yet they are different SQL against different pages, and "the lock holds for a delete" is not
automatically "the lock holds for a demote": the guard re-reads its predicate (``role = owner``)
after blocking, and a demote satisfies that predicate by an UPDATE the lock must see committed,
exactly as a delete does.

So the demote path gets its own race, with the same hold-open control: the first transaction is
held OPEN past its write before it commits, so the second reaches the guard while the lock is
still held. With the lock it blocks and is refused; without it, its unlocked count reads two
committed owners and it proceeds, leaving the business with none.

``db``-marked: the guarantee is server-side concurrency control, which SQLite cannot exercise (it
serialises writers anyway, so the bug is invisible there — which is the whole reason it is here).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethercal.core.model import MemberRole
from aethercal.server.db.guc import tenant_scope
from aethercal.server.db.models import Membership, Tenant, User
from aethercal.server.services import memberships as memberships_service
from aethercal.server.services.memberships import LastOwnerError

pytestmark = pytest.mark.db


async def _seed_two_owners(
    owner_maker: async_sessionmaker,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A business with exactly TWO owners — seeded on the BYPASSRLS owner engine (no business is
    bound yet to write it under). Returns the tenant and both owner ``membership`` ids."""
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Studio")
        session.add(tenant)
        await session.flush()
        membership_ids: list[uuid.UUID] = []
        for index in range(2):
            user = User(
                tenant_id=tenant.id,
                email=f"owner{index}@example.com",
                name=f"Owner {index}",
                timezone="UTC",
            )
            session.add(user)
            await session.flush()
            membership = Membership(tenant_id=tenant.id, user_id=user.id, role=MemberRole.OWNER)
            session.add(membership)
            await session.flush()
            membership_ids.append(membership.id)
        return tenant.id, membership_ids[0], membership_ids[1]


async def _owner_count(owner_maker: async_sessionmaker, tenant_id: uuid.UUID) -> int:
    """Owners of the business, read on the BYPASSRLS owner engine (sees every business's rows)."""
    async with owner_maker() as session:
        owners = list(
            (
                await session.scalars(
                    select(Membership).where(
                        Membership.tenant_id == tenant_id, Membership.role == MemberRole.OWNER
                    )
                )
            ).all()
        )
    return len(owners)


async def test_two_concurrent_last_owner_demotions_leave_exactly_one_owner(
    app: FastAPI, owner_maker: async_sessionmaker
) -> None:
    """==Demote vs demote.== Two owners each demoted to ``member`` at the same instant: the FOR
    UPDATE lock lets exactly one through and refuses the other, so the business keeps an owner.
    Without the lock, both read two owners and both demote, and the business is left with none — the
    same bug the revoke race proves, reached by the OTHER write path."""
    tenant_id, first_id, second_id = await _seed_two_owners(owner_maker)
    sessionmaker: async_sessionmaker = app.state.sessionmaker

    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def _demote_first() -> None:
        """Demote one owner, then HOLD the transaction open — lock acquired, not yet committed."""
        with tenant_scope(tenant_id):
            async with sessionmaker() as session:
                async with session.begin():
                    await memberships_service.set_role(
                        session,
                        tenant_id=tenant_id,
                        membership_id=first_id,
                        role=MemberRole.MEMBER,
                    )
                    first_holds_lock.set()
                    await release_first.wait()
                # Commit happens here, on leaving ``session.begin()`` — releasing the lock.

    async def _demote_second() -> None:
        """The racing demotion of the OTHER owner: it must block on the lock, then be refused."""
        await first_holds_lock.wait()
        with tenant_scope(tenant_id):
            async with sessionmaker() as session, session.begin():
                await memberships_service.set_role(
                    session, tenant_id=tenant_id, membership_id=second_id, role=MemberRole.MEMBER
                )

    task_first = asyncio.create_task(_demote_first())
    task_second = asyncio.create_task(_demote_second())

    await first_holds_lock.wait()
    # Let the second demoter reach the guard: it blocks on the lock (fixed) or reads the stale
    # two-owner count and proceeds (buggy). Only then does the first commit and release.
    await asyncio.sleep(0.3)
    release_first.set()

    results = await asyncio.gather(task_first, task_second, return_exceptions=True)

    succeeded = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, LastOwnerError)]
    assert len(succeeded) == 1, f"expected exactly one demotion to survive, got {results!r}"
    assert len(refused) == 1, f"expected exactly one last-owner refusal, got {results!r}"

    assert await _owner_count(owner_maker, tenant_id) == 1, "a business was left without an owner"


async def test_a_revoke_and_a_demote_racing_the_last_owner_leave_one(
    app: FastAPI, owner_maker: async_sessionmaker
) -> None:
    """==The mixed race — the realistic one.== One request REVOKES an owner while another DEMOTES
    the other, at the same instant. Both doors reach the same lock, so exactly one wins and the
    business still has an owner. A guard that locked only within a single call path would let these
    two — on different paths — slip past each other."""
    tenant_id, revoke_id, demote_id = await _seed_two_owners(owner_maker)
    sessionmaker: async_sessionmaker = app.state.sessionmaker

    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def _revoke_first() -> None:
        with tenant_scope(tenant_id):
            async with sessionmaker() as session:
                async with session.begin():
                    await memberships_service.revoke_membership(
                        session, tenant_id=tenant_id, membership_id=revoke_id
                    )
                    first_holds_lock.set()
                    await release_first.wait()

    async def _demote_second() -> None:
        await first_holds_lock.wait()
        with tenant_scope(tenant_id):
            async with sessionmaker() as session, session.begin():
                await memberships_service.set_role(
                    session, tenant_id=tenant_id, membership_id=demote_id, role=MemberRole.MEMBER
                )

    task_first = asyncio.create_task(_revoke_first())
    task_second = asyncio.create_task(_demote_second())

    await first_holds_lock.wait()
    await asyncio.sleep(0.3)
    release_first.set()

    results = await asyncio.gather(task_first, task_second, return_exceptions=True)

    succeeded = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, LastOwnerError)]
    assert len(succeeded) == 1, f"expected exactly one write to survive, got {results!r}"
    assert len(refused) == 1, f"expected exactly one last-owner refusal, got {results!r}"

    assert await _owner_count(owner_maker, tenant_id) == 1, "a business was left without an owner"
