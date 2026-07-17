"""Two owners, two removers, one race — and only the DATABASE may settle it (PostgreSQL only).

``db``-marked, and for the same reason as ``test_users_concurrency.py``: the guarantee lives in
server-side concurrency control, which SQLite cannot exercise (it serialises writers anyway).

.. rubric:: The hole this closes

``memberships.revoke_membership`` (and ``set_role``) refuse to remove the LAST owner — and that
refusal is a **check-then-act**. It counts the owners, finds two, and only then deletes one. Two
removals that interleave — two owners each demoting the OTHER, an owner removed twice by a retried
request — can each read "there are two owners", each proceed, and leave the business with **none**:
a business nobody inside it can ever administer again, because only an owner may grant a role and
there is no owner left to do it. Nothing in the application layer can prevent it — the window
between the count and the delete is where the second remover lives.

So the invariant belongs to the database: the guard takes a ``SELECT ... FOR UPDATE`` over the
business's owner rows BEFORE it counts them. The second remover blocks on that lock until the first
commits, then re-reads the reduced set (one owner gone) and is refused. Exactly one survives.

==The hold-open is what makes this a proof.== Without it the two coroutines interleave by luck: the
first can finish and COMMIT before the second even reads, and then the ordinary count catches it —
the test goes green with no lock at all, having proved nothing. Here the FIRST transaction is held
OPEN, past its delete, before it commits. The second then reaches the guard while the first still
holds the lock: with the lock it blocks and is refused; WITHOUT it, its unlocked count still reads
two committed owners, it proceeds, and the business is left with none — which is the bug, made
deterministic instead of left to scheduling luck.
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
    """A business with exactly TWO owners — seeded on the BYPASSRLS owner engine, since there is no
    business bound yet to write it under. Returns the tenant and both owner ``membership`` ids."""
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


async def test_two_concurrent_last_owner_removals_leave_exactly_one_owner(
    app: FastAPI, owner_maker: async_sessionmaker
) -> None:
    tenant_id, first_id, second_id = await _seed_two_owners(owner_maker)
    sessionmaker: async_sessionmaker = app.state.sessionmaker

    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def _remove_first() -> None:
        """Remove one owner, then HOLD the transaction open — lock acquired, not yet committed."""
        with tenant_scope(tenant_id):
            async with sessionmaker() as session:
                async with session.begin():
                    await memberships_service.revoke_membership(
                        session, tenant_id=tenant_id, membership_id=first_id
                    )
                    first_holds_lock.set()
                    await release_first.wait()
                # The commit happens here, on leaving `session.begin()` — releasing the lock.

    async def _remove_second() -> None:
        """The racing removal of the OTHER owner: it must block on the lock, then be refused."""
        await first_holds_lock.wait()
        with tenant_scope(tenant_id):
            async with sessionmaker() as session, session.begin():
                await memberships_service.revoke_membership(
                    session, tenant_id=tenant_id, membership_id=second_id
                )

    task_first = asyncio.create_task(_remove_first())
    task_second = asyncio.create_task(_remove_second())

    await first_holds_lock.wait()
    # Give the second remover time to reach the guard: it blocks on the lock (fixed) or reads the
    # stale two-owner count and proceeds (buggy). Only then does the first commit and release.
    await asyncio.sleep(0.3)
    release_first.set()

    results = await asyncio.gather(task_first, task_second, return_exceptions=True)

    succeeded = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, LastOwnerError)]
    assert len(succeeded) == 1, f"expected exactly one removal to survive, got {results!r}"
    assert len(refused) == 1, f"expected exactly one last-owner refusal, got {results!r}"

    # ==The fact that actually matters: the business still has an owner.== Observed on the OWNER
    # engine, which sees every business, so a missing owner is not hidden by a policy here.
    async with owner_maker() as session:
        owners = list(
            (
                await session.scalars(
                    select(Membership).where(
                        Membership.tenant_id == tenant_id,
                        Membership.role == MemberRole.OWNER,
                    )
                )
            ).all()
        )
    assert len(owners) == 1, f"a business was left with {len(owners)} owners"
