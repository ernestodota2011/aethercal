"""RBAC across the isolation belt, against a real PostgreSQL — ==criteria 38 and 39== (B-02).

These are the two acceptance criteria that CANNOT be proven offline, because the thing they turn on
is row-level security, and SQLite has no policies. They run on the same harness the rest of the
``db`` suite does (``tests/conftest.py``): the world is arranged on the ``owner`` engine
(``BYPASSRLS`` — a two-business fixture is impossible on the app role, since ``bind_tenant`` refuses
to re-bind a scope), and the system under test runs on the ``app`` engine, under RLS, taking its GUC
by the REAL path — the member login and the admin's own ``admin_session``.

* ==Criterion 38==: the bootstrap operator switches between two businesses and each shows only its
  own. Proven at the belt: the operator's ``admin_session(slug)`` binds each business in turn, and a
  query for one returns none of the other's rows — under ``FORCE ROW LEVEL SECURITY``, on a
  connection with no ``BYPASSRLS``.

* ==Criterion 39==: two people who share an address — ``ana@example.com`` in business A and the same
  string in business B — each sign in to THEIR OWN. This is the criterion the per-business login
  exists for: ``users`` is unique on ``(tenant_id, lower(email))`` (per business, migration 0007),
  so the address alone is ambiguous, and the login resolves ``(tenant_slug, email)``.

Both drive the exact production seams (``admin.bootstrap.member_login`` /
``AdminRuntime.admin_session``) rather than hand-stamping a GUC, so a belt that only *looked* wired
would read zero rows and go red — which is what a belt is for.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import MemberRole
from aethercal.server.admin import bootstrap, service
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.db.guc import reset_tenant_binding
from aethercal.server.db.models import Membership, Tenant, User
from aethercal.server.passwords import hash_password
from aethercal.server.services import memberships as memberships_service
from aethercal.server.services.rbac import Principal

pytestmark = pytest.mark.db

_PASSWORD_A = "correct horse battery staple"
_PASSWORD_B = "a completely different passphrase"


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


class _Person:
    """One member of one business, seeded on the owner engine."""

    def __init__(self, tenant_id: uuid.UUID, slug: str, user_id: uuid.UUID) -> None:
        self.tenant_id = tenant_id
        self.slug = slug
        self.user_id = user_id


@pytest_asyncio.fixture
async def same_email_two_businesses(
    owner_maker: async_sessionmaker[AsyncSession],
) -> tuple[_Person, _Person]:
    """``ana@example.com`` in TWO businesses — two people, two passwords, two roles. ==Seeded as the
    owner==, because ``bind_tenant`` makes a two-business fixture impossible on the app role.

    The address is byte-identical on purpose: it is the whole point of criterion 39. The unique
    index is ``(tenant_id, lower(email))``, so both rows are legal, and the ONLY thing that tells
    them apart is the business — which is why the login has to be per business.
    """
    people: list[_Person] = []
    async with owner_maker() as session, session.begin():
        for index, (slug, password, role) in enumerate(
            (
                ("acme", _PASSWORD_A, MemberRole.OWNER),
                ("globex", _PASSWORD_B, MemberRole.MEMBER),
            )
        ):
            tenant = Tenant(slug=slug, name=slug.title())
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                email="ana@example.com",  # IDENTICAL across both businesses
                name=f"Ana {index}",
                timezone="UTC",
                hashed_password=hash_password(password),
            )
            session.add(user)
            await session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
            people.append(_Person(tenant.id, slug, user.id))
    return people[0], people[1]


def _runtime(maker: async_sessionmaker[AsyncSession]) -> AdminRuntime:
    """A runtime over the APP engine — the system under test runs under RLS, never on the owner."""
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="operator", password_hash="x", tenant_slug=None),
    )


# ==========================================================================================
# ==CRITERION 39== — two people, one address, each into their own business.
# ==========================================================================================


class TestCriterion39SameEmailTwoBusinesses:
    async def test_each_person_signs_in_to_their_own_business(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """==The heart of criterion 39.== The address is the same; the SLUG decides who signs in.
        Ana-of-Acme's password gets Ana-of-Acme (and her OWNER role); Ana-of-Globex's password gets
        Ana-of-Globex (and her MEMBER role). Each is bound to her own business, under RLS."""
        acme, globex = same_email_two_businesses
        admin = _runtime(app_maker)

        into_acme = await bootstrap.member_login(
            admin, tenant_slug="acme", email="ana@example.com", password=_PASSWORD_A
        )
        assert into_acme is not None
        assert into_acme.tenant_id == acme.tenant_id
        assert into_acme.user_id == acme.user_id
        assert into_acme.role is MemberRole.OWNER

        into_globex = await bootstrap.member_login(
            admin, tenant_slug="globex", email="ana@example.com", password=_PASSWORD_B
        )
        assert into_globex is not None
        assert into_globex.tenant_id == globex.tenant_id
        assert into_globex.user_id == globex.user_id
        assert into_globex.role is MemberRole.MEMBER

        # ==And the two are genuinely different people.== Same string, different rows.
        assert into_acme.user_id != into_globex.user_id

    async def test_the_password_of_the_wrong_business_does_not_get_in(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """Ana-of-Globex's password, presented at Acme's door, is simply wrong. ==If the login
        matched on address alone this would let her into Acme==; it does not, because the business
        selects whose password is checked, and hers is not Acme's."""
        admin = _runtime(app_maker)

        assert (
            await bootstrap.member_login(
                admin, tenant_slug="acme", email="ana@example.com", password=_PASSWORD_B
            )
            is None
        )
        assert (
            await bootstrap.member_login(
                admin, tenant_slug="globex", email="ana@example.com", password=_PASSWORD_A
            )
            is None
        )

    async def test_an_unknown_business_is_refused_like_a_wrong_password(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """==Every refusal is one message.== An unknown slug returns ``None`` exactly as a wrong
        password does — never a distinct error that would confirm which businesses exist."""
        admin = _runtime(app_maker)
        assert (
            await bootstrap.member_login(
                admin,
                tenant_slug="no-such-business",
                email="ana@example.com",
                password=_PASSWORD_A,
            )
            is None
        )


# ==========================================================================================
# ==CRITERION 38== — the operator switches business, and each shows only its own.
# ==========================================================================================


class TestCriterion38OperatorSwitchesBusiness:
    async def test_the_operator_sees_each_business_only_its_own_members(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """==Switch, and only the selected business's rows appear.==

        The operator's ``admin_session(slug)`` binds each business in turn, under RLS, on a
        connection that holds no ``BYPASSRLS``. The members it reads for Acme are Acme's alone, and
        for Globex, Globex's alone — proven by the ROLE each carries (owner in one, member in the
        other) and by the fact that neither list contains the other's user id.
        """
        acme, globex = same_email_two_businesses
        admin = _runtime(app_maker)

        async with admin.admin_session("acme") as (session, ctx):
            assert ctx.tenant_id == acme.tenant_id
            acme_members = await memberships_service.list_members(session, tenant_id=ctx.tenant_id)

        async with admin.admin_session("globex") as (session, ctx):
            assert ctx.tenant_id == globex.tenant_id
            globex_members = await memberships_service.list_members(
                session, tenant_id=ctx.tenant_id
            )

        assert [m.user_id for m in acme_members] == [acme.user_id]
        assert [m.role for m in acme_members] == [MemberRole.OWNER]
        assert [m.user_id for m in globex_members] == [globex.user_id]
        assert [m.role for m in globex_members] == [MemberRole.MEMBER]
        # The two businesses share nobody, even though their one member shares an address.
        assert {m.user_id for m in acme_members}.isdisjoint(m.user_id for m in globex_members)

    async def test_switching_does_not_leak_a_bound_business_across_the_blocks(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """==The scope is the block.== After administering Acme, nothing stays bound — otherwise the
        very next ``admin_session('globex')`` would hit the immutability rule and raise, or worse,
        silently carry Acme's GUC into Globex's block. Each block opens and closes its own scope."""
        acme, globex = same_email_two_businesses
        admin = _runtime(app_maker)

        async with admin.admin_session("acme") as (_, ctx):
            assert ctx.tenant_id == acme.tenant_id
        # No TenantBindingError here is itself the assertion: the previous block released its scope.
        async with admin.admin_session("globex") as (session, ctx):
            assert ctx.tenant_id == globex.tenant_id
            rows = await session.scalars(select(User.email))
            assert list(rows.all()) == ["ana@example.com"]  # Globex's Ana, and only hers

    async def test_the_operator_holds_no_membership_of_either_business(
        self,
        same_email_two_businesses: tuple[_Person, _Person],
    ) -> None:
        """The operator administers both and belongs to NEITHER: their authority is the instance
        credential, not a ``memberships`` row. So their principal carries no business at all — which
        is exactly why ``require_operator`` (not a capability) is what gates switching."""
        operator = Principal.bootstrap_operator()
        assert operator.tenant_id is None
        assert operator.is_operator


# ==========================================================================================
# ==A role is re-read from `memberships` per action, under the belt== — not the login snapshot.
# ==========================================================================================


class TestStaleRoleIsRevalidatedUnderRls:
    """The critical B-02 defect, proven where it lives: a role change must reach an OPEN session on
    its very next action, and the re-read that makes it so runs under row-level security — bound to
    the business being administered, on the app role, with no ``BYPASSRLS``.
    """

    async def test_a_demoted_admins_next_action_is_refused_under_rls(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        owner_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """==Persistence of privilege, closed.== An ``admin`` signs in for real (the login writes
        ``admin`` onto the session), an owner demotes them to ``member`` in the database, and their
        next write is refused — because ``create_host_action`` re-reads the live role from
        ``memberships`` under RLS before it authorises, rather than trusting the snapshot the login
        took. A second owner is seeded so the demotion is not itself the last-owner refusal."""
        password = "correct horse battery staple"
        async with owner_maker() as session, session.begin():
            tenant = Tenant(slug="acme", name="Acme")
            session.add(tenant)
            await session.flush()
            owner = User(
                tenant_id=tenant.id,
                email="owner@acme.example",
                name="Owner",
                timezone="UTC",
                hashed_password=hash_password(password),
            )
            adm = User(
                tenant_id=tenant.id,
                email="adm@acme.example",
                name="Adm",
                timezone="UTC",
                hashed_password=hash_password(password),
            )
            session.add_all([owner, adm])
            await session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=owner.id, role=MemberRole.OWNER))
            adm_membership = Membership(tenant_id=tenant.id, user_id=adm.id, role=MemberRole.ADMIN)
            session.add(adm_membership)
            await session.flush()
            adm_membership_id = adm_membership.id

        admin = _runtime(app_maker)

        # The admin signs in — through the REAL login seam, under RLS. Snapshot role: admin.
        principal = await bootstrap.member_login(
            admin, tenant_slug="acme", email="adm@acme.example", password=password
        )
        assert principal is not None
        assert principal.role is MemberRole.ADMIN

        # An owner demotes them to a plain member (seeded on the BYPASSRLS owner engine).
        async with owner_maker() as session, session.begin():
            membership = await session.get(Membership, adm_membership_id)
            assert membership is not None
            membership.role = MemberRole.MEMBER

        # The stale ADMIN principal's next scheduling write is refused — the role is re-read per
        # action, under the belt, and a member does not hold MANAGE_SCHEDULING.
        with pytest.raises(service.AdminPermissionError):
            await service.create_host_action(
                admin,
                principal=principal,
                tenant_slug="acme",
                form=service.HostForm(name="New Host", email="new@acme.example", timezone="UTC"),
            )

    async def test_a_revoked_members_open_session_is_refused_under_rls(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        owner_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A member with their ``memberships`` row deleted is refused even a READ on their next
        action: no row under the belt means no session, not a member who may still look around."""
        password = "correct horse battery staple"
        async with owner_maker() as session, session.begin():
            tenant = Tenant(slug="globex", name="Globex")
            session.add(tenant)
            await session.flush()
            member = User(
                tenant_id=tenant.id,
                email="mem@globex.example",
                name="Mem",
                timezone="UTC",
                hashed_password=hash_password(password),
            )
            session.add(member)
            await session.flush()
            member_membership = Membership(
                tenant_id=tenant.id, user_id=member.id, role=MemberRole.MEMBER
            )
            session.add(member_membership)
            await session.flush()
            member_membership_id = member_membership.id

        admin = _runtime(app_maker)
        principal = await bootstrap.member_login(
            admin, tenant_slug="globex", email="mem@globex.example", password=password
        )
        assert principal is not None
        assert principal.role is MemberRole.MEMBER

        async with owner_maker() as session, session.begin():
            membership = await session.get(Membership, member_membership_id)
            assert membership is not None
            await session.delete(membership)

        with pytest.raises(service.SessionRevokedError):
            await service.list_bookings_view(admin, principal=principal, tenant_slug="globex")
