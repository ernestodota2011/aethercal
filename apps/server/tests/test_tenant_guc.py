"""The GUC lifecycle — the ``ContextVar``, the listener, and the immutability rule.

``SET LOCAL`` dies at every ``commit()``. A belt that stamps the GUC once, by hand, at the top of a
handler is therefore a belt with holes in it: the payments batch introduces a mid-request commit,
``expire_on_commit=False`` lets a lazy load open a brand-new transaction after the commit, and the
worker's drain is not a request at all but a LOOP over items belonging to different businesses.

So the GUC is not stamped by call sites. A ``ContextVar`` holds the bound business and an
``after_begin`` listener stamps **every** transaction the sessionmaker opens while it is populated.
The call sites only ever *bind*.

The effective-state half of this (that PostgreSQL really receives the setting, and that a session
without one reads zero rows) is ``db``-marked and lives in ``test_rls_isolation.py``: SQLite has no
``current_setting`` and no roles, so an offline test here could only prove the Python half.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.db.guc import (
    TenantBindingError,
    bind_tenant,
    current_tenant,
    reset_tenant_binding,
    tenant_scope,
)


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    """Each test starts with nothing bound (a ContextVar outlives a coroutine in the same task)."""
    reset_tenant_binding()


class TestBindTenant:
    async def test_binding_populates_the_context_var(self, sqlite_session: AsyncSession) -> None:
        tenant_id = uuid.uuid4()
        await bind_tenant(sqlite_session, tenant_id)
        assert current_tenant() == tenant_id

    async def test_re_binding_the_same_business_is_allowed(
        self, sqlite_session: AsyncSession
    ) -> None:
        """Two dependencies resolving the same business must not fight each other."""
        tenant_id = uuid.uuid4()
        await bind_tenant(sqlite_session, tenant_id)
        await bind_tenant(sqlite_session, tenant_id)
        assert current_tenant() == tenant_id

    async def test_re_binding_another_business_in_the_same_scope_raises(
        self, sqlite_session: AsyncSession
    ) -> None:
        """==Without this, a handler could re-stamp the GUC: escalation between businesses.==

        The scope is the REQUEST in the web and ONE ITEM in the worker. Inside it the binding is
        immutable. Crossing to another business means opening a new scope, explicitly.
        """
        first, second = uuid.uuid4(), uuid.uuid4()
        await bind_tenant(sqlite_session, first)

        with pytest.raises(TenantBindingError) as caught:
            await bind_tenant(sqlite_session, second)

        assert str(first) in str(caught.value)
        assert str(second) in str(caught.value)
        assert current_tenant() == first, "the original binding must survive the refusal"


class TestTenantScope:
    """The worker's scope is ONE ITEM — a drain loop crosses businesses by design.

    Without an explicit per-item scope both outcomes are bad, and both are silent: honour the
    immutability rule and the batch's second business RAISES (everything not belonging to the first
    business dies); relax it so the loop compiles and an item of B executes with the GUC of A — its
    reads come back ``None``, the handlers read that as "cascade-deleted, be defensive", and the
    intent settles ``delivered`` **having sent nothing**.
    """

    def test_a_scope_binds_and_then_unbinds(self) -> None:
        tenant_id = uuid.uuid4()
        assert current_tenant() is None
        with tenant_scope(tenant_id):
            assert current_tenant() == tenant_id
        assert current_tenant() is None

    def test_consecutive_scopes_may_carry_different_businesses(self) -> None:
        first, second = uuid.uuid4(), uuid.uuid4()
        with tenant_scope(first):
            assert current_tenant() == first
        with tenant_scope(second):
            assert current_tenant() == second

    def test_a_scope_unbinds_even_when_the_item_raises(self) -> None:
        tenant_id = uuid.uuid4()
        with pytest.raises(RuntimeError), tenant_scope(tenant_id):
            raise RuntimeError("the effect blew up")
        assert current_tenant() is None, "a failed item must not leave its business bound"

    async def test_binding_another_business_inside_a_scope_still_raises(
        self, sqlite_session: AsyncSession
    ) -> None:
        first, second = uuid.uuid4(), uuid.uuid4()
        with tenant_scope(first), pytest.raises(TenantBindingError):
            await bind_tenant(sqlite_session, second)


class TestTheBindingDoesNotLeakBetweenTasks:
    """A ``ContextVar`` set inside a task does not escape it — which is what makes the REQUEST the
    natural scope in the web. Asserted, not assumed: the whole belt rests on it."""

    async def test_a_binding_made_in_one_task_is_invisible_to_another(
        self, sqlite_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        seen: list[uuid.UUID | None] = []

        async def _request(tenant_id: uuid.UUID) -> None:
            async with sqlite_maker() as session:
                await bind_tenant(session, tenant_id)
                await asyncio.sleep(0)  # yield: let the other "request" interleave
                seen.append(current_tenant())

        first, second = uuid.uuid4(), uuid.uuid4()
        await asyncio.gather(_request(first), _request(second))

        assert set(seen) == {first, second}, "each task kept its own binding"
        assert current_tenant() is None, "and neither escaped into the parent"
