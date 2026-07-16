"""The role gates on the admin service layer — ==criterion 37==, and the lock that keeps it (B-02).

.. rubric:: Why the gate cannot be the database's job

B-01 put a belt on every scoped table, and the belt compares ``tenant_id``. A ``member`` of Acme
deleting Acme's hosts, listing Acme's calendar credentials, or removing Acme's owner is touching
rows that carry **Acme's** ``tenant_id`` — so every policy says yes, and says it silently. ==RLS
isolates BUSINESSES. It does not authorise PEOPLE.== That is this layer's job, and these are its
tests.

.. rubric:: The structural test is the one that matters

Auditing today's twenty-odd panels buys nothing if tomorrow's opens a session and forgets to ask.
:func:`test_every_panel_that_opens_a_session_authorises_first` asserts it about the TREE — the same
lock ``test_admin_session_belt`` puts on "no panel opens an unbelted session", and for the same
reason: a rule that is only written down is a rule that is eventually skipped, and skipping THIS one
hands a business's members panel to somebody who was only ever meant to see their own agenda.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus, MemberRole
from aethercal.server.admin import service
from aethercal.server.admin import service as service_module
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.db import Base
from aethercal.server.db.guc import reset_tenant_binding
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.services import memberships as memberships_service
from aethercal.server.services import users as users_service
from aethercal.server.services.rbac import Principal

Sessionmaker = async_sessionmaker[AsyncSession]

_PASSWORD = "correct horse battery staple"
_SERVICE_MODULE = Path(service_module.__file__).resolve()

#: The helpers a session-owning panel may authorise with. Named here so a future rename is a
#: decision somebody makes in a diff, rather than a test that quietly stops asserting anything.
_AUTHORIZERS = frozenset({"_authorize", "_authorize_booking", "_authorize_event_type"})


@pytest.fixture(autouse=True)
def _clean_binding() -> Iterator[None]:
    reset_tenant_binding()
    yield
    reset_tenant_binding()


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class _Business:
    """One seeded business, and a principal for each of its three roles."""

    slug: str
    tenant_id: uuid.UUID
    owner: Principal
    admin: Principal
    member: Principal
    member_booking_id: uuid.UUID
    other_booking_id: uuid.UUID

    def __init__(self, slug: str) -> None:
        self.slug = slug


async def _seed(maker: Sessionmaker, slug: str = "acme") -> _Business:
    """A business with an owner, an admin and a member — plus a booking hosted by EACH of two.

    Two bookings, because ``member`` does not mean "may touch no booking": it means "may touch THEIR
    OWN". A fixture with one booking cannot tell a working row-level check from a blanket denial.
    """
    business = _Business(slug)
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        business.tenant_id = tenant.id

        principals: dict[MemberRole, Principal] = {}
        hosts: dict[MemberRole, User] = {}
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
            hosts[role] = host
            principals[role] = Principal.member(tenant_id=tenant.id, user_id=host.id, role=role)

        business.owner = principals[MemberRole.OWNER]
        business.admin = principals[MemberRole.ADMIN]
        business.member = principals[MemberRole.MEMBER]

        schedule = Schedule(
            tenant_id=tenant.id,
            user_id=hosts[MemberRole.MEMBER].id,
            name="Default",
            timezone="UTC",
            rules={},
        )
        session.add(schedule)
        await session.flush()

        for role, suffix in ((MemberRole.MEMBER, "mine"), (MemberRole.OWNER, "theirs")):
            event_type = EventType(
                tenant_id=tenant.id,
                host_id=hosts[role].id,
                schedule_id=schedule.id,
                slug=f"call-{suffix}",
                title=f"Call ({suffix})",
                duration_seconds=1800,
                max_advance_seconds=60 * 60 * 24 * 60,
            )
            session.add(event_type)
            await session.flush()
            start = datetime.now(UTC) + timedelta(days=1 if role is MemberRole.MEMBER else 2)
            booking = Booking(
                tenant_id=tenant.id,
                event_type_id=event_type.id,
                start_at=start,
                end_at=start + timedelta(minutes=30),
                status=BookingStatus.CONFIRMED,
                guest_name="Guest",
                guest_email="guest@example.com",
                guest_timezone="UTC",
                ical_uid=f"{uuid.uuid4()}@aethercal.test",
            )
            session.add(booking)
            await session.flush()
            if role is MemberRole.MEMBER:
                business.member_booking_id = booking.id
            else:
                business.other_booking_id = booking.id
    return business


def _runtime(maker: Sessionmaker) -> AdminRuntime:
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="operator", password_hash="x", tenant_slug=None),
    )


# ======================================================================================
# ==CRITERION 37== — a `member` may see neither the members nor the credentials.
# ======================================================================================


async def test_criterion_37_a_member_cannot_see_the_members(sessionmaker: Sessionmaker) -> None:
    """==Refused, not filtered.== An empty list would tell them something false about their own
    business, and would tell nobody at all that a gate had fired."""
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    with pytest.raises(service.AdminPermissionError):
        await service.list_members_view(admin, principal=business.member, tenant_slug=business.slug)


async def test_criterion_37_a_member_cannot_edit_the_members(sessionmaker: Sessionmaker) -> None:
    """Granting a role is handing the business over — the one thing an ``admin`` may not do either.

    Both doors are tested, because closing one closes neither: whoever holds a ``member`` session
    does not care WHICH handler is willing to make them an owner.
    """
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    for principal in (business.member, business.admin):
        with pytest.raises(service.AdminPermissionError):
            await service.create_member_action(
                admin,
                principal=principal,
                tenant_slug=business.slug,
                form=service.MemberForm(
                    host_id=uuid.uuid4(), role=MemberRole.OWNER, password=_PASSWORD
                ),
            )
        with pytest.raises(service.AdminPermissionError):
            await service.delete_member_action(
                admin, principal=principal, tenant_slug=business.slug, membership_id=uuid.uuid4()
            )


async def test_criterion_37_a_member_cannot_see_the_businesss_credentials(
    sessionmaker: Sessionmaker,
) -> None:
    """==A connected calendar IS a credential== — an OAuth grant on a real Google account that the
    business acts through. It sits behind ``MANAGE_CREDENTIALS`` together with everything B-03 will
    add there, so ``member`` is refused today rather than on the day that panel grows."""
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    with pytest.raises(service.AdminPermissionError):
        await service.list_connections_view(
            admin, principal=business.member, tenant_slug=business.slug, host_id=uuid.uuid4()
        )


async def test_an_owner_manages_the_members_and_an_admin_runs_the_business(
    sessionmaker: Sessionmaker,
) -> None:
    """The other half of criterion 37: the gate refuses the member ==without locking anybody else
    out==. A gate that also stops the owner is not a gate, it is an outage — and it is the failure
    this wave is most likely to ship, because "deny" is the easy half.
    """
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    listed = await service.list_members_view(
        admin, principal=business.owner, tenant_slug=business.slug
    )
    assert sorted(row.role for row in listed) == sorted(MemberRole)

    # And the ADMIN — refused the members — still runs the business day to day.
    hosts = await service.list_hosts_view(
        admin, principal=business.admin, tenant_slug=business.slug
    )
    assert len(hosts) == 3
    await service.create_host_action(
        admin,
        principal=business.admin,
        tenant_slug=business.slug,
        form=service.HostForm(name="New Host", email="new@acme.example", timezone="UTC"),
    )


async def test_a_member_reads_the_agenda_and_is_refused_the_scheduling_writes(
    sessionmaker: Sessionmaker,
) -> None:
    """``member`` = read + their own bookings. The READ has to work: a role that can do nothing at
    all is not a role, it is a broken login."""
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    assert await service.list_bookings_view(
        admin, principal=business.member, tenant_slug=business.slug
    )
    assert await service.list_hosts_view(
        admin, principal=business.member, tenant_slug=business.slug
    )

    with pytest.raises(service.AdminPermissionError):
        await service.create_host_action(
            admin,
            principal=business.member,
            tenant_slug=business.slug,
            form=service.HostForm(name="Nope", email="nope@acme.example", timezone="UTC"),
        )
    with pytest.raises(service.AdminPermissionError):
        await service.delete_host_action(
            admin, principal=business.member, tenant_slug=business.slug, host_id=uuid.uuid4()
        )


# ======================================================================================
# "Their own bookings" — the half a capability cannot express.
# ======================================================================================


async def test_a_member_may_cancel_the_booking_they_host(sessionmaker: Sessionmaker) -> None:
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    cancelled = await service.cancel_booking_action(
        admin,
        principal=business.member,
        tenant_slug=business.slug,
        booking_id=business.member_booking_id,
    )
    assert cancelled.status is BookingStatus.CANCELLED


async def test_a_member_may_not_cancel_a_booking_somebody_else_hosts(
    sessionmaker: Sessionmaker,
) -> None:
    """==The row-level half of ``MANAGE_OWN_BOOKINGS``.== The capability says "your own"; only a
    query against the booking's HOST can say which ones those are — and if nobody asks, "your own"
    quietly comes to mean "anybody's"."""
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    with pytest.raises(service.AdminPermissionError):
        await service.cancel_booking_action(
            admin,
            principal=business.member,
            tenant_slug=business.slug,
            booking_id=business.other_booking_id,
        )


async def test_an_admin_may_cancel_anybodys_booking(sessionmaker: Sessionmaker) -> None:
    """``MANAGE_SCHEDULING`` is exactly "any booking in this business" — the front desk's job."""
    business = await _seed(sessionmaker)
    admin = _runtime(sessionmaker)

    cancelled = await service.cancel_booking_action(
        admin,
        principal=business.admin,
        tenant_slug=business.slug,
        booking_id=business.other_booking_id,
    )
    assert cancelled.status is BookingStatus.CANCELLED


# ======================================================================================
# The business a member ASKS for is never the business they get.
# ======================================================================================


async def test_a_member_cannot_act_on_another_business_by_naming_its_slug(
    sessionmaker: Sessionmaker,
) -> None:
    """==The attack the whole batch exists to stop, in one line.==

    ``tenant_slug`` comes from the client. The principal's ``tenant_id`` comes from the server-side
    membership check at login. When the two disagree, the SERVER's answer wins and the action is
    refused — before a session is ever opened for the business that was asked for.
    """
    acme = await _seed(sessionmaker, "acme")
    globex = await _seed(sessionmaker, "globex")
    admin = _runtime(sessionmaker)

    # An OWNER of Acme — the most powerful role there is — is still nobody at all in Globex.
    with pytest.raises(service.AdminPermissionError):
        await service.list_bookings_view(admin, principal=acme.owner, tenant_slug=globex.slug)
    with pytest.raises(service.AdminPermissionError):
        await service.list_members_view(admin, principal=acme.owner, tenant_slug=globex.slug)


async def test_the_instance_operator_administers_any_business(sessionmaker: Sessionmaker) -> None:
    """The bootstrap operator is a member of nothing and administers everything — which is what the
    business SELECTOR (criterion 38) is for."""
    acme = await _seed(sessionmaker, "acme")
    globex = await _seed(sessionmaker, "globex")
    admin = _runtime(sessionmaker)
    operator = Principal.bootstrap_operator()

    assert await service.list_members_view(admin, principal=operator, tenant_slug=acme.slug)
    assert await service.list_members_view(admin, principal=operator, tenant_slug=globex.slug)


# ======================================================================================
# ==The lock: no panel reaches the data without asking.==
# ======================================================================================


def _session_owning_panels() -> list[ast.AsyncFunctionDef]:
    """Every public async function in ``admin/service.py`` that opens an admin session."""
    found: list[ast.AsyncFunctionDef] = []
    for node in ast.parse(_SERVICE_MODULE.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.AsyncFunctionDef) or node.name.startswith("_"):
            continue
        opens = any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "admin_session"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
        if opens:
            found.append(node)
    return found


def test_the_panels_are_actually_found_by_the_structural_tests() -> None:
    """A tree-walking assertion that matches NOTHING passes for ever. This is the canary for the two
    below: if a refactor moves the panels somewhere this parser cannot see, they must go red here —
    not go green having checked an empty list."""
    assert len(_session_owning_panels()) > 15


def test_every_panel_that_opens_a_session_authorises_first() -> None:
    """==The lock on the cause, not on its symptoms.==

    Every test above is worth exactly as much as this one. Gating today's panels buys nothing if the
    twenty-ninth opens ``admin_session`` and simply does not ask: it would type-check, it would run,
    and it would serve a ``member`` whatever it had. So the fact is asserted about the TREE — inside
    ``admin/service.py``, a function that opens a session calls an authoriser, or this goes red.
    """
    offenders = sorted(
        node.name
        for node in _session_owning_panels()
        if not (
            {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            & _AUTHORIZERS
        )
    )
    assert offenders == []


def test_every_panel_takes_the_principal_and_never_defaults_it() -> None:
    """A panel cannot authorise what it was never told. The principal is a REQUIRED keyword — never
    a default, because ==a default IS the silent hole==: "forgot to pass it" would come to mean
    "full power", and it would come to mean it without an error anywhere."""
    offenders: list[str] = []
    for node in _session_owning_panels():
        keywords = {arg.arg for arg in node.args.kwonlyargs}
        defaulted = {
            arg.arg
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
            if default is not None
        }
        if "principal" not in keywords or "principal" in defaulted:
            offenders.append(node.name)
    assert offenders == []
