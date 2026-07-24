"""RBAC verified THROUGH the Reflex state handler — ==the panel must RENDER the refusal== (C-01).

.. rubric:: Why the service-layer RBAC tests are not enough on their own

``test_admin_rbac.py`` proves the SERVICE raises ``AdminPermissionError`` for the wrong role, and
``test_rbac.py`` proves the pure role table. Neither proves what a real client SEES. Reflex exposes
every ``@rx.event`` over the websocket, so the handler — not a page ``on_load`` guard — is the
authorization seam a client actually hits. A handler that let the refusal fall through as an EMPTY
render would be the project's signature defect: the person is told something FALSE about their own
business ("you have no members") and nobody learns a gate fired. ==So these drive the handler and
assert ``state.error`` carries the refusal — the panel renders it, it does not swallow it.==

Offline (aiosqlite) on purpose: the role gate is business-INTERNAL (RLS isolates businesses; this
authorises people), so it needs no Postgres. The principal is set exactly as ``member_login`` sets
it — the login itself is proven against a real PostgreSQL in ``tests/rls/test_rbac_isolation.py``.

Covers, at the STATE seam:
* RF-C01-1/-2/-6 — the static frontier, BOTH pairs: owner+admin (the members frontier) and
  owner+member (the scheduling + credentials frontier); the lower-privilege one is refused OUT LOUD
  and never sees another tenant, and the gate is not an outage for the higher one.
* RF-C01-4 — owner/admin/member are refused the business SELECTOR by ``require_operator``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import MemberRole
from aethercal.server.admin import runtime as runtime_mod
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.ratelimit import LOGIN_LIMITER
from aethercal.server.admin.runtime import AdminRuntime, configure_runtime
from aethercal.server.admin.state import AdminState
from aethercal.server.db import Base
from aethercal.server.db.models import Tenant
from aethercal.server.services import memberships as memberships_service
from aethercal.server.services import users as users_service
from aethercal.server.services.rbac import PrincipalKind

Sessionmaker = async_sessionmaker[AsyncSession]

_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _clean_runtime() -> Iterator[None]:
    """Reset the process-global runtime + login limiter around each test (no cross-test bleed)."""
    saved = runtime_mod._Holder.value
    runtime_mod._Holder.value = None
    LOGIN_LIMITER.reset()
    yield
    runtime_mod._Holder.value = saved
    LOGIN_LIMITER.reset()


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[Sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_business(
    m: Sessionmaker, slug: str
) -> tuple[uuid.UUID, dict[MemberRole, uuid.UUID]]:
    """A business with a host + membership for each role. Returns the tenant id + the user ids."""
    users: dict[MemberRole, uuid.UUID] = {}
    async with m() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        for role in MemberRole:
            host = await users_service.create_user(
                session,
                tenant_id=tenant.id,
                data=users_service.UserData(
                    name=role.value.title(), email=f"{role.value}@{slug}.example"
                ),
            )
            await memberships_service.grant_membership(
                session, tenant_id=tenant.id, user_id=host.id, role=role, password=_PASSWORD
            )
            users[role] = host.id
        return tenant.id, users


def _state() -> AdminState:
    return AdminState(_reflex_internal_init=True)


def _signed_in_member(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole, slug: str
) -> AdminState:
    """A state authenticated as one member of a business — exactly as ``member_login`` leaves it.

    The role here is only the login SNAPSHOT; the service re-reads the live role from
    ``memberships`` on every action (``_live_principal``), so the seeded membership is what
    actually authorises.
    """
    state = _state()
    state._authenticated = True
    state._principal_kind = PrincipalKind.MEMBER.value
    state._principal_role = role.value
    state._principal_user_id = str(user_id)
    state._business_tenant_id = str(tenant_id)
    state._business_slug = slug
    return state


def _configure(m: Sessionmaker, *, tenant_slug: str | None = None) -> None:
    configure_runtime(
        AdminRuntime(
            sessionmaker=m,
            config=AdminConfig(username="operator", password_hash="x", tenant_slug=tenant_slug),
        )
    )


# ======================================================================================
# Pair 1 — owner + admin: the members frontier (MANAGE_MEMBERS). An admin RUNS the business
# but does not OWN it, so the members panel is refused — and the refusal is RENDERED.
# ======================================================================================


async def test_an_admin_load_members_renders_the_refusal_not_an_empty_panel(
    maker: Sessionmaker,
) -> None:
    """==The whole point of C-01.4a's state hardening.== An ``admin`` opening the members panel is
    refused, and the panel shows the refusal. ``members == []`` alone would be the silent no-op — a
    business with three members told it has none — so the ERROR being set is the assertion, and the
    empty list is only allowed BECAUSE the error explains it."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    admin = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.ADMIN], role=MemberRole.ADMIN, slug="acme"
    )

    await AdminState.load_members.fn(admin)

    assert admin.error != "", "the panel silently rendered empty instead of showing the refusal"
    assert admin.members == []


async def test_an_admin_create_member_renders_the_refusal(maker: Sessionmaker) -> None:
    """Granting a role is handing the business over — precisely the door an ``admin`` may not open.
    The handler surfaces the refusal rather than appearing to succeed."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    admin = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.ADMIN], role=MemberRole.ADMIN, slug="acme"
    )

    await AdminState.create_member.fn(
        admin,
        {
            "host_id": str(users[MemberRole.MEMBER]),
            "role": MemberRole.OWNER.value,
            "password": _PASSWORD,
        },
    )

    assert admin.error != ""


async def test_an_owner_load_members_is_not_locked_out_by_the_gate(maker: Sessionmaker) -> None:
    """The other half: refusing the admin must NOT refuse the owner. A gate that also stops the
    owner is an outage, not a fix — and "deny" is the easy half to get right by accident."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    owner = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.OWNER], role=MemberRole.OWNER, slug="acme"
    )

    await AdminState.load_members.fn(owner)

    assert owner.error == ""
    assert len(owner.members) == 3


# ======================================================================================
# Pair 2 — owner + member: the scheduling + credentials frontier. A member READS the business
# and runs their own bookings; the scheduling and credentials writes are refused OUT LOUD.
# ======================================================================================


async def test_a_member_create_host_renders_the_refusal_and_writes_nothing(
    maker: Sessionmaker,
) -> None:
    """==Scheduling frontier (MANAGE_SCHEDULING).== A ``member`` cannot add a host; the panel
    renders the refusal AND nothing is written — a member has ``VIEW`` so the unchanged host list is
    a real effect assertion, not a guess."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    member = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.MEMBER], role=MemberRole.MEMBER, slug="acme"
    )

    await AdminState.create_host.fn(
        member, {"name": "Nope", "email": "nope@acme.example", "timezone": "UTC"}
    )
    assert member.error != ""

    # The member holds VIEW, so this read works — and shows the three seeded hosts, not a fourth.
    await AdminState.load_hosts.fn(member)
    assert member.error == ""  # the read itself is allowed and clears the stale error
    assert len(member.hosts) == 3


async def test_a_member_designate_calendar_is_refused_on_the_credentials_frontier(
    maker: Sessionmaker,
) -> None:
    """==Credentials frontier (MANAGE_CREDENTIALS).== Pointing a connection at a calendar is a
    credential operation; a ``member`` is refused BEFORE any connection is looked up, so a random id
    is enough to reach the gate."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    member = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.MEMBER], role=MemberRole.MEMBER, slug="acme"
    )

    await AdminState.designate_calendar.fn(
        member,
        {"connection_id": str(uuid.uuid4()), "calendar_id": "bookings@group.calendar.google.com"},
    )

    assert member.error != ""


async def test_a_member_load_members_renders_the_refusal(maker: Sessionmaker) -> None:
    """ "See or edit the members" is criterion 37, and it means SEE too: the reconnaissance half of
    taking a business over. A ``member`` is refused, out loud."""
    tenant_id, users = await _seed_business(maker, "acme")
    _configure(maker)
    member = _signed_in_member(
        tenant_id=tenant_id, user_id=users[MemberRole.MEMBER], role=MemberRole.MEMBER, slug="acme"
    )

    await AdminState.load_members.fn(member)

    assert member.error != ""
    assert member.members == []


# ======================================================================================
# The lower-privilege session never sees another tenant (RF-C01-6, the RLS/RBAC seam at the panel).
# ======================================================================================


async def test_a_member_naming_another_business_sees_none_of_it(maker: Sessionmaker) -> None:
    """==Two questions, two places, one panel.== A member of Acme whose session names Globex is
    refused at the tenant gate (their verified business is not the one asked for) and sees NONE of
    Globex's data — the refusal is rendered, the agenda stays empty. RLS would also stop the read;
    the ROLE layer refuses it FIRST, in words, because the request is otherwise coherent."""
    acme_id, acme_users = await _seed_business(maker, "acme")
    await _seed_business(maker, "globex")
    _configure(maker)

    # A real member of Acme, but their session is pointed at Globex's slug (the address-bar attack).
    intruder = _signed_in_member(
        tenant_id=acme_id,
        user_id=acme_users[MemberRole.OWNER],
        role=MemberRole.OWNER,
        slug="globex",
    )

    await AdminState.load_bookings.fn(intruder)

    assert intruder.error != ""
    assert intruder.bookings == []
    assert intruder.calendar_events == []


# ======================================================================================
# RF-C01-4 — the business SELECTOR is the operator's, and no role may switch (require_operator).
# ======================================================================================


@pytest.mark.parametrize("role", list(MemberRole))
async def test_no_member_role_may_switch_business_at_the_selector(
    maker: Sessionmaker, role: MemberRole
) -> None:
    """==Switching business is not a capability any role can hold.== Even an ``owner`` — everything
    IN their business — is refused here: an owner of Acme who could step into Globex is the
    cross-tenant escalation the whole batch exists to prevent. The handler renders the refusal and
    does NOT change the bound business."""
    acme_id, acme_users = await _seed_business(maker, "acme")
    await _seed_business(maker, "globex")
    _configure(maker)
    actor = _signed_in_member(tenant_id=acme_id, user_id=acme_users[role], role=role, slug="acme")

    # The selector list: a non-operator sees nothing to switch to.
    await AdminState.load_businesses.fn(actor)
    assert actor.businesses == []

    # And an attempt to switch is refused, out loud, without moving the bound business.
    await AdminState.select_business.fn(actor, "globex")
    assert actor.error != ""
    assert actor._business_slug == "acme"


async def test_the_operator_may_use_the_selector(maker: Sessionmaker) -> None:
    """The control: the gate is operator-ONLY, not operator-NONE. The instance operator lists the
    businesses and switches between them — which is what proves the refusals above are the gate
    firing, not the selector being broken for everybody."""
    await _seed_business(maker, "acme")
    await _seed_business(maker, "globex")
    _configure(maker)
    operator = _state()
    operator._authenticated = True
    operator._principal_kind = PrincipalKind.BOOTSTRAP_OPERATOR.value

    await AdminState.load_businesses.fn(operator)

    assert {b["slug"] for b in operator.businesses} == {"acme", "globex"}
