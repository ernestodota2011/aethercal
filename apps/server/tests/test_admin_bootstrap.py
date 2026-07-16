"""``admin.bootstrap.member_login`` — every refusal costs the same, so timing tells nothing.

An unknown business, an unknown address and a wrong password all return ``None``, and — this file —
all cost the same wall-clock work. ``authenticate_member`` already runs the KDF for an unknown HOST
(see ``test_memberships_service``), so a wrong address and a wrong password are indistinguishable in
time. The hole this closes is one level UP: an unknown BUSINESS fails a step earlier — the slug
does not resolve, and ``member_login`` returns before a business is bound and
``authenticate_member`` could run. A refusal that skipped the derivation THERE would answer, in
microseconds, the question
the whole login refuses to answer in words — *does this business exist?* — and the response time
becomes an oracle for which slugs are real.

.. rubric:: ==Measured by INSTRUMENTING the hasher, never by a clock==

A wall-clock assertion on a KDF is flaky by construction (a GC pause, the scheduler, a busy CI box),
and a flaky security test is one that gets marked ``xfail`` and stops guarding anything. So these
count CALLS to ``verify_password`` instead: the derivation ran, or it did not, and that is a fact
with no jitter in it. Both the unknown-host path (``authenticate_member``) and the unknown-business
path (``spend_absent_kdf``) go through the one module-level ``verify_password`` in ``memberships``,
so a single patch catches every derivation the login performs.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import MemberRole
from aethercal.server.admin import bootstrap
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.db.models import Membership, Tenant, User
from aethercal.server.passwords import hash_password
from aethercal.server.services import memberships as memberships_service


def _runtime(maker: async_sessionmaker[AsyncSession]) -> AdminRuntime:
    """A runtime over the offline sqlite maker — ``member_login`` needs only ``bootstrap_session``,
    which binds no business, so nothing here is anything a real PostgreSQL would do differently."""
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="operator", password_hash="x", tenant_slug=None),
    )


def _count_kdf(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Spy on the login's KDF; return a getter for how many times it ran.

    ``spend_absent_kdf`` and ``authenticate_member`` both call the module-level ``verify_password``
    in ``memberships``, so patching that one name counts every derivation the login performs.
    ``_ABSENT_PASSWORD_HASH`` was minted at import with the REAL ``hash_password``, so the reference
    hash is untouched by this patch.
    """
    calls = 0
    real = memberships_service.verify_password

    def _spy(stored: str, presented: str) -> bool:
        nonlocal calls
        calls += 1
        return real(stored, presented)

    monkeypatch.setattr(memberships_service, "verify_password", _spy)
    return lambda: calls


async def _seed_member(
    maker: async_sessionmaker[AsyncSession], *, slug: str, email: str, password: str
) -> None:
    """One business with one signed-in-capable member — the baseline a wrong password is measured
    against."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            email=email,
            name="Ana",
            timezone="UTC",
            hashed_password=hash_password(password),
        )
        session.add(user)
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=MemberRole.MEMBER))


async def test_an_unknown_business_still_runs_the_kdf(
    sqlite_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """==The timing oracle, closed.== A slug that resolves to no business returns ``None`` — and it
    must PAY the KDF while doing so, or its faster answer tells an attacker the slug is not real.
    Before the fix this path returned before any derivation ran (call count 0); now it spends the
    same budget a wrong password would."""
    kdf_calls = _count_kdf(monkeypatch)
    admin = _runtime(sqlite_maker)

    result = await bootstrap.member_login(
        admin, tenant_slug="no-such-business", email="ana@example.com", password="whatever-it-is"
    )

    assert result is None
    assert kdf_calls() == 1  # the derivation ran, exactly as an existing business's would


async def test_it_costs_the_same_kdf_a_wrong_password_does(
    sqlite_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline the test above is measured against: a REAL business with a WRONG password runs
    the KDF exactly once and returns ``None``. The unknown-business path must match this number —
    and it does — which is what makes the two indistinguishable in time."""
    await _seed_member(sqlite_maker, slug="acme", email="ana@example.com", password="the-real-one")
    kdf_calls = _count_kdf(monkeypatch)
    admin = _runtime(sqlite_maker)

    result = await bootstrap.member_login(
        admin, tenant_slug="acme", email="ana@example.com", password="not-the-real-one"
    )

    assert result is None
    assert kdf_calls() == 1
