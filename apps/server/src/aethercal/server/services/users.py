"""Hosts (RF-30): the ``users`` domain service — and the ONLY thing that writes that table.

A ``users`` row is a HOST: the person an event type is offered against, whose name signs every
message the product sends, whose address every confirmation is copied to, and into whose calendar a
booking is written. It is not an incidental record.

.. rubric:: Why this module exists at all

It did not. The host CRUD lived INLINE in ``admin/service.py``, written straight against the model,
and the CLI wrote a **second** copy of it inside ``create-tenant``. Two write surfaces, each with
its own idea of what a host is — and they had already drifted apart in exactly the way two copies
always do:

* a duplicate address was a clean, operator-facing refusal in the panel, and an unhandled
  ``IntegrityError`` (a traceback) in the CLI;
* **neither of them validated anything.** ``--email "not-an-email" --timezone "America/Mars"``
  created that host, in silence, and the row read back perfectly. Meanwhile the GUEST's equivalents
  are refused at the edge and always have been (``BookingCreate.guest_email`` / ``guest_timezone``,
  "so email/ICS rendering never fails") — the host's are the same two strings, used for the same two
  things, checked by nobody.

So the rules live HERE, once, and both surfaces consume them.
``test_nothing_outside_the_service_constructs_a_user`` asserts the tree keeps it that way: a third
caller writing its own ``User(...)`` is how this started.

.. rubric:: Every refusal below replaces a silence

An unknown timezone raises nothing at write time; it is resolved much later, somewhere else. A
malformed address raises nothing at write time; it is "resolved" by an SMTP server, days later,
against a booking whose guest is waiting to be told the meeting is on. A nameless host signs a real
email with a blank. And a second row for the same person — ``Ana@example.com`` and
``ana@example.com``, which a unique constraint on the exact string cannot see — is a selector
offering two of somebody, an event type landing on whichever was clicked, and mail going to
whichever row is read first.

That last one is the only refusal here the service cannot actually make good on alone: its guard is
a check-then-act, and two concurrent creates walk straight through it. ==So the invariant is the
database's== — a functional unique index on ``(tenant_id, lower(email))``, migration 0007 — and what
this module owes the operator is that the index's refusal reads like the guard's, rather than like a
crash. See :func:`_ensure_email_available` and :func:`_is_duplicate_email`.

Transaction control (commit / rollback) belongs to the caller, as everywhere else in this layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

# The same two rules the GUEST's fields are held to, applied to the HOST's. Imported, never
# re-implemented: a second copy of "what a real timezone is" is exactly how two surfaces come to
# disagree about it. (They were private to the schema; this is the second caller, so they are public
# now — the rule was always about a timezone and an address, not about whose they are.)
from aethercal.schemas.bookings import require_emailish, require_iana_zone
from aethercal.server.db.models import EventType, Schedule, User


# --------------------------------------------------------------------------------------
# Errors — the admin maps them onto operator-facing text; the CLI onto a clean exit.
# --------------------------------------------------------------------------------------
class UserServiceError(Exception):
    """Base class for host-service failures."""


class UserNotFoundError(UserServiceError):
    """No such host in this business — an id/email that is unknown, or is another business's.

    The two are deliberately indistinguishable: every id here arrives from a form or a command-line
    flag, so "that host is not yours" and "that host does not exist" must read the same, or the
    panel becomes an oracle for the neighbouring business's rows.
    """


class DuplicateUserEmailError(UserServiceError):
    """The business already has a host on that address (→ HTTP 409 / a refused form)."""


class InvalidUserError(UserServiceError):
    """The host's own data contradicts what a host IS: no name, no real address, no real zone."""


class UserInUseError(UserServiceError):
    """The host still holds event types or schedules; deleting them would cascade or orphan."""


class AmbiguousUserEmailError(UserServiceError):
    """Two rows, one address, differing only in case — a pair only a pre-0007 write could make.

    ==Never resolved by picking one.== A ``.first()`` over an ambiguous set is the very defect RF-30
    was raised for (a host's second calendar connection, silently dropped).

    Since migration 0007 the database itself cannot hold such a pair — the functional unique index
    on ``(tenant_id, lower(email))`` — and the migration refuses to run against a database that
    already does, naming the rows rather than merging them. This remains the answer for the only
    case left: a database that predates 0007, or one whose index somebody has dropped. It says so,
    loudly, instead of guessing which host was meant.
    """


@dataclass(frozen=True, slots=True)
class UserData:
    """The fields a host is authored with. Both surfaces send all three (there is no PATCH here)."""

    name: str
    email: str
    timezone: str = "UTC"


# --------------------------------------------------------------------------------------
# Validation — ONE copy, and it runs before anything is added to the session.
# --------------------------------------------------------------------------------------
def _clean(data: UserData) -> UserData:
    """Normalise and validate a host, or refuse. Whitespace is stripped; the CASE is preserved.

    Trimming is not cosmetic: a trailing space is invisible in a form and fatal at a lookup —
    ``connect-google --user-email ana@example.com`` never matches ``'ana@example.com '``, and the
    operator is told the host does not exist while the panel lists them happily.

    The case is left exactly as the operator typed it, because the stored value is what a person
    reads. It is MATCHING that must be case-insensitive, and that is where it is done: the
    uniqueness guard below, and :func:`get_user_by_email`.
    """
    name = data.name.strip()
    if not name:
        raise InvalidUserError(
            "a host needs a name: it signs every message this business sends, and labels them in "
            "the host selector"
        )
    try:
        email = require_emailish(data.email)
    except ValueError as exc:
        raise InvalidUserError(
            f"'{data.email}' is not a valid email address — and a host's address is where every "
            "booking confirmation is copied"
        ) from exc
    try:
        timezone = require_iana_zone(data.timezone.strip())
    except ValueError as exc:
        raise InvalidUserError(
            f"'{data.timezone}' is not a real IANA timezone (e.g. 'America/New_York', 'UTC'). "
            "Stored, it is a zone that resolves nowhere: it fails when a message is rendered, not "
            "when it is typed"
        ) from exc
    return UserData(name=name, email=email, timezone=timezone)


async def _ensure_email_available(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Refuse a second host on the same address — ==case-insensitively==.

    ``Ana@example.com`` and ``ana@example.com`` are one person with a typo, and two rows for one
    person is a real defect: the selector offers both, an event type lands on whichever was clicked,
    and mail goes to whichever row is read.

    ==This is NOT what makes the address unique.== A check-then-act cannot be: it reads, finds
    nobody, and writes, and two CONCURRENT creates can each do all three. The invariant is the
    DATABASE's — the functional unique index on ``(tenant_id, lower(email))`` (migration 0007) — and
    this guard exists for the one thing an index cannot do: refuse the operator BEFORE anything is
    written, in a sentence they can act on. When it loses the race, the index refuses the write and
    :func:`_is_duplicate_email` turns that refusal back into this same error, so the two paths are
    indistinguishable from outside.
    """
    stmt = select(User.id).where(
        User.tenant_id == tenant_id, func.lower(User.email) == email.lower()
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    if (await session.scalars(stmt)).first() is not None:
        raise DuplicateUserEmailError(
            f"a host with the email '{email}' already exists in this business: two hosts on one "
            "address is one host with a typo"
        )


# The database's own name for the invariant (migration 0007). It is the ONE unique index on
# ``users``, which is precisely why the exact-string ``UNIQUE`` was substituted rather than kept:
# with two of them, an IntegrityError could arrive under either name and this function would have to
# guess.
_EMAIL_UNIQUE_INDEX = "uq_users_tenant_id_email_lower"


def _is_duplicate_email(exc: IntegrityError) -> bool:
    """Is this the case-insensitive uniqueness index refusing a second host on one address?

    ==Asked, rather than assumed.== A blanket ``except IntegrityError: raise
    DuplicateUserEmailError`` reports "a host with the email 'ana@example.com' already exists" for
    ANY refusal the database makes — a foreign key to a tenant that does not exist, a NOT NULL that
    a future column adds — and sends the operator hunting for a host that was never there. A
    misdiagnosis is worse than a traceback: the traceback at least says it does not know.

    PostgreSQL names the violated constraint in ``Diagnostic.constraint_name``; SQLite carries it in
    the message (``UNIQUE constraint failed: index '...'``). Both are checked, so this holds on the
    backend production runs on AND on the one the offline suite proves it with.
    """
    origin = exc.orig
    named = getattr(getattr(origin, "diag", None), "constraint_name", None)
    if named is not None:
        return bool(named == _EMAIL_UNIQUE_INDEX)
    return _EMAIL_UNIQUE_INDEX in str(origin)


# --------------------------------------------------------------------------------------
# Reads.
# --------------------------------------------------------------------------------------
async def get_user(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    """The business's host by id, or :class:`UserNotFoundError`. Tenant-scoped, always."""
    row = (
        await session.scalars(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    ).one_or_none()
    if row is None:
        raise UserNotFoundError(f"no host {user_id} in this business")
    return row


async def get_user_by_email(session: AsyncSession, *, tenant_id: uuid.UUID, email: str) -> User:
    """The business's host by address — matched case-insensitively, as addresses are.

    A host stored as ``Ana@example.com`` is found by ``ana@example.com``: the operator typing their
    own address into ``connect-google`` should not have to remember how they capitalised it in the
    panel a month ago.
    """
    candidate = email.strip()
    try:
        row = (
            await session.scalars(
                select(User).where(
                    User.tenant_id == tenant_id, func.lower(User.email) == candidate.lower()
                )
            )
        ).one_or_none()
    except MultipleResultsFound as exc:
        raise AmbiguousUserEmailError(
            f"this business has more than one host whose email is '{candidate}' (they differ only "
            "in capitalisation). Refusing to guess which one was meant: delete or re-address the "
            "duplicate first"
        ) from exc
    if row is None:
        raise UserNotFoundError(f"no host with the email '{candidate}' in this business")
    return row


async def list_users(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[User]:
    """The business's hosts, oldest first — the choices a host selector offers."""
    rows = await session.scalars(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at, User.id)
    )
    return list(rows.all())


# --------------------------------------------------------------------------------------
# Writes.
# --------------------------------------------------------------------------------------
async def create_user(session: AsyncSession, *, tenant_id: uuid.UUID, data: UserData) -> User:
    """Add a host to the business. Validated once, here, for every caller."""
    clean = _clean(data)
    await _ensure_email_available(session, tenant_id=tenant_id, email=clean.email)

    row = User(tenant_id=tenant_id, name=clean.name, email=clean.email, timezone=clean.timezone)
    try:
        # A SAVEPOINT, so losing the race with the unique index refuses the ACTION without killing
        # the caller's transaction — the CLI creates the tenant in that same one.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        # ==The failure the guard above cannot prevent, made legible rather than invisible.== The
        # guard read before the winner committed, so it found nobody; the INDEX is what refused this
        # write. The caller gets the SAME error either way — an operator cannot tell (and should not
        # have to care) whether they lost a race or simply typed a taken address.
        if not _is_duplicate_email(exc):
            raise  # not our invariant: somebody else's bug, and it travels intact
        raise DuplicateUserEmailError(
            f"a host with the email '{clean.email}' already exists in this business"
        ) from exc
    return row


async def update_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, data: UserData
) -> User:
    """Edit a host's name / email / timezone. All three are sent, and the create rules apply again.

    ==The edit path is not the way around the create path's guards.== A rule enforced only on the
    way in is a rule a second screen quietly removes.
    """
    row = await get_user(session, tenant_id=tenant_id, user_id=user_id)
    clean = _clean(data)
    await _ensure_email_available(
        session, tenant_id=tenant_id, email=clean.email, exclude_id=row.id
    )

    try:
        # ==The row is mutated INSIDE the SAVEPOINT, and that is not a style choice.==
        #
        # Assigning the new email BEFORE ``begin_nested()`` leaves the row dirty on the way in, so
        # the doomed UPDATE is emitted OUTSIDE the savepoint that exists to contain it — and its
        # ``IntegrityError`` takes the caller's whole transaction down with it. The refusal was
        # still
        # raised, and still read correctly, so the damage surfaced somewhere else entirely: at the
        # NEXT query on that session, as ``PendingRollbackError``, in code that had done nothing
        # wrong. (Invisible until now only because the guard above always won first; the database's
        # refusal — the race — is what actually reaches this line.)
        #
        # Inside, the savepoint owns the write: its rollback undoes the UPDATE *and* restores the
        # object, so a refused edit leaves the host exactly as they were — in the session as well as
        # in the database — and the caller's transaction stays usable. ``create_user`` has always
        # had
        # this shape (its ``session.add`` is inside); this makes the two paths the same.
        async with session.begin_nested():
            row.name = clean.name
            row.email = clean.email
            row.timezone = clean.timezone
            await session.flush()
    except IntegrityError as exc:
        # Rolling the savepoint back EXPIRES the row it had modified — correct (its attributes no
        # longer reflect anything), and a landmine on an async session: the caller still holds that
        # object, and the next plain ``row.email`` on it is a lazy load outside the greenlet, i.e.
        # ``MissingGreenlet`` — an exception about IO plumbing, thrown at whoever merely looked at
        # the host they were told they could not rename. Reloading it here is what makes "the
        # refusal
        # left the host exactly as they were" TRUE of the object as well as of the table.
        await session.refresh(row)
        # Same reasoning as ``create_user``: ONLY the address index is translated.
        if not _is_duplicate_email(exc):
            raise
        raise DuplicateUserEmailError(
            f"a host with the email '{clean.email}' already exists in this business"
        ) from exc
    return row


async def delete_user(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Remove a host — REFUSED while anything of the business still points at them.

    Both silent outcomes are catastrophic, and neither raises anything on its own: let it CASCADE
    and the booking page loses event types nobody asked to remove (and their bookings with them);
    let it ORPHAN and the page keeps offering slots for a host who no longer exists. So the refusal
    names what is holding them, and the operator decides.
    """
    row = await get_user(session, tenant_id=tenant_id, user_id=user_id)

    hosted = (
        await session.scalars(
            select(EventType.slug).where(
                EventType.tenant_id == tenant_id, EventType.host_id == row.id
            )
        )
    ).all()
    if hosted:
        names = ", ".join(f"'{slug}'" for slug in hosted)
        raise UserInUseError(
            f"host '{row.name}' still hosts the event type(s) {names}: deleting them would either "
            "take those event types (and their bookings) with them, or leave the booking page "
            "offering slots for a host who no longer exists. Re-assign or deactivate them first"
        )

    owned = (
        await session.scalars(
            select(Schedule.name).where(Schedule.tenant_id == tenant_id, Schedule.user_id == row.id)
        )
    ).all()
    if owned:
        names = ", ".join(f"'{name}'" for name in owned)
        raise UserInUseError(
            f"host '{row.name}' still owns the schedule(s) {names}. Delete them, or hand them to "
            "the business (clear their owner), first"
        )

    await session.delete(row)
    await session.flush()


__all__ = [
    "AmbiguousUserEmailError",
    "DuplicateUserEmailError",
    "InvalidUserError",
    "UserData",
    "UserInUseError",
    "UserNotFoundError",
    "UserServiceError",
    "create_user",
    "delete_user",
    "get_user",
    "get_user_by_email",
    "list_users",
    "update_user",
]
