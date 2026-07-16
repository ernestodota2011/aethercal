"""EventType CRUD service (RF-14): the async, tenant-scoped operations behind the API.

Every query is scoped by ``tenant_id`` — a caller for tenant A can never read or mutate tenant B's
rows. Two invariants are enforced here rather than left to the database alone:

* **slug unique per tenant** — guarded by the ``(tenant_id, slug)`` unique constraint; a violating
  insert/update is caught and re-raised as :class:`DuplicateSlugError` (the flush runs inside a
  SAVEPOINT so the failed row rolls back without poisoning the caller's transaction).
* **references belong to the same tenant** — ``host_id``/``schedule_id`` must point at a ``User`` /
  ``Schedule`` owned by the same tenant (a plain foreign key checks existence, not ownership, and is
  not even enforced under SQLite), else :class:`InvalidReferenceError`.

Transaction control (commit/rollback) belongs to the caller (the ``get_session`` request dependency
or the test session), never to this module. ``to_core_event_type`` bridges a stored row's integer
seconds onto the pure ``aethercal.core`` value objects the slots engine (F1-04) consumes.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import Buffer
from aethercal.core.model import EventType as CoreEventType
from aethercal.schemas.event_types import EventTypeCreate, EventTypeUpdate
from aethercal.server.db.models import EventType, Schedule, User


class EventTypeError(Exception):
    """Base class for EventType service errors the API maps to a clean HTTP status."""


class DuplicateSlugError(EventTypeError):
    """Raised when an EventType slug already exists for the tenant (→ HTTP 409)."""


class InvalidReferenceError(EventTypeError):
    """Raised when a ``host_id``/``schedule_id`` is unknown or not the tenant's (→ HTTP 422)."""


async def _host_belongs(session: AsyncSession, tenant_id: uuid.UUID, host_id: uuid.UUID) -> bool:
    """Return whether ``host_id`` is a ``User`` owned by ``tenant_id``."""
    found = (
        await session.scalars(
            select(User.id).where(User.id == host_id, User.tenant_id == tenant_id)
        )
    ).one_or_none()
    return found is not None


async def _load_schedule(
    session: AsyncSession, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> Schedule | None:
    """The tenant's ``Schedule`` by id, or ``None`` (a plain FK checks existence, not ownership)."""
    return (
        await session.scalars(
            select(Schedule).where(Schedule.id == schedule_id, Schedule.tenant_id == tenant_id)
        )
    ).one_or_none()


async def _require_references(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    host_id: uuid.UUID,
    schedule_id: uuid.UUID,
) -> None:
    """Validate the host/schedule PAIR: both are the tenant's, and the host may use that schedule.

    The ownership check (RF-30) needs both sides at once, which is why this takes the EFFECTIVE pair
    rather than each field on its own: a schedule owned by another host (``Schedule.user_id`` set to
    someone else) is not usable here. Without the check, Ana's event type could quietly run on
    Bruno's weekly pattern — no error, no symptom, until Ana starts taking bookings at Bruno's
    hours. A schedule with ``user_id IS NULL`` is shared by the business and usable by every host.
    """
    if not await _host_belongs(session, tenant_id, host_id):
        raise InvalidReferenceError(f"host {host_id} does not belong to this tenant")
    schedule = await _load_schedule(session, tenant_id, schedule_id)
    if schedule is None:
        raise InvalidReferenceError(f"schedule {schedule_id} does not belong to this tenant")
    if schedule.user_id is not None and schedule.user_id != host_id:
        raise InvalidReferenceError(
            f"schedule {schedule_id} belongs to host {schedule.user_id}, not to host {host_id}; "
            "use one of that host's schedules, or a shared one"
        )


async def create_event_type(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: EventTypeCreate
) -> EventType:
    """Create an EventType for ``tenant_id``. The row is flushed (not committed); caller commits.

    The insert runs inside a SAVEPOINT so a unique-slug violation rolls back only the offending
    row (and re-raises as :class:`DuplicateSlugError`), leaving the caller's transaction intact.
    """
    await _require_references(
        session, tenant_id=tenant_id, host_id=data.host_id, schedule_id=data.schedule_id
    )
    row = EventType(tenant_id=tenant_id, **data.model_dump())
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateSlugError(f"slug '{data.slug}' already exists for this tenant") from exc
    return row


async def get_event_type(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID
) -> EventType | None:
    """Return the tenant's EventType by id, or ``None`` if no such row exists for that tenant."""
    return (
        await session.scalars(
            select(EventType).where(EventType.id == event_type_id, EventType.tenant_id == tenant_id)
        )
    ).one_or_none()


async def get_bookable_event_type(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID
) -> EventType | None:
    """The tenant's EventType — but ONLY if it is still on sale (``active``). The GUEST's lookup.

    .. rubric:: Why this is a new function and not a filter inside :func:`get_event_type`

    ``active = False`` is this product's DELETE (:func:`deactivate_event_type`). The flag was
    written, and read by nobody: ``get_event_type`` matched on ``id`` + ``tenant_id`` alone,
    ``compute_slots`` never mentioned ``active``, and the only reader in the codebase was a display
    column. So "delete this event type" removed it from precisely nothing — the slots endpoint kept
    publishing a full open week for it and the booking endpoint kept accepting bookings for a
    service the business had withdrawn. The public booking page filtered ``e.active`` in memory,
    which is not a defence: ==that is the CLIENT, and a server must never rely on its client to
    enforce what the business decided.== With the booking API going public, that stops being untidy.

    But the fix cannot be "add ``active`` to ``get_event_type``", and that is the point of this
    seam. The OPERATOR must keep seeing inactive rows — to list them, inspect them, and above all to
    REACTIVATE them. Filter the shared lookup and ``deactivate`` becomes a one-way door that hides
    the row from the only person who can undo it (``update_event_type`` reads through that very
    function). Two audiences, two lookups:

    * **Guest paths require ``active``** — ``compute_slots`` (offer nothing) and
      ``create_booking`` / ``reschedule_booking`` (take nothing). They use THIS function.
    * **Operator paths do not** — :func:`get_event_type` / :func:`list_event_types` stay unfiltered.
      Nor does the CANCEL path: a guest holding an appointment on a withdrawn service must still be
      able to cancel it, or deactivating a type would trap them with a booking nobody can undo.

    Returning ``None`` rather than "it exists but is off" is deliberate: the router renders it as
    the same 404 an unknown id gets, so a stranger cannot enumerate which of a business's event
    types have been switched off.
    """
    row = await get_event_type(session, tenant_id=tenant_id, event_type_id=event_type_id)
    return row if row is not None and row.active else None


async def list_event_types(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[EventType]:
    """Return all of the tenant's EventTypes (active and inactive), oldest first.

    ==Deliberately unfiltered — the OPERATOR's list.== The guest's equivalent is
    :func:`get_bookable_event_type`. Do not "tidy up" by adding an ``active`` filter here: the admin
    would lose the ability to see and reactivate a deactivated type
    (``test_the_admin_keeps_its_full_view_of_a_deactivated_event_type`` pins this).
    """
    result = await session.scalars(
        select(EventType)
        .where(EventType.tenant_id == tenant_id)
        .order_by(EventType.created_at, EventType.id)
    )
    return list(result.all())


async def get_bookable_event_type_by_slug(
    session: AsyncSession, *, tenant_id: uuid.UUID, slug: str
) -> EventType | None:
    """The bookable event type a PUBLIC caller named by slug — the second half of the resolver.

    ==The slug is unique per BUSINESS, not globally== (``UniqueConstraint("tenant_id", "slug")`` —
    ``db/models/scheduling.py``). So ``discovery-call`` exists in every business on the instance,
    and
    a lookup by slug alone finds several rows in one table. Whatever such a lookup returned — the
    first, the newest, whichever the planner felt like — would be **somebody's booking filed in
    somebody else's diary**, and, from the payments cut onward, somebody else's money.

    The resolver is therefore the PAIR ``(tenant_slug, event_slug)``: the business is resolved first
    (``services/tenant_resolution.tenant_by_slug`` — ``tenants.slug`` IS globally unique) and bound
    to the session, and only then is this called. Under RLS the query is already confined to that
    business; the explicit ``tenant_id`` predicate stays anyway, because a belt that only holds
    while
    the other belt holds is one belt.

    ``one_or_none`` is the fail-closed half, and it is not decoration: were this ever to see two
    rows, it RAISES rather than picking one. The database says that cannot happen; this says that if
    the database is ever wrong, nobody gets a booking rather than somebody getting a stranger's.

    ``None`` for an unknown slug AND for a deactivated one, deliberately — the router answers both
    with the same 404, so a stranger cannot enumerate which of a business's services are switched
    off.
    """
    row = (
        await session.scalars(
            select(EventType).where(EventType.tenant_id == tenant_id, EventType.slug == slug)
        )
    ).one_or_none()
    return row if row is not None and row.active else None


async def list_bookable_event_types(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[EventType]:
    """The business's event types that are ON SALE, oldest first — the PUBLIC listing.

    Not :func:`list_event_types`, which is the OPERATOR's view and deliberately includes the
    withdrawn ones. The booking page used to receive all of them and filter ``active`` in memory —
    but that is the CLIENT, and a server may never lean on its client to enforce what the business
    decided. With no API key in front of this, "the page filters it" stops being untidy and becomes
    the whole of the protection.
    """
    result = await session.scalars(
        select(EventType)
        .where(EventType.tenant_id == tenant_id, EventType.active.is_(True))
        .order_by(EventType.created_at, EventType.id)
    )
    return list(result.all())


async def update_event_type(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type_id: uuid.UUID,
    data: EventTypeUpdate,
) -> EventType | None:
    """Apply a partial update to the tenant's EventType.

    Returns the updated row, or ``None`` if no such row exists for the tenant (→ HTTP 404). Only the
    fields the caller actually sent are touched; changed references are re-validated and a slug
    collision raises :class:`DuplicateSlugError`.
    """
    row = await get_event_type(session, tenant_id=tenant_id, event_type_id=event_type_id)
    if row is None:
        return None

    fields = data.model_dump(exclude_unset=True)
    # Validate the EFFECTIVE pair (the patch merged onto the row), not just the fields that arrived.
    # Re-hosting an event type without touching its schedule_id is exactly how a host ends up on a
    # weekly pattern owned by someone else: each field is individually valid, the pair is not.
    await _require_references(
        session,
        tenant_id=tenant_id,
        host_id=fields.get("host_id", row.host_id),
        schedule_id=fields.get("schedule_id", row.schedule_id),
    )
    # Capture the target slug now: a failed flush expires ``row`` and reading it later would emit
    # sync IO inside the async context (MissingGreenlet).
    target_slug: str = fields.get("slug", row.slug)
    try:
        async with session.begin_nested():
            for key, value in fields.items():
                setattr(row, key, value)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateSlugError(f"slug '{target_slug}' already exists for this tenant") from exc
    return row


async def deactivate_event_type(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID
) -> bool:
    """Soft-delete the tenant's EventType (set ``active = False``); idempotent.

    Returns ``True`` when a row owned by the tenant was found, ``False`` otherwise.
    """
    row = await get_event_type(session, tenant_id=tenant_id, event_type_id=event_type_id)
    if row is None:
        return False
    row.active = False
    await session.flush()
    return True


def to_core_event_type(row: EventType) -> CoreEventType:
    """Bridge a stored EventType row to the pure ``aethercal.core`` value object (seconds → deltas).

    Consumed by the slots engine (F1-04); only the scheduling parameters cross over — slug, title,
    questions and the like stay in the persistence/API layers.
    """
    increment = (
        timedelta(seconds=row.increment_seconds) if row.increment_seconds is not None else None
    )
    return CoreEventType(
        duration=timedelta(seconds=row.duration_seconds),
        buffer=Buffer(
            before=timedelta(seconds=row.buffer_before_seconds),
            after=timedelta(seconds=row.buffer_after_seconds),
        ),
        increment=increment,
        min_notice=timedelta(seconds=row.min_notice_seconds),
        max_advance=timedelta(seconds=row.max_advance_seconds),
    )


__all__ = [
    "DuplicateSlugError",
    "EventTypeError",
    "InvalidReferenceError",
    "create_event_type",
    "deactivate_event_type",
    "get_bookable_event_type",
    "get_bookable_event_type_by_slug",
    "get_event_type",
    "list_bookable_event_types",
    "list_event_types",
    "to_core_event_type",
    "update_event_type",
]
