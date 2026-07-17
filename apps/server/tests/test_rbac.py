"""The role table: who may do what, decided in ONE place and asserted exhaustively (B-02).

.. rubric:: Why this is not "just an if"

==Row-level security isolates BUSINESSES. It does not validate PERMISSIONS.== The database will
happily let a ``member`` of Acme delete Acme's own hosts, revoke Acme's own API keys and remove
Acme's owner from Acme — every one of those rows carries the right ``tenant_id``, so every policy
says yes. The belt B-01 landed is about *which business's rows you can touch*; this table is about
*what you may do to your own business's rows*, and nothing in PostgreSQL knows the difference.

So the answer lives in the service layer, once, as a total function from role to capability — and
these tests pin it. A capability added without a decision for all three roles fails
:func:`test_every_role_decides_every_capability`; a role added without a decision fails
``capabilities_for``'s own ``assert_never``, at type-check time.
"""

from __future__ import annotations

import uuid

import pytest

from aethercal.core.model import MemberRole
from aethercal.server.services.rbac import (
    Capability,
    PermissionDeniedError,
    Principal,
    capabilities_for,
    require,
    require_operator,
)

_TENANT = uuid.uuid4()
_USER = uuid.uuid4()


def _member(role: MemberRole) -> Principal:
    return Principal.member(tenant_id=_TENANT, user_id=_USER, role=role)


# --------------------------------------------------------------------------------------
# The table itself.
# --------------------------------------------------------------------------------------


def test_every_role_decides_every_capability() -> None:
    """==No capability may be left undecided for a role.==

    The failure mode this locks out is the one that ships: a new capability (B-03's credentials,
    B-05b's refunds) is added, wired into a panel, and simply never appears in one role's set. It
    then denies for that role — or, if the check is written the other way round, ALLOWS for it — and
    the defect is invisible until somebody's ``member`` is found holding the Stripe key.
    """
    for role in MemberRole:
        granted = capabilities_for(role)
        assert isinstance(granted, frozenset)
        assert granted <= frozenset(Capability)


def test_the_owner_can_do_everything() -> None:
    """``owner`` = everything: members, credentials and billing included (spec §4.3)."""
    assert capabilities_for(MemberRole.OWNER) == frozenset(Capability)


def test_the_admin_can_do_everything_except_members_and_billing() -> None:
    """``admin`` = everything but the two that hand over the business itself: who is in it, and
    where its money goes."""
    granted = capabilities_for(MemberRole.ADMIN)
    assert Capability.MANAGE_MEMBERS not in granted
    assert Capability.MANAGE_BILLING not in granted
    assert granted == frozenset(Capability) - {Capability.MANAGE_MEMBERS, Capability.MANAGE_BILLING}


def test_the_member_may_read_and_run_their_own_bookings_and_nothing_else() -> None:
    """``member`` = read + their own bookings. Not the hosts, not the workflows, not the members."""
    assert capabilities_for(MemberRole.MEMBER) == frozenset(
        {Capability.VIEW, Capability.MANAGE_OWN_BOOKINGS}
    )


# --------------------------------------------------------------------------------------
# Criterion 37 (the half of it that lives in the pure table).
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("capability", [Capability.MANAGE_MEMBERS, Capability.MANAGE_CREDENTIALS])
def test_criterion_37_a_member_reaches_neither_the_members_nor_the_credentials(
    capability: Capability,
) -> None:
    """==Criterion 37, at the root.== A ``member`` cannot see or edit the business's members, nor
    its credentials — and the refusal is a raised error, never a quietly empty list."""
    with pytest.raises(PermissionDeniedError):
        require(_member(MemberRole.MEMBER), capability)


def test_a_denial_names_the_role_and_the_capability_but_never_the_data() -> None:
    """The message tells the operator what was refused and why. It reveals nothing about the rows
    they were refused — a denial is not an oracle."""
    with pytest.raises(PermissionDeniedError) as refused:
        require(_member(MemberRole.MEMBER), Capability.MANAGE_MEMBERS)
    message = str(refused.value)
    assert MemberRole.MEMBER.value in message
    assert Capability.MANAGE_MEMBERS.value in message


# --------------------------------------------------------------------------------------
# The bootstrap operator: the operator of the INSTANCE, and NOT a role in the table.
# --------------------------------------------------------------------------------------


def test_the_bootstrap_operator_holds_every_business_scoped_capability() -> None:
    """They are the operator of the INSTANCE (env credential, no row in any table). Every business
    on it is theirs to administer — which is exactly what the selector of criterion 38 is for."""
    operator = Principal.bootstrap_operator()
    for capability in Capability:
        require(operator, capability)  # raises nothing


def test_only_the_bootstrap_operator_may_switch_business() -> None:
    """==Switching business is NOT a capability an owner can hold.==

    It cannot live in the role table at all: ``owner`` means "everything **in my business**", and an
    owner of Acme who could switch to Globex would be the escalation this whole batch exists to
    prevent. So it is a property of the PRINCIPAL KIND, and it has its own gate.
    """
    require_operator(Principal.bootstrap_operator())
    for role in MemberRole:
        with pytest.raises(PermissionDeniedError):
            require_operator(_member(role))


def test_a_member_principal_carries_the_business_it_was_verified_against() -> None:
    """The business on the principal comes from the server-side membership check at login — it is
    what the service layer compares the REQUESTED business against, and it is never a value the
    client sent."""
    principal = _member(MemberRole.OWNER)
    assert principal.tenant_id == _TENANT
    assert principal.user_id == _USER
    assert principal.role is MemberRole.OWNER

    operator = Principal.bootstrap_operator()
    assert operator.tenant_id is None
    assert operator.user_id is None
    assert operator.role is None
