"""The two things that run BEFORE a business is bound — and the only callers of the unbound session.

``AdminRuntime`` offers exactly two doors: :meth:`~..runtime.AdminRuntime.admin_session`, which
resolves a business and BINDS it before it yields, and
:meth:`~..runtime.AdminRuntime.bootstrap_session`, which yields with **nothing bound**. B-01 left
the
second one declared, named and alone — and, deliberately, with no callers at all
(``test_admin_session_belt`` asserted that emptiness, and named this wave as the reason it would
end). ==This module is its caller list, and the list is two functions long.==

.. rubric:: Why either of them needs an unbound session

* :func:`list_businesses` — the operator's business SELECTOR (criterion 38). It has to enumerate the
  businesses in order to offer them, and the answer it is looking for IS the binding. It cannot run
  inside one.
* :func:`member_login` — a member signing in (criterion 39). It arrives with a slug, an address and
  no authority whatsoever; the business is the thing it is trying to establish.

``tenants`` carries no RLS policy by design (B-01: the slugs are semi-public, and a policy there
would lock the admin out of its own boot), so both reads work with nothing bound. ==Everything else
read through this door reads NOTHING== — which is why :func:`member_login` binds the business before
it goes anywhere near ``users``.

.. rubric:: ==The ORDER inside :func:`member_login`, and what each step buys==

#. ``bootstrap_session()`` — a transaction, with no business bound.
#. resolve the slug (``tenants``: no policy, so a plain read reaches it).
#. ``bind_tenant`` — stamp the GUC onto the transaction that is ALREADY open.
#. **only now** read ``users`` and ``memberships``.

Get step 3 wrong and step 4 returns **zero rows for everybody**: nobody ever signs in again, in
silence, and the panel tells the owner of the business that their password is invalid. That is
fail-closed — the right way to be wrong — but it still has to be written correctly. So it is written
once, here, and ``tests/rls/test_rbac_isolation.py`` proves it against a real PostgreSQL, where
getting it wrong genuinely costs zero rows (on SQLite there is no policy to forget).

.. rubric:: ==Why the login is PER BUSINESS==

Because an address is not unique on this instance. ``users`` is unique on ``(tenant_id,
lower(email))`` — **per business, not globally** (migration 0007) — so ``ana@example.com`` can be
two
different people, in two different businesses, with two different passwords and two different roles.
A login that took only an address would have to GUESS which of them was signing in, and both ways of
guessing are defects: take the first row (and authenticate somebody as a person they are not), or
search every business (an oracle for who exists where). ==So the business comes from the ROUTE
(``/admin/{tenant_slug}/login``), the resolver takes ``(tenant_slug, email)``, and there is no
ambiguity left to resolve.== That is criterion 39.

The slug is **not a secret and confers no authority**. It only SELECTS which business's password is
about to be checked. What authorises is the password.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.guc import bind_tenant, tenant_scope
from aethercal.server.db.models import Tenant
from aethercal.server.services import memberships as memberships_service
from aethercal.server.services import rbac
from aethercal.server.services.rbac import Principal
from aethercal.server.services.tenant_resolution import tenant_by_slug


class BootstrapSessions(Protocol):
    """An accessor that offers the UNBOUND door — ==asked for by name, in a type.==

    The panels take :class:`~..service.AdminSessions`, whose only method binds the business before
    it
    yields. These two functions need the other door, so they declare a type that HAS it: "this
    function can open a session with no business bound" is then visible in its signature rather than
    buried in its body, and a panel cannot reach for it by accident — the object it would need is
    not
    in its parameter's type.
    """

    def bootstrap_session(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Open a transaction with NO business bound (``AdminRuntime.bootstrap_session``)."""
        ...  # pragma: no cover - a protocol declaration; the runtime implements it


@dataclass(frozen=True, slots=True)
class BusinessRead:
    """One choice in the operator's business selector."""

    id: uuid.UUID
    slug: str
    name: str


async def list_businesses(admin: BootstrapSessions, *, principal: Principal) -> list[BusinessRead]:
    """Every business on this instance — ==the operator's selector, and half of criterion 38.==

    Gated by :func:`~...services.rbac.require_operator`, which is not a capability and cannot be
    one:
    ``owner`` means "everything **in my business**", so an owner of Acme able to enumerate — and
    then
    step into — Globex would be the cross-business escalation this batch exists to prevent.
    Switching
    business is a property of the principal's KIND: the instance's operator, whose credential lives
    in the environment and who belongs to no business at all.

    Before B-02 there was no selector: with more than one business and no
    ``AETHERCAL_ADMIN_TENANT_SLUG``, the admin simply **raised** (``service._resolve_tenant``:
    *"multiple tenants exist; set AETHERCAL_ADMIN_TENANT_SLUG to choose one"*). That is what this
    replaces — the operator picks, at run time, and ``admin_session`` binds whichever they picked.
    """
    rbac.require_operator(principal)
    async with admin.bootstrap_session() as session:
        rows = (await session.scalars(select(Tenant).order_by(Tenant.created_at, Tenant.id))).all()
        return [BusinessRead(id=row.id, slug=row.slug, name=row.name) for row in rows]


async def member_login(
    admin: BootstrapSessions, *, tenant_slug: str, email: str, password: str
) -> Principal | None:
    """Sign a MEMBER of ``tenant_slug`` in, or return ``None``. ==It never says which half failed.==

    An unknown business, an unknown address, a wrong password, a host with no password, a host with
    no membership: **all five** return ``None``. Telling them apart would turn the login form into
    an
    oracle for which businesses exist on this instance and who is inside them — precisely the
    enumeration row-level security is here to prevent. (An unknown slug therefore does NOT raise
    ``AdminSetupError`` the way ``admin_session`` would — which is the whole reason this path opens
    its own session rather than borrowing that one.)

    The :class:`~...services.rbac.Principal` it returns carries the business the SERVER verified,
    and
    that is what every panel compares the requested ``tenant_slug`` against from here on. ==The
    business is never taken from the client again.==
    """
    async with admin.bootstrap_session() as session:
        # 1-2. Nothing is bound yet. `tenants` carries no policy, so this read reaches the row — and
        # it is the ONLY read that would.
        tenant_id = await tenant_by_slug(session, tenant_slug.strip())
        if tenant_id is None:
            # ==An unknown business must cost what a wrong password costs.== `authenticate_member`
            # runs the KDF for an unknown HOST (so a wrong address and a wrong password are
            # indistinguishable in time); this path returns one step earlier, before a business is
            # bound and it could run at all. Skipping the derivation here would make the response
            # time an oracle for which business slugs exist — so spend the same budget first.
            memberships_service.spend_absent_kdf(password)
            return None

        # 3. Bind, and only THEN read. The `after_begin` listener has already fired on this
        # transaction — on an empty binding, because the business was not known yet — so
        # `bind_tenant`
        # stamps the GUC onto the transaction that is already open. Without this line, step 4
        # returns
        # zero rows for everybody and nobody ever signs in again.
        with tenant_scope(tenant_id):
            await bind_tenant(session, tenant_id)

            # 4. Under RLS, in this business, and in no other.
            membership = await memberships_service.authenticate_member(
                session, tenant_id=tenant_id, email=email, password=password
            )
            if membership is None:
                return None
            return Principal.member(
                tenant_id=tenant_id, user_id=membership.user_id, role=membership.role
            )


__all__ = ["BootstrapSessions", "BusinessRead", "list_businesses", "member_login"]
