"""Hosts (``users``) — the domain service, and the fact that it is the ONLY thing that writes them.

Until now there was no ``services/users.py`` at all. The CRUD lived INLINE in ``admin/service.py``,
against the model, and the CLI wrote a second copy of it inside ``create-tenant``. Two write
surfaces, each with its own idea of what a host is — which is exactly how one of them ends up
enforcing a guard the other has never heard of.

They already disagreed. A duplicate address was a clean refusal in the panel and an unhandled
``IntegrityError`` in the CLI. And NEITHER of them checked the two fields that matter: the email
every confirmation is CC'd to, and the timezone the host is displayed in — both of which the
GUEST's equivalents (``BookingCreate.guest_email`` / ``guest_timezone``) have been validated at the
edge since the first booking. A host could be created with ``"not-an-email"`` in ``"America/Mars"``
and nothing raised: the row saved, the panel read it back, and the first symptom would have been a
message that never arrived.

So the tests below assert the EFFECTIVE state — the row that was actually written, the row a refusal
left unchanged, the tree that contains no second writer — never the apparent one.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.schemas.event_types import EventTypeCreate
from aethercal.schemas.schedules import ScheduleCreate, TimeRangeSchema
from aethercal.server.cli import run_create_tenant
from aethercal.server.db.models import EventType, Schedule, Tenant, User
from aethercal.server.services import users
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.schedules import create_schedule
from aethercal.server.services.users import (
    DuplicateUserEmailError,
    InvalidUserError,
    UserData,
    UserInUseError,
    UserNotFoundError,
    create_user,
    delete_user,
    get_user,
    get_user_by_email,
    list_users,
    update_user,
)

_WEEKLY_9_TO_5 = {day: [TimeRangeSchema(start="09:00", end="17:00")] for day in range(5)}
_MAX_ADVANCE = 60 * 60 * 24 * 30


async def _tenant(session: AsyncSession, *, slug: str = "acme") -> Tenant:
    tenant = Tenant(slug=slug, name=slug.title())
    session.add(tenant)
    await session.flush()
    return tenant


def _ana(*, name: str = "Ana", email: str = "ana@example.com", timezone: str = "UTC") -> UserData:
    return UserData(name=name, email=email, timezone=timezone)


async def _count_users(session: AsyncSession, tenant: Tenant) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
        )
    ) or 0


# --------------------------------------------------------------------------------------
# Create / list / read.
# --------------------------------------------------------------------------------------


async def test_a_host_is_written_with_the_data_the_operator_gave(
    sqlite_session: AsyncSession,
) -> None:
    tenant = await _tenant(sqlite_session)

    created = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    row = await sqlite_session.get(User, created.id)
    assert row is not None
    assert (row.tenant_id, row.name, row.email, row.timezone) == (
        tenant.id,
        "Ana",
        "ana@example.com",
        "UTC",
    )


async def test_the_stored_email_is_trimmed(sqlite_session: AsyncSession) -> None:
    """==Asserted on the ROW, not on the argument.==

    A trailing space is invisible in a form and fatal at a lookup: ``connect-google --user-email
    ana@example.com`` matches ``'ana@example.com '`` never, so the operator is told that host does
    not exist while the panel lists them happily.
    """
    tenant = await _tenant(sqlite_session)

    created = await create_user(
        sqlite_session, tenant_id=tenant.id, data=_ana(name=" Ana ", email="  ana@example.com  ")
    )

    row = await sqlite_session.get(User, created.id)
    assert row is not None
    assert row.email == "ana@example.com"
    assert row.name == "Ana"


async def test_hosts_are_listed_and_only_this_businesss(sqlite_session: AsyncSession) -> None:
    """Asserted as a SET, deliberately.

    The order is ``(created_at, id)`` — the panel's own, moved here unchanged — and ``created_at``
    is a second-resolution server default, so two hosts added in the same second tie and fall back
    to a random UUID. Asserting a sequence here would be asserting the tie-break, which is a test
    that passes for the wrong reason and fails on a slow machine. What is load-bearing is WHICH
    hosts come back: the neighbouring business's must not.
    """
    acme = await _tenant(sqlite_session, slug="acme")
    other = await _tenant(sqlite_session, slug="other")
    ana = await create_user(sqlite_session, tenant_id=acme.id, data=_ana())
    bruno = await create_user(
        sqlite_session, tenant_id=acme.id, data=_ana(name="Bruno", email="bruno@example.com")
    )
    await create_user(
        sqlite_session, tenant_id=other.id, data=_ana(name="Beto", email="beto@example.com")
    )

    listed = await list_users(sqlite_session, tenant_id=acme.id)

    assert {row.id for row in listed} == {ana.id, bruno.id}


async def test_a_host_of_another_tenant_is_not_found(sqlite_session: AsyncSession) -> None:
    """Not "forbidden" — NOT FOUND. Every id here arrives from a form or a flag, so an unscoped read
    is a cross-tenant write surface one line later."""
    acme = await _tenant(sqlite_session, slug="acme")
    other = await _tenant(sqlite_session, slug="other")
    beto = await create_user(
        sqlite_session, tenant_id=other.id, data=_ana(name="Beto", email="beto@example.com")
    )

    with pytest.raises(UserNotFoundError):
        await get_user(sqlite_session, tenant_id=acme.id, user_id=beto.id)
    with pytest.raises(UserNotFoundError):
        await get_user_by_email(sqlite_session, tenant_id=acme.id, email="beto@example.com")


async def test_get_user_by_email_finds_the_tenants_host(sqlite_session: AsyncSession) -> None:
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    found = await get_user_by_email(sqlite_session, tenant_id=tenant.id, email="ana@example.com")

    assert found.id == ana.id


# --------------------------------------------------------------------------------------
# The validation — ONE copy of it, and every refusal writes nothing.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email",
    ["not-an-email", "", "   ", "ana@", "@example.com", "ana example@x.com", "a@b@c.com"],
)
async def test_an_email_that_is_not_an_email_is_refused_and_nothing_is_written(
    sqlite_session: AsyncSession, email: str
) -> None:
    """The host's address is where every confirmation is CC'd. The GUEST's has been checked at the
    edge since the first booking; the host's was checked nowhere, on either surface."""
    tenant = await _tenant(sqlite_session)

    with pytest.raises(InvalidUserError):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(email=email))

    assert await _count_users(sqlite_session, tenant) == 0


@pytest.mark.parametrize("timezone", ["America/Mars", "Europe/Madird", "", "not a zone"])
async def test_an_unknown_timezone_is_refused_and_nothing_is_written(
    sqlite_session: AsyncSession, timezone: str
) -> None:
    """``Europe/Madird`` is a typo a human makes, and it used to store SILENTLY: nothing resolved
    the zone at write time. The guest's timezone is refused at the edge ("so email/ICS rendering
    never fails" — ``schemas/bookings.py``); the host's is the same string, for the same reason."""
    tenant = await _tenant(sqlite_session)

    with pytest.raises(InvalidUserError):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(timezone=timezone))

    assert await _count_users(sqlite_session, tenant) == 0


@pytest.mark.parametrize("name", ["", "   "])
async def test_a_nameless_host_is_refused(sqlite_session: AsyncSession, name: str) -> None:
    """The host's name signs every email the product sends and labels them in the selector. Blank,
    it is a host nobody can pick and a signature nobody wrote."""
    tenant = await _tenant(sqlite_session)

    with pytest.raises(InvalidUserError):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(name=name))

    assert await _count_users(sqlite_session, tenant) == 0


async def test_a_duplicate_email_is_refused(sqlite_session: AsyncSession) -> None:
    tenant = await _tenant(sqlite_session)
    await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    with pytest.raises(DuplicateUserEmailError):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(name="Ana again"))

    assert await _count_users(sqlite_session, tenant) == 1


async def test_a_duplicate_email_that_differs_only_in_case_is_refused(
    sqlite_session: AsyncSession,
) -> None:
    """==The typo an exact-string unique constraint cannot see.==

    ``Ana@example.com`` and ``ana@example.com`` were two hosts as far as the database was concerned
    — and one human being. Two rows for one person means a selector offering both, an event type
    landing on whichever was clicked, and mail going to whichever row happens to be read.

    The guard below is the OPERATOR-facing half of the fix: it refuses before anything is written,
    with a sentence a person can act on. The half it cannot supply — an invariant that holds under
    concurrency — is migration 0006's, and is proven in the three tests that follow.
    """
    tenant = await _tenant(sqlite_session)
    await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    with pytest.raises(DuplicateUserEmailError):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(email="Ana@Example.com"))

    assert await _count_users(sqlite_session, tenant) == 1


# --------------------------------------------------------------------------------------
# The invariant is the DATABASE's; the service only makes its refusal legible (0006).
# --------------------------------------------------------------------------------------


async def test_the_database_refuses_a_case_variant_pair_with_no_service_in_the_way(
    sqlite_session: AsyncSession,
) -> None:
    """==The guard is a courtesy. THIS is the invariant.==

    Written straight against the model, with nothing between the write and the table — which is, in
    effect, exactly where two concurrent creates end up: each one's check-then-act has already read,
    found nobody, and moved on. What refuses the second row here is the functional unique index on
    ``(tenant_id, lower(email))``, and nothing else.
    """
    tenant = await _tenant(sqlite_session)
    sqlite_session.add(
        User(tenant_id=tenant.id, name="Ana", email="Ana@example.com", timezone="UTC")
    )
    await sqlite_session.flush()

    sqlite_session.add(
        User(tenant_id=tenant.id, name="Ana Ruiz", email="ana@example.com", timezone="UTC")
    )
    with pytest.raises(IntegrityError):
        await sqlite_session.flush()


async def test_a_refusal_only_the_database_could_make_is_still_the_domain_error(
    sqlite_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the guard cannot win, arriving at the caller as a SENTENCE rather than a traceback.

    Blinding the guard is precisely what a concurrent create does to it: the other transaction has
    not committed, so there is nothing for the read to find, and the write goes ahead. The database
    then refuses it — and the operator must get "that address is already taken", not
    ``IntegrityError: UNIQUE constraint failed: index 'uq_users_tenant_id_email_lower'``, which
    tells them nothing they can act on and reads as a crash.

    (The real, concurrent version of this is ``test_users_concurrency.py``, on PostgreSQL. SQLite
    serialises writers, so the race itself cannot be staged here — only its outcome.)
    """
    tenant = await _tenant(sqlite_session)
    await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    async def _blind(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(users, "_ensure_email_available", _blind)

    with pytest.raises(DuplicateUserEmailError, match="already exists"):
        await create_user(sqlite_session, tenant_id=tenant.id, data=_ana(email="Ana@Example.com"))

    assert await _count_users(sqlite_session, tenant) == 1


async def test_an_edit_that_loses_the_race_is_the_domain_error_too(
    sqlite_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The edit path is not the way around the create path's guarantees — and never was.

    ==And the refusal leaves NOTHING half-written — not even in memory.==

    This is where a real defect was hiding. ``update_user`` assigned the new address to the row
    BEFORE opening the SAVEPOINT, so the doomed ``UPDATE`` was emitted OUTSIDE the savepoint meant
    to
    contain it: the refusal was raised and read perfectly, and the caller's transaction was dead.
    The
    damage then surfaced somewhere else entirely — at the next query on that session, as a
    ``PendingRollbackError``, in code that had done nothing wrong. Nobody had ever seen it because
    the guard always won first; the DATABASE's refusal (i.e. the race this whole change is about) is
    what actually reaches that line.

    So the assertions below are deliberately about what happens AFTER the refusal: the session still
    works, the row still reads as it did, and the count is what it was.
    """
    tenant = await _tenant(sqlite_session)
    await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())
    bruno = await create_user(
        sqlite_session, tenant_id=tenant.id, data=_ana(name="Bruno", email="bruno@example.com")
    )

    async def _blind(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(users, "_ensure_email_available", _blind)

    with pytest.raises(DuplicateUserEmailError, match="already exists"):
        await update_user(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=bruno.id,
            data=_ana(name="Bruno", email="ANA@example.com"),
        )

    # The session is ALIVE (this query is what used to raise PendingRollbackError)...
    assert await _count_users(sqlite_session, tenant) == 2
    # ...and Bruno is untouched — the rejected address did not survive on the object either.
    row = await sqlite_session.get(User, bruno.id)
    assert row is not None
    assert row.email == "bruno@example.com"


async def test_the_same_email_in_another_tenant_is_perfectly_fine(
    sqlite_session: AsyncSession,
) -> None:
    """Uniqueness is per BUSINESS. One person may host for two of them: separate rows, separate
    hosts — the constraint is ``(tenant_id, email)``, never ``email``."""
    acme = await _tenant(sqlite_session, slug="acme")
    other = await _tenant(sqlite_session, slug="other")

    await create_user(sqlite_session, tenant_id=acme.id, data=_ana())
    await create_user(sqlite_session, tenant_id=other.id, data=_ana())

    assert await _count_users(sqlite_session, acme) == 1
    assert await _count_users(sqlite_session, other) == 1


# --------------------------------------------------------------------------------------
# Update.
# --------------------------------------------------------------------------------------


async def test_editing_a_host_rewrites_the_row(sqlite_session: AsyncSession) -> None:
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    await update_user(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=ana.id,
        data=_ana(name="Ana Ruiz", timezone="Europe/Madrid"),
    )

    row = await sqlite_session.get(User, ana.id)
    assert row is not None
    assert (row.name, row.timezone) == ("Ana Ruiz", "Europe/Madrid")


async def test_an_edit_to_a_taken_email_is_refused_and_the_row_is_unchanged(
    sqlite_session: AsyncSession,
) -> None:
    """A refusal is worth nothing if it leaves the row half-written. Asserted by re-reading it."""
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())
    bruno = await create_user(
        sqlite_session, tenant_id=tenant.id, data=_ana(name="Bruno", email="bruno@example.com")
    )

    with pytest.raises(DuplicateUserEmailError):
        await update_user(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=bruno.id,
            data=_ana(name="Bruno", email="ana@example.com"),
        )

    row = await sqlite_session.get(User, bruno.id)
    assert row is not None
    assert row.email == "bruno@example.com"
    assert (await sqlite_session.get(User, ana.id)) is not None


async def test_an_edit_that_keeps_the_hosts_own_email_is_not_a_duplicate(
    sqlite_session: AsyncSession,
) -> None:
    """The own-goal of a naive uniqueness check: a host who changes only their NAME collides with
    themselves, and can never be edited again."""
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    updated = await update_user(
        sqlite_session, tenant_id=tenant.id, user_id=ana.id, data=_ana(name="Ana Ruiz")
    )

    assert updated.name == "Ana Ruiz"


async def test_an_edit_to_an_invalid_timezone_is_refused(sqlite_session: AsyncSession) -> None:
    """The edit path is not the way around the create path's guard."""
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    with pytest.raises(InvalidUserError):
        await update_user(
            sqlite_session, tenant_id=tenant.id, user_id=ana.id, data=_ana(timezone="America/Mars")
        )

    row = await sqlite_session.get(User, ana.id)
    assert row is not None
    assert row.timezone == "UTC"


async def test_editing_an_unknown_host_is_an_error_not_a_silent_no_op(
    sqlite_session: AsyncSession,
) -> None:
    tenant = await _tenant(sqlite_session)

    with pytest.raises(UserNotFoundError):
        await update_user(sqlite_session, tenant_id=tenant.id, user_id=uuid.uuid4(), data=_ana())


# --------------------------------------------------------------------------------------
# Delete — refused while anything of the business still points at the host.
# --------------------------------------------------------------------------------------


async def test_deleting_a_host_who_still_hosts_an_event_type_is_refused(
    sqlite_session: AsyncSession,
) -> None:
    """Both silent outcomes are catastrophic: CASCADE and the booking page loses event types (and
    their bookings) nobody asked to remove; ORPHAN and it keeps offering slots for a host who no
    longer exists. So the refusal names what holds them, and the row survives."""
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())
    schedule = await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=ana.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )

    with pytest.raises(UserInUseError, match="event type"):
        await delete_user(sqlite_session, tenant_id=tenant.id, user_id=ana.id)

    assert (await sqlite_session.get(User, ana.id)) is not None
    assert list((await sqlite_session.scalars(select(EventType.id))).all()) != []


async def test_deleting_a_host_who_still_owns_a_schedule_is_refused(
    sqlite_session: AsyncSession,
) -> None:
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())
    await create_schedule(
        sqlite_session,
        tenant_id=tenant.id,
        data=ScheduleCreate(
            name="Ana's hours", timezone="UTC", rules=_WEEKLY_9_TO_5, user_id=ana.id
        ),
    )

    with pytest.raises(UserInUseError, match="schedule"):
        await delete_user(sqlite_session, tenant_id=tenant.id, user_id=ana.id)

    assert (await sqlite_session.get(User, ana.id)) is not None
    assert list((await sqlite_session.scalars(select(Schedule.id))).all()) != []


async def test_a_free_host_is_deleted(sqlite_session: AsyncSession) -> None:
    tenant = await _tenant(sqlite_session)
    ana = await create_user(sqlite_session, tenant_id=tenant.id, data=_ana())

    await delete_user(sqlite_session, tenant_id=tenant.id, user_id=ana.id)

    assert await _count_users(sqlite_session, tenant) == 0


async def test_deleting_an_unknown_host_is_an_error_not_a_silent_success(
    sqlite_session: AsyncSession,
) -> None:
    tenant = await _tenant(sqlite_session)

    with pytest.raises(UserNotFoundError):
        await delete_user(sqlite_session, tenant_id=tenant.id, user_id=uuid.uuid4())


# --------------------------------------------------------------------------------------
# The CLI is a CONSUMER of this service, not a second implementation of it.
# --------------------------------------------------------------------------------------


async def test_create_tenant_writes_its_first_host_through_the_service(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id = await run_create_tenant(
        sqlite_maker,
        slug="acme",
        name="Acme Inc",
        email="  host@acme.test ",
        timezone="America/New_York",
    )

    async with sqlite_maker() as session:
        row = await session.get(User, user_id)
        assert row is not None
        assert row.tenant_id == tenant_id
        assert row.email == "host@acme.test"  # trimmed by the service, not by the caller
        assert row.timezone == "America/New_York"


@pytest.mark.parametrize(
    ("email", "timezone"),
    [("not-an-email", "UTC"), ("host@acme.test", "America/Mars")],
)
async def test_create_tenant_refuses_the_host_the_admin_would_refuse(
    sqlite_maker: async_sessionmaker[AsyncSession], email: str, timezone: str
) -> None:
    """==The divergence, stated as a test.==

    The two surfaces validated differently because they WERE two implementations: the CLI took
    whatever it was handed, so ``--email not-an-email --timezone America/Mars`` created that host in
    silence. One service, one answer — and the transaction leaves no half-made tenant behind either.
    """
    with pytest.raises(InvalidUserError):
        await run_create_tenant(
            sqlite_maker, slug="acme", name="Acme Inc", email=email, timezone=timezone
        )

    async with sqlite_maker() as session:
        assert list((await session.scalars(select(Tenant.id))).all()) == []
        assert list((await session.scalars(select(User.id))).all()) == []


# --------------------------------------------------------------------------------------
# The lock: ONE writer.
# --------------------------------------------------------------------------------------


def test_nothing_outside_the_service_constructs_a_user() -> None:
    """==The lock on the cause, not on its symptoms.==

    Every test above is worth exactly as much as this one. Promoting the CRUD to a service buys
    nothing if the next caller writes a ``users`` row of their own, against the model, with its own
    idea of what a host is — that is precisely how the panel and the CLI came to disagree.

    So the fact is asserted about the TREE: inside ``aethercal.server``, ``User`` is a class to
    query and to read; it is CONSTRUCTED in exactly one module, this service. (Test fixtures build
    rows directly on purpose — the rule is about the product, not about the harness.)
    """
    home = Path(users.__file__).resolve()
    server_root = home.parent.parent
    offenders = sorted(
        str(path.relative_to(server_root))
        for path in server_root.rglob("*.py")
        if path.resolve() != home
        # ``class User(...)`` in the model module is the DEFINITION, not a write.
        and re.search(r"(?<!class )\bUser\(", path.read_text(encoding="utf-8"))
    )
    assert offenders == []
