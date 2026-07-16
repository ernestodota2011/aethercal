"""Authorisation: what a PERSON may do inside the business RLS already decided they are in (B-02).

.. rubric:: ==The database isolates businesses. It does not authorise people.==

Migration 0008 put a belt on every scoped table, and the belt compares one thing: ``tenant_id``. It
is exactly the right question for *"may this session touch this row's business?"* and it is
completely blind to *"may this person do this to their own business?"*. A ``member`` of Acme who
deletes Acme's hosts, revokes Acme's API keys, or removes Acme's owner from Acme is writing rows
that carry Acme's ``tenant_id`` — so every policy in PostgreSQL says **yes**, and says it silently.

==So the role gate cannot be in the database, and it is not.== It is here: one total function from
role to capability, called by the service layer before it acts. The GUC remains the authority for
*which* business; this decides *what*.

.. rubric:: Two kinds of principal, and why the operator is NOT a fourth role

* the **bootstrap operator** — the instance's operator. Their credential is in the environment
  (``AETHERCAL_ADMIN_USERNAME`` / ``..._PASSWORD_HASH``); they have no row in any table and belong
  to no business. They administer whichever business they select, and B-02 gives them the selector.
* a **member** — a ``users`` row with a password and a ``memberships`` row. Their business is the
  one the login VERIFIED them into, and it travels on the principal.

Modelling the operator as a fourth role would collapse two different things into one, and the seam
where it breaks is :func:`require_operator`: an ``owner`` means "everything **in my business**", so
an owner able to *switch* business would be precisely the cross-business escalation this batch
exists to prevent. Switching is therefore not a capability at all — it is a property of the
principal's KIND, with its own gate. (B-03's per-business credentials and B-05b's refunds ARE
capabilities, and they are in the table below already, denied to ``member`` today — so the panels
that will hold them are born gated rather than gated afterwards.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from aethercal.core.model import MemberRole


class Capability(StrEnum):
    """What a principal may DO. Business-scoped, always: every one of these is "…of MY business".

    ==Adding one is a decision for all three roles.== ``test_rbac`` asserts every role decides every
    capability: one that is added and quietly appears in nobody's set denies for everyone; one that
    lands in ``owner``'s set by a copy-paste hands the business away.
    """

    VIEW = "view"
    """Read the business: its agenda, hosts, event types, schedules, workflows, health."""

    MANAGE_SCHEDULING = "manage_scheduling"
    """Write what the business OFFERS, and act on ANY booking in it — hosts, event types,
    schedules, workflows, templates, calendar connections."""

    MANAGE_OWN_BOOKINGS = "manage_own_bookings"
    """Act on the bookings this person HOSTS. The row-level half of it (*is this booking theirs?*)
    cannot live in a capability — it is checked against the booking's host, where the action is."""

    MANAGE_MEMBERS = "manage_members"
    """See and edit who is in the business, and what they may do. ==Owner only== (criterion 37):
    whoever can grant ``owner`` can hand the business over, so this IS the business."""

    MANAGE_CREDENTIALS = "manage_credentials"
    """The business's own credentials — its API keys today, its BYOK payment/messaging secrets from
    B-03. ==Denied to ``member`` (criterion 37).=="""

    MANAGE_BILLING = "manage_billing"
    """Money: prices, refunds, the payout account (B-05b). ==Owner only.=="""


class PrincipalKind(StrEnum):
    """Who is acting. Not a role — see the module docstring."""

    BOOTSTRAP_OPERATOR = "bootstrap_operator"
    MEMBER = "member"


class PermissionDeniedError(Exception):
    """A principal was refused a capability. ==Raised, never returned as an empty list.==

    A refusal that renders as "you have no members" instead of "you may not see the members" is a
    silent no-op wearing a UI: the person is told something false ABOUT THE BUSINESS, and nobody
    learns that a gate fired. The service raises; the panel says so.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """WHO is acting — and, for a member, the business the server verified them into.

    ==``tenant_id`` here is an AUTHORITY, not a request.== It is written by the login, from a
    server-side ``memberships`` lookup, and the service layer compares the business a panel asks for
    against it. It is never a value that came from the client: a member of Acme who could send
    ``tenant_slug=globex`` and be believed is the whole attack.
    """

    kind: PrincipalKind
    tenant_id: uuid.UUID | None
    user_id: uuid.UUID | None
    role: MemberRole | None

    @classmethod
    def bootstrap_operator(cls) -> Principal:
        """The instance's operator (env credential). Belongs to no business — hence the ``None``s:
        the business they administer is the one they SELECT, re-resolved server-side every time."""
        return cls(kind=PrincipalKind.BOOTSTRAP_OPERATOR, tenant_id=None, user_id=None, role=None)

    @classmethod
    def member(cls, *, tenant_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole) -> Principal:
        """A person of ONE business, as verified by ``services.memberships.authenticate_member``."""
        return cls(kind=PrincipalKind.MEMBER, tenant_id=tenant_id, user_id=user_id, role=role)

    @property
    def is_operator(self) -> bool:
        """Whether this is the instance's operator (and not a member of any single business)."""
        return self.kind is PrincipalKind.BOOTSTRAP_OPERATOR


def capabilities_for(role: MemberRole) -> frozenset[Capability]:
    """Every capability ``role`` holds. ==Total, exhaustive, and in ONE place.==

    ``assert_never`` is what makes it total: a fourth role cannot be added without pyright refusing
    the build until somebody decides what it may do. That is the point — the alternative is a role
    that silently holds nothing (its panels come up empty) or one that falls through a default and
    holds everything.
    """
    match role:
        case MemberRole.OWNER:
            # Everything — that is what an owner IS: the person who can hand the business over
            # (members) and change where its money goes (credentials, billing).
            return frozenset(Capability)
        case MemberRole.ADMIN:
            # Runs the business; does not OWN it. Not the members, not the billing (spec §4.3).
            return frozenset(Capability) - {Capability.MANAGE_MEMBERS, Capability.MANAGE_BILLING}
        case MemberRole.MEMBER:
            # Reads, and runs their own bookings. ==Criterion 37 lives here.==
            return frozenset({Capability.VIEW, Capability.MANAGE_OWN_BOOKINGS})
        case _:  # pragma: no cover - unreachable while MemberRole has exactly three members
            assert_never(role)


def has(principal: Principal, capability: Capability) -> bool:
    """Whether ``principal`` holds ``capability``. The operator holds every business-scoped one."""
    if principal.is_operator:
        return True
    if principal.role is None:  # pragma: no cover - a member always carries a role
        return False
    return capability in capabilities_for(principal.role)


def require(principal: Principal, capability: Capability) -> None:
    """Refuse unless ``principal`` holds ``capability``. ==Fail-closed, and out loud.=="""
    if has(principal, capability):
        return
    role = principal.role.value if principal.role is not None else principal.kind.value
    raise PermissionDeniedError(
        f"a '{role}' may not '{capability.value}' in this business.\n"
        "\n"
        "Roles are decided in aethercal.server.services.rbac: owner = everything; admin = "
        "everything but the members and the billing; member = read + their own bookings.\n"
        "\n"
        "Ask an owner of this business to change your role."
    )


def require_operator(principal: Principal) -> None:
    """Refuse anybody but the INSTANCE's operator.

    ==The gate that cannot be a capability.== Its one caller is the business selector (criterion
    38): listing the businesses on the instance, and switching between them. An ``owner`` holds
    every capability *of their own business* and must still be refused here — an owner of Acme who
    could step into Globex is the cross-business escalation the whole batch exists to prevent.
    """
    if principal.is_operator:
        return
    raise PermissionDeniedError(
        "only the instance's operator may list or switch business.\n"
        "\n"
        "A role — even 'owner' — is a role INSIDE one business. Switching to another one is not a "
        "capability any of them can hold: it is what the operator's own credential "
        "(AETHERCAL_ADMIN_USERNAME) is for."
    )


__all__ = [
    "Capability",
    "PermissionDeniedError",
    "Principal",
    "PrincipalKind",
    "capabilities_for",
    "has",
    "require",
    "require_operator",
]
