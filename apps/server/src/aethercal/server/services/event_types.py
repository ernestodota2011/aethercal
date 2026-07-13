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


async def list_event_types(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[EventType]:
    """Return all of the tenant's EventTypes (active and inactive), oldest first."""
    result = await session.scalars(
        select(EventType)
        .where(EventType.tenant_id == tenant_id)
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
    "get_event_type",
    "list_event_types",
    "to_core_event_type",
    "update_event_type",
]
