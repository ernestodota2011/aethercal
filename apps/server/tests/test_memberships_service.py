"""Memberships: who is in a business, what they may do, and how they prove they are them (B-02).

Offline (SQLite) service-layer TDD. The BUSINESS-ISOLATION half of this — that two people with the
SAME address in two businesses each sign in to their own, and that neither can see the other — is
proven against a real PostgreSQL under RLS in ``tests/rls/test_rbac_isolation.py`` (criteria 38 and
39). It cannot be proven here: SQLite has no policies to enforce.

What IS proven here is everything the database cannot know: that a password is verified rather than
compared, that a wrong address and a wrong password are indistinguishable, that a host with no
membership cannot sign in at all, and that a business cannot be left with no owner.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import MemberRole
from aethercal.server.db.models import Membership, Tenant, User
from aethercal.server.services import memberships as service
from aethercal.server.services import users as users_service

_PASSWORD = "correct horse battery staple"

TenantFactory = Callable[..., Awaitable[Tenant]]


async def _host(session: AsyncSession, tenant: Tenant, *, email: str, name: str = "Ana") -> User:
    return await users_service.create_user(
        session, tenant_id=tenant.id, data=users_service.UserData(name=name, email=email)
    )


async def _owner(session: AsyncSession, tenant: Tenant) -> Membership:
    user = await _host(session, tenant, email="owner@example.com", name="Owner")
    return await service.grant_membership(
        session, tenant_id=tenant.id, user_id=user.id, role=MemberRole.OWNER, password=_PASSWORD
    )


# --------------------------------------------------------------------------------------
# The password: ``users.hashed_password`` stops being dead code.
# --------------------------------------------------------------------------------------


async def test_the_stored_password_is_a_pbkdf2_hash_and_never_the_password(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """==``users.hashed_password`` was DECLARED and dead== — nobody wrote it, nobody read it. It is
    alive now, and what lands in it is the same salted, stretched PBKDF2 string the instance
    operator's own credential uses. Never the password, and never a fast digest of it."""
    membership = await _owner(sqlite_session, tenant)
    user = await users_service.get_user(
        sqlite_session, tenant_id=tenant.id, user_id=membership.user_id
    )

    stored = user.hashed_password
    assert stored is not None
    assert stored.startswith("pbkdf2_sha256$")
    assert _PASSWORD not in stored


async def test_a_password_below_the_floor_is_refused(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """A human-chosen password is the weakest link the KDF is protecting; a floor is the cheapest
    thing that helps. Refused at the service, once, for every caller."""
    user = await _host(sqlite_session, tenant, email="short@example.com")
    with pytest.raises(service.WeakPasswordError):
        await service.grant_membership(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=user.id,
            role=MemberRole.MEMBER,
            password="short",
        )


async def test_a_member_can_change_their_own_password(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """The one self-service this wave ships. ==Invitation by email and password RECOVERY are F5==,
    declared out of scope: an owner creates the member with an initial password, and the member
    changes it. Nothing here pretends otherwise."""
    membership = await _owner(sqlite_session, tenant)
    await service.change_own_password(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=membership.user_id,
        current_password=_PASSWORD,
        new_password="a whole new passphrase",
    )

    assert (
        await service.authenticate_member(
            sqlite_session,
            tenant_id=tenant.id,
            email="owner@example.com",
            password="a whole new passphrase",
        )
        is not None
    )
    assert (
        await service.authenticate_member(
            sqlite_session, tenant_id=tenant.id, email="owner@example.com", password=_PASSWORD
        )
        is None
    )


async def test_changing_a_password_requires_the_current_one(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """A session left open on a shared laptop must not be a password reset."""
    membership = await _owner(sqlite_session, tenant)
    with pytest.raises(service.InvalidCredentialsError):
        await service.change_own_password(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=membership.user_id,
            current_password="not the current one",
            new_password="a whole new passphrase",
        )


# --------------------------------------------------------------------------------------
# Authentication: every refusal looks the same.
# --------------------------------------------------------------------------------------


async def test_the_right_address_and_password_return_the_membership(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    granted = await _owner(sqlite_session, tenant)
    found = await service.authenticate_member(
        sqlite_session, tenant_id=tenant.id, email="owner@example.com", password=_PASSWORD
    )
    assert found is not None
    assert found.id == granted.id
    assert found.role is MemberRole.OWNER


async def test_the_address_is_matched_case_insensitively(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """``Owner@Example.com`` is the same person as ``owner@example.com`` — as the unique index on
    ``(tenant_id, lower(email))`` has said since migration 0007."""
    await _owner(sqlite_session, tenant)
    assert (
        await service.authenticate_member(
            sqlite_session, tenant_id=tenant.id, email="OWNER@Example.com", password=_PASSWORD
        )
        is not None
    )


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("owner@example.com", "wrong password entirely"),
        ("nobody@example.com", _PASSWORD),
    ],
)
async def test_a_wrong_address_and_a_wrong_password_are_indistinguishable(
    sqlite_session: AsyncSession, tenant: Tenant, email: str, password: str
) -> None:
    """Both return ``None``. ==A login that says WHICH half was wrong is an address oracle==: it
    tells whoever is guessing which people are in this business, which is exactly the enumeration
    row-level security is protecting."""
    await _owner(sqlite_session, tenant)
    assert (
        await service.authenticate_member(
            sqlite_session, tenant_id=tenant.id, email=email, password=password
        )
        is None
    )


async def test_a_host_with_no_password_cannot_sign_in(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """==Every host that exists today has ``hashed_password = NULL``== (the column was dead). None
    of them may sign in until an owner gives them one: a NULL hash is not an empty password, and it
    must never verify against anything."""
    user = await _host(sqlite_session, tenant, email="legacy@example.com")
    await service.grant_membership(
        sqlite_session, tenant_id=tenant.id, user_id=user.id, role=MemberRole.ADMIN, password=None
    )

    assert (
        await service.authenticate_member(
            sqlite_session, tenant_id=tenant.id, email="legacy@example.com", password=""
        )
        is None
    )


async def test_a_host_with_no_membership_cannot_sign_in(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """==Fail-closed.== A ``users`` row is a HOST — the person a booking is offered against. It is
    not, on its own, permission to administer anything. The membership is what grants that, so a
    host with a password and no membership is refused — and cannot be handed a default role by
    accident, because there is no default."""
    user = await _host(sqlite_session, tenant, email="hostonly@example.com")
    await users_service.set_password(
        sqlite_session, tenant_id=tenant.id, user_id=user.id, password=_PASSWORD
    )

    assert (
        await service.authenticate_member(
            sqlite_session, tenant_id=tenant.id, email="hostonly@example.com", password=_PASSWORD
        )
        is None
    )


# --------------------------------------------------------------------------------------
# The membership rows themselves.
# --------------------------------------------------------------------------------------


async def test_a_person_holds_at_most_one_role_in_a_business(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """Two rows for one person is two answers to "what may they do", and the code would take
    whichever it happened to read first."""
    membership = await _owner(sqlite_session, tenant)
    with pytest.raises(service.DuplicateMembershipError):
        await service.grant_membership(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=membership.user_id,
            role=MemberRole.MEMBER,
            password=None,
        )


async def test_a_membership_cannot_be_granted_to_another_businesss_host(
    sqlite_session: AsyncSession, tenant: Tenant, tenant_factory: TenantFactory
) -> None:
    """==The escalation this refusal exists to stop is a LOGIN.==

    A membership row carrying Acme's ``tenant_id`` but Globex's ``user_id`` would let a person of
    Globex sign in to Acme — and every RLS policy would wave it through, because the row's
    ``tenant_id`` IS Acme's. The service resolves the host WITHIN the business (and under RLS the
    foreign row is not even readable), so the grant is refused rather than written.
    """
    other = await tenant_factory(sqlite_session, slug="globex", email="them@example.com")
    stranger = (await sqlite_session.scalars(select(User).where(User.tenant_id == other.id))).one()

    with pytest.raises(users_service.UserNotFoundError):
        await service.grant_membership(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=stranger.id,
            role=MemberRole.OWNER,
            password=_PASSWORD,
        )


async def test_the_members_are_listed_with_their_host(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """What the panel renders: the person (name, address) and what they may do (role)."""
    await _owner(sqlite_session, tenant)
    junior = await _host(sqlite_session, tenant, email="junior@example.com", name="Junior")
    await service.grant_membership(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=junior.id,
        role=MemberRole.MEMBER,
        password=_PASSWORD,
    )

    listed = await service.list_members(sqlite_session, tenant_id=tenant.id)
    # Compared as a SET, not a sequence. The service orders "oldest first" (``created_at``, then
    # ``id``), and on SQLite ``CURRENT_TIMESTAMP`` has SECOND resolution — so two members seeded in
    # the same second tie on ``created_at`` and fall to the uuid ``id``, which is random. Postgres
    # (microsecond ``created_at``) the order is stable; asserting the sequence here would test the
    # clock's resolution, not the service. This test is about CONTENT: the members and their roles.
    assert {(row.email, row.role) for row in listed} == {
        ("owner@example.com", MemberRole.OWNER),
        ("junior@example.com", MemberRole.MEMBER),
    }


async def test_a_role_can_be_changed(sqlite_session: AsyncSession, tenant: Tenant) -> None:
    await _owner(sqlite_session, tenant)
    junior = await _host(sqlite_session, tenant, email="junior@example.com", name="Junior")
    granted = await service.grant_membership(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=junior.id,
        role=MemberRole.MEMBER,
        password=None,
    )

    updated = await service.set_role(
        sqlite_session, tenant_id=tenant.id, membership_id=granted.id, role=MemberRole.ADMIN
    )
    assert updated.role is MemberRole.ADMIN


async def test_a_membership_can_be_revoked(sqlite_session: AsyncSession, tenant: Tenant) -> None:
    await _owner(sqlite_session, tenant)
    junior = await _host(sqlite_session, tenant, email="junior@example.com", name="Junior")
    granted = await service.grant_membership(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=junior.id,
        role=MemberRole.MEMBER,
        password=None,
    )

    await service.revoke_membership(sqlite_session, tenant_id=tenant.id, membership_id=granted.id)
    listed = await service.list_members(sqlite_session, tenant_id=tenant.id)
    assert [row.role for row in listed] == [MemberRole.OWNER]


async def test_an_unknown_membership_is_refused_rather_than_ignored(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """==A no-op UPDATE is this product's signature silent failure.== A "revoke" that matches no row
    and returns happily tells the operator the person is out while they are still in."""
    with pytest.raises(service.MembershipNotFoundError):
        await service.revoke_membership(
            sqlite_session, tenant_id=tenant.id, membership_id=uuid.uuid4()
        )


# --------------------------------------------------------------------------------------
# The last owner.
# --------------------------------------------------------------------------------------


async def test_the_last_owner_cannot_be_revoked(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """==A business with no owner is a business nobody can ever administer again.== Nothing inside
    the business can restore it: only an owner may grant a role, and there would be none left. The
    INSTANCE operator could — which is exactly why this must be a refusal and not a "well, the
    operator can fix it": on a hosted instance the operator is not the customer."""
    granted = await _owner(sqlite_session, tenant)
    with pytest.raises(service.LastOwnerError):
        await service.revoke_membership(
            sqlite_session, tenant_id=tenant.id, membership_id=granted.id
        )


async def test_the_last_owner_cannot_be_demoted(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """The same hole, through the other door. Closing only one of them closes neither."""
    granted = await _owner(sqlite_session, tenant)
    with pytest.raises(service.LastOwnerError):
        await service.set_role(
            sqlite_session, tenant_id=tenant.id, membership_id=granted.id, role=MemberRole.ADMIN
        )


async def test_an_owner_may_be_removed_while_another_owner_remains(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """The refusal is about the LAST owner, not about owners."""
    first = await _owner(sqlite_session, tenant)
    second_host = await _host(sqlite_session, tenant, email="second@example.com", name="Second")
    await service.grant_membership(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=second_host.id,
        role=MemberRole.OWNER,
        password=_PASSWORD,
    )

    await service.revoke_membership(sqlite_session, tenant_id=tenant.id, membership_id=first.id)
    listed = await service.list_members(sqlite_session, tenant_id=tenant.id)
    assert [row.email for row in listed] == ["second@example.com"]
