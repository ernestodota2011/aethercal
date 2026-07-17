"""The isolation suite's own fixture — the two businesses only a bypassing role can arrange.

.. rubric:: ==Everything else moved UP, and that move IS the hole this batch had left open==

The three roles, the really-migrated schema, the owner-seeding engine and the app engine under RLS
used to live HERE, and only here. That is exactly what made the rest of the suite theatre: the
``-m db`` tests one directory up built their schema with ``Base.metadata.create_all``, as the role
that owns the database — no policies, no ``FORCE``, no belt — so a service that forgot to call
``bind_tenant`` could not have failed in CI, and this directory was the only place in the entire
product where isolation was ever actually enforced.

So the harness now lives in ``tests/conftest.py`` (``pg_role_urls`` / ``owner_maker`` /
``app_maker`` / ``worker_pools`` / ``pg_clean``), and EVERY db-marked test in the suite runs on it.
This file keeps only what is specific to proving the belt.

.. rubric:: Why the seeding runs as the OWNER, and why this suite is a lie without it

The obvious way to make these tests pass is to point the "app" engine at the owner's URL. Everything
would go green, and nothing whatsoever would be enforced. So the two halves are kept apart,
deliberately, and that split IS the design:

* **the seeding runs on the OWNER engine** (``BYPASSRLS``). It has to: a fixture that creates TWO
  businesses cannot run on the app role at all, because ``bind_tenant`` RAISES when a scope is
  re-bound to a second business — by the design of this very batch. Seeding is arrangement, not
  behaviour; it is allowed to reach across.
* **the system under test runs on the APP engine**, under RLS, and takes its GUC by the REAL path:
  the auth dependency, ``admin_session``, ``tenant_scope`` per item in the worker. If any of those
  is wrong, the test reads zero rows and goes red — which is what a belt is for.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.db.models import Tenant, User
from aethercal.server.services.api_keys import issue_api_key


@pytest_asyncio.fixture
async def two_businesses(
    owner_maker: async_sessionmaker[AsyncSession],
) -> list[tuple[uuid.UUID, str]]:
    """Two businesses, each with an API key. ==Seeded on the OWNER engine, and it MUST be.==

    On the app engine this fixture is *impossible to write*, and not by accident: ``bind_tenant``
    raises when a scope is re-bound to a second business, which is a rule this batch introduced on
    purpose. A fixture that needs two businesses is therefore proof, on its own, that seeding and
    exercising cannot be the same connection.
    """
    created: list[tuple[uuid.UUID, str]] = []
    async with owner_maker() as session, session.begin():
        for index in range(2):
            tenant = Tenant(slug=f"biz-{index}-{uuid.uuid4().hex[:6]}", name=f"Business {index}")
            session.add(tenant)
            await session.flush()
            session.add(
                User(
                    tenant_id=tenant.id,
                    email=f"host{index}@example.com",
                    name=f"Host {index}",
                    timezone="UTC",
                )
            )
            _, key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")
            created.append((tenant.id, key))
    return created
