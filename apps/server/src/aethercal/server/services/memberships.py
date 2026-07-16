"""Memberships: who is in a business, what they may do, and how they prove they are them (B-02).

The ``memberships`` domain service — and, like ``users``, the ONLY thing that writes its table.

.. rubric:: A host is not a member, and that distinction is the whole design

A ``users`` row is a HOST: the person an event type is offered against. Every business has hosts who
administer nothing, and — until this wave — *every host in the product had no password and no way to
sign in at all* (``users.hashed_password`` was declared in 0001 and was dead code: nobody wrote it,
nobody read it). A ``role`` column on ``users`` would have had to pick a default for all of them,
and every possible default is wrong: ``member`` locks the real administrator out of their own panel;
anything higher hands the panel to everybody who was ever bookable.

A separate table has no default. ==No membership ⇒ no login==, which is the only safe thing an
absent fact can mean, and :func:`authenticate_member` enforces exactly that.

.. rubric:: What is DECLARED OUT OF SCOPE (F5) — not forgotten, and not half-built

* **invitation by email** — an owner creates the member WITH an initial password and hands it over
  out of band;
* **password recovery** — there is no reset link and no "forgot password". A member who loses their
  password asks an owner to set them a new one.

Both need a mail round-trip with signed, expiring, single-use tokens, and half of that shipped is
worse than none: a reset link that is guessable, replayable or non-expiring is a login for whoever
finds it. They are F5, in the spec, in writing. What is here instead is the smallest complete thing:
:func:`change_own_password`, which takes the CURRENT password — so an admin panel left open on a
shared laptop is not a password reset.

.. rubric:: Every refusal from :func:`authenticate_member` looks the same

Wrong address, wrong password, no password set, no membership: all four return ``None``. A login
that distinguishes them is an ADDRESS ORACLE — it tells whoever is guessing which people are in this
business, which is precisely the enumeration row-level security exists to prevent. The KDF runs even
when the host does not exist, so the TIMING does not answer the question either.

Transaction control (commit / rollback) belongs to the caller, as everywhere else in this layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import MemberRole
from aethercal.server.db.models import Membership, User
from aethercal.server.passwords import hash_password, verify_password
from aethercal.server.services import users as users_service

# A PBKDF2 hash of nothing in particular, verified against when there is no host or no stored hash.
# ==The point is the TIME it costs, not the answer it gives.== An early return on "no such address"
# answers, in microseconds, the question :func:`authenticate_member` refuses to answer in words: is
# this person in this business? So the derivation runs anyway, and every refusal costs the same.
_ABSENT_PASSWORD_HASH = hash_password("aethercal: no password is set for this host")

# The floor a human-chosen password must clear. Not a policy engine, deliberately: length is the one
# rule that measurably helps, and no character-class rule ever written has stopped anybody typing
# "P@ssw0rd!" — which clears all of them.
MIN_PASSWORD_LENGTH = 12


class MembershipError(Exception):
    """Base class for membership-service failures."""


class MembershipNotFoundError(MembershipError):
    """No such membership in this business — an id that is unknown, or is another business's.

    Indistinguishable on purpose, exactly as ``UserNotFoundError`` is: the id arrives from a form,
    so
    "that membership is not yours" and "that membership does not exist" must read the same, or the
    panel becomes an oracle for the neighbouring business's rows.
    """


class DuplicateMembershipError(MembershipError):
    """This person already holds a role in this business."""


class LastOwnerError(MembershipError):
    """The refusal that keeps a business administrable.

    ==A business with no owner cannot be repaired from inside it==: only an owner may grant a role,
    and there would be none left. The INSTANCE operator could — and that is exactly why this must be
    a refusal rather than a "the operator can fix it later": on a hosted instance the operator is
    not
    the customer, and the customer's panel is the one that just locked itself.
    """


class WeakPasswordError(MembershipError):
    """The password is below :data:`MIN_PASSWORD_LENGTH`."""


class InvalidCredentialsError(MembershipError):
    """The CURRENT password offered to :func:`change_own_password` did not verify."""


@dataclass(frozen=True, slots=True)
class MemberRead:
    """One row of the members panel: the person, and what they may do."""

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: MemberRole
    has_password: bool
    """Whether this person can sign in at all. ==A member with no password is not a bug== — it is
    every host that existed before this wave — and the panel must show the owner which of their
    people still cannot get in, rather than leaving them to find out from the person."""


# --------------------------------------------------------------------------------------
# Reads.
# --------------------------------------------------------------------------------------
async def get_membership(
    session: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID
) -> Membership:
    """The business's membership by id, or :class:`MembershipNotFoundError`. Tenant-scoped, always.

    The ``tenant_id`` filter stays even though RLS now enforces it: the belt is the DATABASE's, and
    a
    service that leans on it exclusively reads zero rows and calls it "not found" the day somebody
    runs it on the owner connection — which the CLI does, by design (``BYPASSRLS``).
    """
    row = (
        await session.scalars(
            select(Membership).where(
                Membership.id == membership_id, Membership.tenant_id == tenant_id
            )
        )
    ).one_or_none()
    if row is None:
        raise MembershipNotFoundError(f"no membership {membership_id} in this business")
    return row


async def get_membership_for_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    """This host's role in this business, or ``None`` if they hold none (and so may not sign in)."""
    return (
        await session.scalars(
            select(Membership).where(
                Membership.tenant_id == tenant_id, Membership.user_id == user_id
            )
        )
    ).one_or_none()


async def list_members(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[MemberRead]:
    """The business's members, oldest first — what the members panel renders."""
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.created_at, Membership.id)
        )
    ).all()
    return [
        MemberRead(
            id=membership.id,
            user_id=user.id,
            name=user.name,
            email=user.email,
            role=membership.role,
            has_password=user.hashed_password is not None,
        )
        for membership, user in rows
    ]


async def count_owners(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    """How many owners this business has — the input to the last-owner refusal."""
    found = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.tenant_id == tenant_id, Membership.role == MemberRole.OWNER)
    )
    return int(found or 0)


# --------------------------------------------------------------------------------------
# Writes.
# --------------------------------------------------------------------------------------
async def grant_membership(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: MemberRole,
    password: str | None,
) -> Membership:
    """Give one of the business's HOSTS a role in it — and, optionally, a password to sign in with.

    ``password=None`` is a real case, not a loophole: it grants the role without a login (the person
    is listed; an owner gives them a password later). A ``NULL`` hash verifies against nothing, so
    they simply cannot sign in yet.

    ==The host is resolved WITHIN the business, and that is a security check, not a lookup.== A
    membership row carrying this business's ``tenant_id`` and ANOTHER business's ``user_id`` would
    let a person from over there sign in HERE — and every RLS policy would wave it through, because
    the row's ``tenant_id`` is this business's. ``users_service.get_user`` filters by tenant (and
    under RLS the foreign row is not readable at all), so the grant raises ``UserNotFoundError``
    rather than being written.
    """
    # Raises UserNotFoundError for an id that is not this business's host. This IS the guard.
    await users_service.get_user(session, tenant_id=tenant_id, user_id=user_id)

    if password is not None:
        _require_strong(password)

    membership = Membership(tenant_id=tenant_id, user_id=user_id, role=role)
    try:
        # A SAVEPOINT, so losing the race with the unique constraint refuses the ACTION rather than
        # killing the caller's transaction — ``users_service.create_user`` has the same shape, and
        # the CLI creates a tenant, its first host and its owner membership in ONE transaction.
        async with session.begin_nested():
            session.add(membership)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateMembershipError(
            "that person already holds a role in this business: change their role instead of "
            "granting them a second one"
        ) from exc

    if password is not None:
        await users_service.set_password(
            session, tenant_id=tenant_id, user_id=user_id, password=password
        )
    return membership


async def set_role(
    session: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID, role: MemberRole
) -> Membership:
    """Change what a member may do. Refuses to demote the LAST owner."""
    membership = await get_membership(session, tenant_id=tenant_id, membership_id=membership_id)
    if membership.role is MemberRole.OWNER and role is not MemberRole.OWNER:
        await _refuse_if_last_owner(session, tenant_id=tenant_id, action="demote")
    membership.role = role
    await session.flush()
    return membership


async def revoke_membership(
    session: AsyncSession, *, tenant_id: uuid.UUID, membership_id: uuid.UUID
) -> None:
    """Remove a person from the business. Refuses to remove the LAST owner.

    The ``users`` row SURVIVES: they remain a HOST, and their event types, schedules and bookings
    are
    untouched. What they lose is the ability to administer — which is the whole point, and is why
    this is not ``delete_user`` (whose refusals are about not orphaning a booking page).
    """
    membership = await get_membership(session, tenant_id=tenant_id, membership_id=membership_id)
    if membership.role is MemberRole.OWNER:
        await _refuse_if_last_owner(session, tenant_id=tenant_id, action="remove")
    await session.delete(membership)
    await session.flush()


async def change_own_password(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    """A member changes THEIR OWN password. ==The current one is required, and it is verified.==

    Not ceremony: an admin panel left open on a shared laptop must not be a password reset. (An
    OWNER setting somebody ELSE's password goes through the members panel — a different authority,
    a different capability, and it never needs to know the old one.)
    """
    user = await users_service.get_user(session, tenant_id=tenant_id, user_id=user_id)
    stored = user.hashed_password
    if stored is None or not verify_password(stored, current_password):
        raise InvalidCredentialsError("the current password is not correct")
    _require_strong(new_password)
    await users_service.set_password(
        session, tenant_id=tenant_id, user_id=user_id, password=new_password
    )


# --------------------------------------------------------------------------------------
# Authentication.
# --------------------------------------------------------------------------------------
async def authenticate_member(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str, password: str
) -> Membership | None:
    """The membership this address+password proves, or ``None``. ==Never which half failed.==

    ==The caller must have BOUND the business first.== This reads ``users`` and ``memberships``,
    both
    RLS-scoped: with no GUC bound it returns zero rows for everybody and *nobody ever signs in*.
    That
    is fail-closed (loud enough to find, and infinitely better than the alternative), but it has to
    be got right. The order is ``bootstrap_session`` → resolve the slug → ``bind_tenant`` →
    **this**;
    ``admin/bootstrap.py`` is where it is written down and where it is tested.

    Four ways to be refused, one answer:

    * no host at that address in this business;
    * the host has no password (``NULL`` — which is every host that predates this wave);
    * the password does not verify;
    * the host holds no membership. ==Being bookable is not permission to administer anything==, and
      there is no default role to fall back on — which is the entire reason the role lives in its
      own
      table.
    """
    user = await _host_by_email(session, tenant_id=tenant_id, email=email)
    stored = user.hashed_password if user is not None else None

    # The KDF runs whichever way this is going (see ``_ABSENT_PASSWORD_HASH``).
    verified = verify_password(stored or _ABSENT_PASSWORD_HASH, password)

    if user is None or stored is None or not verified:
        return None
    return await get_membership_for_user(session, tenant_id=tenant_id, user_id=user.id)


async def _host_by_email(session: AsyncSession, *, tenant_id: uuid.UUID, email: str) -> User | None:
    """The business's host at this address (case-insensitive) — or ``None``, never a raise.

    ``users_service.get_user_by_email`` raises: right for an operator typing a command, wrong at a
    login, where "no such address" and "wrong password" must be the same outcome. It also raises
    ``AmbiguousUserEmailError`` for a pre-0007 duplicate pair, and ==refusing to guess is the only
    safe answer at a LOGIN too==: two hosts differing only in case are two passwords, and picking
    either would authenticate somebody as a person they might not be. Both collapse to ``None``.
    """
    candidate = email.strip()
    rows = (
        await session.scalars(
            select(User).where(
                User.tenant_id == tenant_id, func.lower(User.email) == candidate.lower()
            )
        )
    ).all()
    return rows[0] if len(rows) == 1 else None


# --------------------------------------------------------------------------------------
# Guards.
# --------------------------------------------------------------------------------------
def _require_strong(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"a password must be at least {MIN_PASSWORD_LENGTH} characters. Length is the one rule "
            "that measurably helps: a passphrase of ordinary words beats a short string of "
            "punctuation, and no character-class rule has ever stopped anybody typing 'P@ssw0rd!'"
        )


async def _refuse_if_last_owner(
    session: AsyncSession, *, tenant_id: uuid.UUID, action: str
) -> None:
    if await count_owners(session, tenant_id=tenant_id) > 1:
        return
    raise LastOwnerError(
        f"refusing to {action} the last owner of this business.\n"
        "\n"
        "A business with no owner cannot be repaired from inside it: only an owner may grant a "
        "role, and there would be none left to do it.\n"
        "\n"
        "Make somebody else an owner first."
    )


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "DuplicateMembershipError",
    "InvalidCredentialsError",
    "LastOwnerError",
    "MemberRead",
    "MembershipError",
    "MembershipNotFoundError",
    "WeakPasswordError",
    "authenticate_member",
    "change_own_password",
    "count_owners",
    "get_membership",
    "get_membership_for_user",
    "grant_membership",
    "list_members",
    "revoke_membership",
    "set_role",
]
