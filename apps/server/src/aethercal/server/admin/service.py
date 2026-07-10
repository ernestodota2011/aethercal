"""The admin's in-process service layer (F1-11, RF-18).

This is the seam the Reflex state handlers call. It deliberately does NOT go through the HTTP API or
the SDK — it opens a session from the same-process ``async_sessionmaker`` and calls the real
``aethercal.server.services`` functions directly, owning one transaction per action exactly like the
CLI's ``run_*`` coroutines. That keeps the admin fast, avoids a second network hop and a second auth
surface, and lets the whole layer be unit-tested offline against an aiosqlite sessionmaker.

Two error families cross the boundary:

* :class:`AdminSetupError` — the admin is misconfigured for this tenant (no tenant, an ambiguous
  choice with several tenants, an unknown slug, or a tenant with no host user).
* :class:`AdminActionError` — a requested action was refused by the underlying service (unknown
  booking, slot taken, duplicate slug/name, invalid input, ...). Its ``message`` is operator-facing.

Every read/write is scoped to the resolved tenant, so administering tenant A can never see or mutate
tenant B's rows — the service layer's ``tenant_id`` filters do the enforcing; this layer just
resolves the single tenant to pass down.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeCreate, EventTypeRead, EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleRead, ScheduleUpdate
from aethercal.server.db.models import Tenant, User
from aethercal.server.services import bookings as bookings_service
from aethercal.server.services import event_types as event_types_service
from aethercal.server.services import schedules as schedules_service

Sessionmaker = async_sessionmaker[AsyncSession]


# --------------------------------------------------------------------------------------
# Errors.
# --------------------------------------------------------------------------------------


class AdminError(Exception):
    """Base class for admin service-layer errors."""


class AdminSetupError(AdminError):
    """The admin cannot resolve its operating tenant/host (a config problem, not an action)."""


class AdminActionError(AdminError):
    """A requested admin action was refused; ``message`` is a safe, operator-facing explanation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------------------
# Inputs / context.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdminContext:
    """The resolved single-user operating context: which tenant, acting as which host user."""

    tenant_id: uuid.UUID
    host_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EventTypeForm:
    """The admin-controlled fields of an event type; ``host_id`` is injected from the context."""

    slug: str
    title: str
    schedule_id: uuid.UUID
    duration_seconds: int
    max_advance_seconds: int
    description: str | None = None
    # Sparse ``{"en": ...}`` overrides (A4); ``title``/``description`` above stay the canonical
    # (Spanish) text. Empty by default so a create with no EN override stores no translation key.
    title_translations: dict[str, str] = field(default_factory=dict)
    description_translations: dict[str, str] = field(default_factory=dict)
    location: str | None = None
    buffer_before_seconds: int = 0
    buffer_after_seconds: int = 0
    min_notice_seconds: int = 0
    active: bool = True


# --------------------------------------------------------------------------------------
# Context resolution.
# --------------------------------------------------------------------------------------


async def _resolve_tenant(session: AsyncSession, tenant_slug: str | None) -> Tenant:
    """Resolve the operating tenant by slug, or the single tenant when no slug is configured."""
    if tenant_slug is not None:
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise AdminSetupError(f"no tenant with slug {tenant_slug!r}")
        return tenant

    tenants = list(
        (await session.scalars(select(Tenant).order_by(Tenant.created_at, Tenant.id))).all()
    )
    if not tenants:
        raise AdminSetupError("no tenant exists; create one with `aethercal-admin create-tenant`")
    if len(tenants) > 1:
        raise AdminSetupError(
            "multiple tenants exist; set AETHERCAL_ADMIN_TENANT_SLUG to choose one"
        )
    return tenants[0]


async def resolve_admin_context(session: AsyncSession, *, tenant_slug: str | None) -> AdminContext:
    """Resolve the tenant + its host user for the single-user admin (RF-18)."""
    tenant = await _resolve_tenant(session, tenant_slug)
    host = (
        await session.scalars(
            select(User).where(User.tenant_id == tenant.id).order_by(User.created_at, User.id)
        )
    ).first()
    if host is None:
        raise AdminSetupError(f"tenant {tenant.slug!r} has no user to act as host")
    return AdminContext(tenant_id=tenant.id, host_user_id=host.id)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Bookings.
# --------------------------------------------------------------------------------------

_BOOKING_ERROR_MESSAGES: dict[type[bookings_service.BookingError], str] = {
    bookings_service.EventTypeNotFoundError: "Event type not found",
    bookings_service.BookingNotFoundError: "Booking not found",
    bookings_service.AvailabilityUnavailableError: (
        "Host availability is temporarily unavailable; please try again"
    ),
    bookings_service.BookingNotActiveError: "Booking cannot be rescheduled",
    bookings_service.SlotUnavailableError: "That time is no longer available",
}


def _booking_action_error(exc: bookings_service.BookingError) -> AdminActionError:
    """Map a booking-service domain error to a safe, operator-facing :class:`AdminActionError`."""
    for error_type, message in _BOOKING_ERROR_MESSAGES.items():
        if isinstance(exc, error_type):
            return AdminActionError(message)
    return AdminActionError("The booking could not be updated")  # pragma: no cover - defensive


async def list_bookings_view(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    status: BookingStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[BookingRead]:
    """List the tenant's bookings (optionally filtered), as read models for the agenda view."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rows = await bookings_service.list_bookings(
            session,
            tenant_id=ctx.tenant_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        return [BookingRead.model_validate(row) for row in rows]


async def cancel_booking_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    booking_id: uuid.UUID,
    now: datetime | None = None,
) -> BookingRead:
    """Cancel a booking (idempotent), returning the updated read model."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            booking = await bookings_service.cancel_booking(
                session, tenant_id=ctx.tenant_id, booking_id=booking_id, now=_now(now)
            )
        except bookings_service.BookingError as exc:
            raise _booking_action_error(exc) from exc
        await session.refresh(booking)
        return BookingRead.model_validate(booking)


async def reschedule_booking_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    booking_id: uuid.UUID,
    new_start: datetime,
    now: datetime | None = None,
) -> BookingRead:
    """Reschedule a booking to ``new_start``, returning the new confirmed booking's read model."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            booking = await bookings_service.reschedule_booking(
                session,
                tenant_id=ctx.tenant_id,
                booking_id=booking_id,
                new_start=new_start,
                now=_now(now),
            )
        except bookings_service.BookingError as exc:
            raise _booking_action_error(exc) from exc
        await session.refresh(booking)
        return BookingRead.model_validate(booking)


# --------------------------------------------------------------------------------------
# Event types.
# --------------------------------------------------------------------------------------


def _validation_message(exc: ValidationError) -> str:
    """The first pydantic error rendered as a concise ``field: message`` string."""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ())) or "input"
    return f"{field}: {first.get('msg', 'invalid value')}"


async def list_event_types_view(
    maker: Sessionmaker, *, tenant_slug: str | None
) -> list[EventTypeRead]:
    """List all of the tenant's event types (active and inactive)."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rows = await event_types_service.list_event_types(session, tenant_id=ctx.tenant_id)
        return [EventTypeRead.model_validate(row) for row in rows]


async def create_event_type_action(
    maker: Sessionmaker, *, tenant_slug: str | None, form: EventTypeForm
) -> EventTypeRead:
    """Create an event type from ``form``, injecting the host user from the resolved context."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            data = EventTypeCreate(
                host_id=ctx.host_user_id,
                schedule_id=form.schedule_id,
                slug=form.slug,
                title=form.title,
                description=form.description,
                title_translations=form.title_translations,
                description_translations=form.description_translations,
                location=form.location,
                duration_seconds=form.duration_seconds,
                buffer_before_seconds=form.buffer_before_seconds,
                buffer_after_seconds=form.buffer_after_seconds,
                min_notice_seconds=form.min_notice_seconds,
                max_advance_seconds=form.max_advance_seconds,
                active=form.active,
            )
        except ValidationError as exc:
            raise AdminActionError(_validation_message(exc)) from exc
        try:
            row = await event_types_service.create_event_type(
                session, tenant_id=ctx.tenant_id, data=data
            )
        except event_types_service.EventTypeError as exc:
            raise AdminActionError(str(exc)) from exc
        await session.refresh(row)
        return EventTypeRead.model_validate(row)


async def update_event_type_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    event_type_id: uuid.UUID,
    data: EventTypeUpdate,
) -> EventTypeRead:
    """Apply a partial update to an event type; raise if it does not exist for the tenant."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await event_types_service.update_event_type(
                session, tenant_id=ctx.tenant_id, event_type_id=event_type_id, data=data
            )
        except event_types_service.EventTypeError as exc:
            raise AdminActionError(str(exc)) from exc
        if row is None:
            raise AdminActionError("Event type not found")
        await session.refresh(row)
        return EventTypeRead.model_validate(row)


async def deactivate_event_type_action(
    maker: Sessionmaker, *, tenant_slug: str | None, event_type_id: uuid.UUID
) -> bool:
    """Soft-delete an event type (set ``active = False``); return whether it existed."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        return await event_types_service.deactivate_event_type(
            session, tenant_id=ctx.tenant_id, event_type_id=event_type_id
        )


# --------------------------------------------------------------------------------------
# Schedules.
# --------------------------------------------------------------------------------------


async def list_schedules_view(
    maker: Sessionmaker, *, tenant_slug: str | None
) -> list[ScheduleRead]:
    """List the tenant's weekly schedules, as read models."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rows = await schedules_service.list_schedules(session, tenant_id=ctx.tenant_id)
        return [schedules_service.schedule_to_read(row) for row in rows]


async def create_schedule_action(
    maker: Sessionmaker, *, tenant_slug: str | None, data: ScheduleCreate
) -> ScheduleRead:
    """Create a weekly schedule; map name/validation failures to :class:`AdminActionError`."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await schedules_service.create_schedule(
                session, tenant_id=ctx.tenant_id, data=data
            )
        except schedules_service.ScheduleServiceError as exc:
            raise AdminActionError(str(exc)) from exc
        return schedules_service.schedule_to_read(row)


async def update_schedule_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
) -> ScheduleRead:
    """Patch a weekly schedule; raise if it does not exist or the new shape is invalid."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await schedules_service.update_schedule(
                session, tenant_id=ctx.tenant_id, schedule_id=schedule_id, data=data
            )
        except schedules_service.ScheduleServiceError as exc:
            raise AdminActionError(str(exc)) from exc
        return schedules_service.schedule_to_read(row)


async def delete_schedule_action(
    maker: Sessionmaker, *, tenant_slug: str | None, schedule_id: uuid.UUID
) -> None:
    """Delete a weekly schedule (its date overrides cascade); raise if it does not exist."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            await schedules_service.delete_schedule(
                session, tenant_id=ctx.tenant_id, schedule_id=schedule_id
            )
        except schedules_service.ScheduleServiceError as exc:
            raise AdminActionError(str(exc)) from exc


__all__ = [
    "AdminActionError",
    "AdminContext",
    "AdminError",
    "AdminSetupError",
    "EventTypeForm",
    "cancel_booking_action",
    "create_event_type_action",
    "create_schedule_action",
    "deactivate_event_type_action",
    "delete_schedule_action",
    "list_bookings_view",
    "list_event_types_view",
    "list_schedules_view",
    "reschedule_booking_action",
    "resolve_admin_context",
    "update_event_type_action",
    "update_schedule_action",
]
