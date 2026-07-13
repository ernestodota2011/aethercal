"""The admin's in-process service layer (F1-11, RF-18).

This is the seam the Reflex state handlers call. It deliberately does NOT go through the HTTP API or
the SDK — it opens a session from the same-process ``async_sessionmaker`` and calls the real
``aethercal.server.services`` functions directly, owning one transaction per action exactly like the
CLI's ``run_*`` coroutines. That keeps the admin fast, avoids a second network hop and a second auth
surface, and lets the whole layer be unit-tested offline against an aiosqlite sessionmaker.

Two error families cross the boundary:

* :class:`AdminSetupError` — the admin is misconfigured for this tenant (no tenant, an ambiguous
  choice with several tenants, or an unknown slug).
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeCreate, EventTypeRead, EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleRead, ScheduleUpdate
from aethercal.schemas.workflows import (
    WorkflowCreate,
    WorkflowRead,
    WorkflowTemplateCreate,
    WorkflowTemplateRead,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
)
from aethercal.server.db.models import (
    Booking,
    ExternalCalendarLink,
    ExternalConnection,
    Outbox,
    OutboxStatus,
    Tenant,
    User,
)
from aethercal.server.db.models.booking import held_filter
from aethercal.server.db.models.outbox import due_filter
from aethercal.server.services import bookings as bookings_service
from aethercal.server.services import calendars as calendars_service
from aethercal.server.services import event_types as event_types_service
from aethercal.server.services import schedules as schedules_service
from aethercal.server.services import users as users_service
from aethercal.server.services import workflow_rules as workflow_rules_service

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
    """The resolved operating context: WHICH TENANT the admin is administering. That is all.

    ==It used to carry a ``host_user_id`` too, and that field was the whole of the RF-30 defect.==
    It was resolved by taking the tenant's FIRST user and injected as the host of every event type
    the admin created — while the form had no host field at all. A business with two hosts therefore
    watched every event type it authored land on whichever host happened to exist first, silently.

    The field is gone rather than fixed, because there is no correct value for it: the host is a
    CHOICE, and a choice belongs on the form (:class:`EventTypeForm`), not in a context that guesses
    it. Leaving a "default host" here would just keep the trap loaded for the next caller.
    """

    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class HostForm:
    """The admin-controlled fields of a host (a ``users`` row): who they are and where they work."""

    name: str
    email: str
    timezone: str


@dataclass(frozen=True, slots=True)
class HostRead:
    """A host as the panel lists them (a flat read model — the ORM row never leaves the session)."""

    id: uuid.UUID
    name: str
    email: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ConnectionRead:
    """One of a host's connected calendar accounts, and where its bookings are written.

    ``booking_calendar_id`` is ``None`` when nothing has been designated: the account's default
    calendar is then used, which is the zero-config path. ==It is NOT the same as "no connection"==
    — a host with no connected account has no row here at all.
    """

    id: uuid.UUID
    account_email: str
    booking_calendar_id: str | None


@dataclass(frozen=True, slots=True)
class BookingForm:
    """The admin-controlled fields for a manually-created booking (F2-F, range-select → create).

    The operator books a slot on a guest's behalf; the tenant/host come from the resolved context,
    and ``end`` is server-derived from the event type's duration (never sent). ``guest_timezone``
    defaults to UTC (the admin's time contract), so a minimal create form need only pick the event
    type + start and name the guest.
    """

    event_type_id: uuid.UUID
    start: datetime
    guest_name: str
    guest_email: str
    guest_timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class EventTypeForm:
    """The admin-controlled fields of an event type.

    ``host_id`` is EXPLICIT (RF-30). It used to be injected from the context — the tenant's first
    user — which is why a second host was unreachable from this panel. It is now whichever host the
    operator picked, and the event-type service checks that the host is theirs and that the schedule
    is one that host may actually use.
    """

    host_id: uuid.UUID
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
    """Resolve the tenant the admin is administering (RF-18).

    It no longer resolves a host. It used to end in ``.first()`` over the tenant's users and hand
    that back as "the" host, which silently decided RF-30 for the operator every time they created
    an event type. The host is now a field on the form, checked against the tenant by the
    event-type service — so there is nothing left here to guess.
    """
    tenant = await _resolve_tenant(session, tenant_slug)
    return AdminContext(tenant_id=tenant.id)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Bookings.
# --------------------------------------------------------------------------------------

_BOOKING_ERROR_MESSAGES: dict[type[bookings_service.BookingError], str | None] = {
    bookings_service.EventTypeNotFoundError: "Event type not found",
    bookings_service.BookingNotFoundError: "Booking not found",
    bookings_service.AvailabilityUnavailableError: (
        "Host availability is temporarily unavailable; please try again"
    ),
    bookings_service.SlotUnavailableError: "That time is no longer available",
    # ``None`` = the SERVICE's own message IS the operator-facing one. These two are refusals of the
    # booking STATE MACHINE, and the same type is raised by SEVERAL operations: ``mark_no_show``
    # raises ``BookingNotActiveError`` exactly as ``reschedule_booking`` does. One hard-coded
    # sentence therefore has to be wrong for every caller but one — this map used to answer "Booking
    # cannot be rescheduled" to an operator who had clicked NO-SHOW and never asked to reschedule.
    # Only the service knows WHICH operation it refused, and it already words its refusal for a
    # human ("only a confirmed booking can be marked a no-show"), so that message is passed through
    # instead of being replaced by a guess. ``BookingNotEndedError`` was mapped nowhere at all and
    # fell through to the catch-all below, which names no cause whatsoever.
    bookings_service.BookingNotActiveError: None,
    bookings_service.BookingNotEndedError: None,
}
"""Every :class:`~aethercal.server.services.bookings.BookingError`, mapped to what the operator is
told. ``test_every_booking_error_has_an_operator_message`` asserts this map stays EXHAUSTIVE over
the service's error tree, so a new subclass fails a test instead of silently inheriting a vague
catch-all that names no cause."""


def _booking_action_error(exc: bookings_service.BookingError) -> AdminActionError:
    """Map a booking-service domain error to a safe, operator-facing :class:`AdminActionError`.

    Resolved along the exception's MRO rather than by dict iteration order, so a future subclass of
    a mapped error deterministically inherits its parent's wording instead of depending on where it
    happened to be inserted.
    """
    for error_type in type(exc).__mro__:
        if error_type in _BOOKING_ERROR_MESSAGES:
            return AdminActionError(_BOOKING_ERROR_MESSAGES[error_type] or str(exc))
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


async def mark_no_show_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    booking_id: uuid.UUID,
    now: datetime | None = None,
) -> BookingRead:
    """Mark a finished appointment as a no-show (RF-25). Idempotent.

    ==It does NOT free the slot.== The appointment time has passed: releasing it would corrupt the
    history and let a booking be written retroactively over it. ``Booking.occupies`` is "not
    cancelled", so ``no_show`` keeps its slot automatically — and the partial index that enforces it
    needed no change at all.

    Refused unless the booking is CONFIRMED and has ENDED. Both refusals reach the operator in the
    service's own words (see :data:`_BOOKING_ERROR_MESSAGES`): a no-show allowed before the end
    would be a cancellation by another name that does not give the time back.
    """
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            booking = await bookings_service.mark_no_show(
                session, tenant_id=ctx.tenant_id, booking_id=booking_id, now=_now(now)
            )
        except bookings_service.BookingError as exc:
            raise _booking_action_error(exc) from exc
        await session.refresh(booking)
        return BookingRead.model_validate(booking)


async def create_booking_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    form: BookingForm,
    now: datetime | None = None,
) -> BookingRead:
    """Create a booking for ``form``'s slot on the guest's behalf (F2-F range-select → create).

    Thin reuse of the SAME domain ``bookings_service.create_booking`` the public booking page uses —
    no new booking logic. The event type + slot are validated against the host's real availability
    (an off-hours or taken slot maps to a safe :class:`AdminActionError`), and ``end`` is derived
    from the event type's duration inside the service.
    """
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            booking = await bookings_service.create_booking(
                session,
                tenant_id=ctx.tenant_id,
                params=bookings_service.BookingParams(
                    event_type_id=form.event_type_id,
                    start=form.start,
                    guest_name=form.guest_name,
                    guest_email=form.guest_email,
                    guest_timezone=form.guest_timezone,
                ),
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
                host_id=form.host_id,
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


# --------------------------------------------------------------------------------------
# The health panel (RF-25 / R9) — THIS business's outbox and no-show rate.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdminMetrics:
    """One read of the operating state of ONE business.

    .. rubric:: Why this is not :func:`~aethercal.server.observability.collect_metrics`

    That snapshot is INSTANCE-WIDE on purpose — "no tenant id, no slug; not in a label, not in a
    value" — because it feeds ``GET /metrics``, the OPERATOR's view, guarded by an operator token
    precisely so that one business's API key can never read the numbers of all of them.

    The admin is the mirror image: it is scoped to ONE tenant, and this module's contract is that
    administering tenant A can never see tenant B's rows. Rendering the instance-wide snapshot in a
    tenant's panel would hand that business the pipeline volume of every other business on the box —
    the very leak ``/metrics`` is locked down to prevent, walked back in through the front door.

    So the facts are the same and the query is not: every count below carries a ``tenant_id``. What
    is NOT re-typed is the VOCABULARY — :class:`OutboxStatus` and :func:`due_filter` are imported,
    because the drain WRITES those states and this COUNTS them, and a backlog gauge that counts a
    status nobody writes any more reports a reassuring ``0`` for ever.

    .. rubric:: What is deliberately absent

    ``lost`` and ``voided_midflight`` are PROCESS-local drain counters with no tenant dimension at
    all. Shown on a tenant's panel they would present instance-wide numbers as if they were this
    business's — apparent state, not effective state, which is the one thing this batch is about.
    They stay where they mean something: the operator's ``/metrics``.
    """

    outbox_by_status: dict[str, int]
    """Every member of :class:`OutboxStatus`, whether or not it has rows. ==Absent and zero must
    never look the same== — nobody can alert on a series that does not exist, and "no dead intents"
    is not the same news as "we stopped counting dead intents"."""
    outbox_due: int
    """Intents whose send time has PASSED and which are still undelivered. ==The alertable one.==

    Not ``pending``: the outbox doubles as the durable scheduler, so a 24 h reminder for a booking
    three weeks out sits ``pending`` for three weeks and is in perfect health. A panel that called
    that backlog would make a healthy business look sick, and the operator would learn to ignore
    the number that was supposed to warn them."""
    outbox_oldest_due_age_seconds: float
    """How long the oldest DUE intent has been waiting. ==The dead-man switch.== Flat on a healthy
    instance; unbounded growth from the moment nothing drains — which is the failure nobody sees,
    because the bookings keep confirming and only the messages stop."""
    bookings_by_status: dict[str, int]
    appointments_expected: int
    """The appointments that ALREADY SHOULD HAVE HAPPENED — the denominator of the no-show rate, and
    the reason it is not simply "no-show + confirmed" (see ``held_filter``, which owns the rule).

    ``CONFIRMED`` is every booking still IN THE DIARY, including every one nobody has attended yet
    because it has not happened. Counting those made the rate FALL every time a booking was taken:
    a business with one real no-show and ninety-nine appointments next week read **1 %** when the
    truth was **100 %**, and a reminder rule that did not work would have looked like a success on
    any week the diary filled up.

    Published ALONGSIDE the ratio rather than hidden inside it, so the panel can say what the
    percentage is a percentage of. A rate with no visible denominator is a number an operator has to
    take on trust — and this is the one they trusted."""
    no_show_ratio: float
    """No-shows over :attr:`appointments_expected`.

    ==Cancelled bookings are not in the denominator.== Nobody was ever expected to attend them, and
    counting them would make a host's rate improve simply because more people cancelled.

    A confirmed booking whose hour has PASSED counts as attended: nobody marked the guest absent,
    and silence from a host who was in the room is the only evidence there is."""


def _age_seconds(moment: datetime | None, *, now: datetime) -> float:
    """Seconds since ``moment``, never negative; ``None`` — nothing is due — is ``0``.

    SQLite hands timestamps back naive, so it is normalised before the arithmetic: subtracting a
    naive from an aware datetime raises, and a health panel that crashes during an incident is worse
    than no health panel at all.
    """
    if moment is None:
        return 0.0
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return max((now - aware).total_seconds(), 0.0)


async def metrics_view(
    maker: Sessionmaker, *, tenant_slug: str | None, now: datetime | None = None
) -> AdminMetrics:
    """Read this business's operational state: the outbox backlog and the no-show rate (RF-25/R9).

    This is what makes a dead scheduler visible to the person who would otherwise never find out:
    today, if the drain dies, every booking still confirms, every intent is still queued, and no
    guest ever hears from the system again — in silence.
    """
    moment = _now(now)
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)

        by_status = {status.value: 0 for status in OutboxStatus}
        for status, count in await session.execute(
            select(Outbox.status, func.count())
            .where(Outbox.tenant_id == ctx.tenant_id)
            .group_by(Outbox.status)
        ):
            if status not in by_status:
                # A status the table holds but the enum does not know is a real divergence, not a
                # rounding error — and silently dropping those rows would make the backlog read
                # reassuringly low. It is the operator's ``/metrics`` that logs it loudly; the panel
                # simply refuses to pretend it counted them.
                continue
            by_status[status] = count

        due = due_filter(moment)
        outbox_due = (
            await session.scalar(
                select(func.count())
                .select_from(Outbox)
                .where(Outbox.tenant_id == ctx.tenant_id, due)
            )
        ) or 0
        due_at = func.coalesce(Outbox.next_retry_at, Outbox.created_at)
        oldest_due = await session.scalar(
            select(due_at)
            .where(Outbox.tenant_id == ctx.tenant_id, due)
            .order_by(due_at.asc())
            .limit(1)
        )

        bookings = {status.value: 0 for status in BookingStatus}
        for status, count in await session.execute(
            select(Booking.status, func.count())
            .where(Booking.tenant_id == ctx.tenant_id)
            .group_by(Booking.status)
        ):
            bookings[BookingStatus(status).value] = count

        # The appointments that already SHOULD have happened — NOT "no_show + confirmed", which is
        # what this counted before and what put every booking still in the diary into the
        # denominator. ``held_filter`` owns the rule (this panel and the operator's Prometheus gauge
        # both publish this rate, and separately they had already drifted into the same error).
        held = held_filter(moment)
        expected = (
            await session.scalar(
                select(func.count())
                .select_from(Booking)
                .where(Booking.tenant_id == ctx.tenant_id, held)
            )
        ) or 0
        absent = (
            await session.scalar(
                select(func.count())
                .select_from(Booking)
                .where(
                    Booking.tenant_id == ctx.tenant_id,
                    held,
                    Booking.status == BookingStatus.NO_SHOW.value,
                )
            )
        ) or 0
        return AdminMetrics(
            outbox_by_status=by_status,
            outbox_due=outbox_due,
            outbox_oldest_due_age_seconds=_age_seconds(oldest_due, now=moment),
            bookings_by_status=bookings,
            appointments_expected=expected,
            no_show_ratio=(absent / expected) if expected else 0.0,
        )


# --------------------------------------------------------------------------------------
# Hosts, and where a host's bookings are written (RF-30).
# --------------------------------------------------------------------------------------
#
# Hosts are ``users`` rows, and every mutation of one goes through ``services/users`` — which is now
# the ONLY thing in the product that writes that table.
#
# ==It used to be written here, inline, against the model== — and the CLI wrote a second copy of the
# same CRUD inside ``create-tenant``. Two surfaces, two ideas of what a host is, and they had
# already diverged: a duplicate address was a clean refusal here and an unhandled ``IntegrityError``
# there, and NEITHER of them validated the address or the timezone at all (the guest's equivalents
# have been refused at the edge since the first booking). The service holds those rules once; this
# layer resolves the tenant, hands the form down, and turns the domain error into operator-facing
# text.
#
# Every id below arrives from a FORM, which makes each one a cross-tenant write surface until it is
# checked. They are all resolved tenant-scoped by the service, and an id that is not this tenant's
# is simply "not found" — never a row that gets written.


def _host_read(row: User) -> HostRead:
    return HostRead(id=row.id, name=row.name, email=row.email, timezone=row.timezone)


def _host_form_data(form: HostForm) -> users_service.UserData:
    return users_service.UserData(name=form.name, email=form.email, timezone=form.timezone)


async def list_hosts_view(maker: Sessionmaker, *, tenant_slug: str | None) -> list[HostRead]:
    """The tenant's hosts, oldest first — the choices the host selector offers."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rows = await users_service.list_users(session, tenant_id=ctx.tenant_id)
        return [_host_read(row) for row in rows]


async def create_host_action(
    maker: Sessionmaker, *, tenant_slug: str | None, form: HostForm
) -> HostRead:
    """Add a host to the business (name, a real address, a real timezone — the service decides)."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await users_service.create_user(
                session, tenant_id=ctx.tenant_id, data=_host_form_data(form)
            )
        except users_service.UserServiceError as exc:
            raise AdminActionError(str(exc)) from exc
        return _host_read(row)


async def update_host_action(
    maker: Sessionmaker, *, tenant_slug: str | None, host_id: uuid.UUID, form: HostForm
) -> HostRead:
    """Edit a host's name / email / timezone (all three are sent; the create rules apply again)."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await users_service.update_user(
                session, tenant_id=ctx.tenant_id, user_id=host_id, data=_host_form_data(form)
            )
        except users_service.UserServiceError as exc:
            raise AdminActionError(str(exc)) from exc
        return _host_read(row)


async def delete_host_action(
    maker: Sessionmaker, *, tenant_slug: str | None, host_id: uuid.UUID
) -> None:
    """Remove a host — refused, by the service, while an event type or a schedule still holds them.

    Both silent outcomes are catastrophic and neither raises anything on its own: let it CASCADE and
    the business's booking page loses event types nobody asked to remove (and their bookings with
    them); let it ORPHAN and the page keeps offering slots for a host who no longer exists. The
    refusal names what is holding them, and the operator decides.
    """
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            await users_service.delete_user(session, tenant_id=ctx.tenant_id, user_id=host_id)
        except users_service.UserServiceError as exc:
            raise AdminActionError(str(exc)) from exc


async def list_connections_view(
    maker: Sessionmaker, *, tenant_slug: str | None, host_id: uuid.UUID
) -> list[ConnectionRead]:
    """A host's connected calendar accounts, and the calendar each writes bookings into.

    ==Every one of them, not the first.== ``load_active_connections`` is deliberately plural: its
    predecessor ended in ``.first()``, so a host with two connected accounts had one silently
    ignored — and an ignored calendar is an ignored busy set, which is a double-booking waiting to
    happen. The operator cannot designate a calendar on a connection the panel never shows them.
    """
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            host = await users_service.get_user(session, tenant_id=ctx.tenant_id, user_id=host_id)
        except users_service.UserServiceError as exc:
            raise AdminActionError(str(exc)) from exc
        connections = await calendars_service.load_active_connections(
            session, tenant_id=ctx.tenant_id, user_id=host.id
        )
        reads: list[ConnectionRead] = []
        for connection in connections:
            links = (
                await session.scalars(
                    select(ExternalCalendarLink).where(
                        ExternalCalendarLink.tenant_id == ctx.tenant_id,
                        ExternalCalendarLink.connection_id == connection.id,
                        ExternalCalendarLink.is_booking_target.is_(True),
                    )
                )
            ).all()
            target = links[0].external_calendar_id if links else None
            reads.append(
                ConnectionRead(
                    id=connection.id,
                    account_email=connection.account_email,
                    booking_calendar_id=target,
                )
            )
        return reads


async def designate_calendar_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    connection_id: uuid.UUID,
    calendar_id: str,
) -> None:
    """Point a connection's bookings at ONE named calendar — the admin's ``--calendar-id`` (RF-11).

    This is the write side of the table that used to be dead: ``_DEFAULT_CALENDAR_ID = "primary"``
    was a hard-coded constant, so nobody ever read or wrote ``external_calendar_links`` and a host
    could not send bookings anywhere but the connected account's primary calendar. Using a
    dedicated, secondary calendar is the rule for a real account — and it was not expressible.

    The service does the rest: it retires the host's other targets (one target per HOST, not per
    connection — two would dead-letter every booking on an ambiguity nobody chose), serialises the
    choice on the host's row, and invalidates the busy cache only when the calendars actually read
    have changed.
    """
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        # Tenant-scoped BEFORE it is handed to the service: the id came off a form, and the service
        # takes the connection row itself, so an unscoped load here would let one business re-point
        # another's calendar and write its meetings into theirs.
        connection = (
            await session.scalars(
                select(ExternalConnection).where(
                    ExternalConnection.id == connection_id,
                    ExternalConnection.tenant_id == ctx.tenant_id,
                )
            )
        ).one_or_none()
        if connection is None:
            raise AdminActionError("Calendar connection not found")
        await calendars_service.link_booking_calendar(
            session, connection=connection, calendar_id=calendar_id
        )


# --------------------------------------------------------------------------------------
# Workflow rules + templates (RF-24).
# --------------------------------------------------------------------------------------
#
# Every mutation here is routed through ``services/workflow_rules``, and that is the whole point:
# that module RECONCILES the queue of every booking a rule governs. A rule edit that writes only the
# ``workflows`` row leaves each guest already on the books reminded at the OLD time. The panel shows
# the change, the database agrees with the panel, and nothing sends what the operator asked for. The
# admin therefore owns no rule logic of its own; it resolves the tenant, passes the clock down, and
# lets that service do the arming.
#
# The READ side is the service's too (``rule_to_read``). The panel used to project a rule with its
# own hand-written copy of the API's projection — nine fields, maintained twice. Both were
# type-checked, which made the duplication look free; it is not, because a new field with a DEFAULT
# breaks neither copy, and the panel then quietly stops showing what the API still returns.
#
# ``WorkflowRuleError`` is surfaced by its OWN message rather than remapped. Each one was written to
# be read by the person who caused it ("the whatsapp step of kind 'reminder' has no template to
# render its body ... without one the step is skipped at send time and the guest is never messaged,
# silently"), and a hand-written table of replacements is precisely the thing that drifted into
# lying about the booking errors above.


async def list_workflows_view(
    maker: Sessionmaker, *, tenant_slug: str | None
) -> list[WorkflowRead]:
    """Every rule of the tenant (active and inactive), each with its steps."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rules = await workflow_rules_service.list_workflows(session, tenant_id=ctx.tenant_id)
        return [workflow_rules_service.rule_to_read(rule) for rule in rules]


async def create_workflow_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    data: WorkflowCreate,
    now: datetime | None = None,
) -> WorkflowRead:
    """Author a rule and ARM it against the bookings that already exist."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            rule = await workflow_rules_service.create_workflow(
                session, tenant_id=ctx.tenant_id, data=data, now=_now(now)
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        return workflow_rules_service.rule_to_read(rule)


async def update_workflow_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    now: datetime | None = None,
) -> WorkflowRead:
    """Edit a rule and MAKE THE EDIT TRUE for every booking it already governs."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            rule = await workflow_rules_service.update_workflow(
                session,
                tenant_id=ctx.tenant_id,
                workflow_id=workflow_id,
                data=data,
                now=_now(now),
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        if rule is None:
            # The service returns ``None`` rather than raising for an absent row. Reported as a
            # success, that is the panel confirming a save that never touched anything.
            raise AdminActionError("Workflow not found")
        return workflow_rules_service.rule_to_read(rule)


async def set_workflow_active_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    workflow_id: uuid.UUID,
    active: bool,
    now: datetime | None = None,
) -> WorkflowRead:
    """Switch a rule on or off. Off PAUSES its queued messages; on re-arms and re-times them."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            rule = await workflow_rules_service.set_workflow_active(
                session,
                tenant_id=ctx.tenant_id,
                workflow_id=workflow_id,
                active=active,
                now=_now(now),
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        if rule is None:
            raise AdminActionError("Workflow not found")
        return workflow_rules_service.rule_to_read(rule)


async def list_templates_view(
    maker: Sessionmaker, *, tenant_slug: str | None
) -> list[WorkflowTemplateRead]:
    """Every message body the tenant has authored."""
    async with maker() as session:
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        rows = await workflow_rules_service.list_templates(session, tenant_id=ctx.tenant_id)
        return [WorkflowTemplateRead.model_validate(row) for row in rows]


async def create_template_action(
    maker: Sessionmaker, *, tenant_slug: str | None, data: WorkflowTemplateCreate
) -> WorkflowTemplateRead:
    """Store the body for one ``(channel, kind, locale)``."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await workflow_rules_service.create_template(
                session, tenant_id=ctx.tenant_id, data=data
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        return WorkflowTemplateRead.model_validate(row)


async def update_template_action(
    maker: Sessionmaker,
    *,
    tenant_slug: str | None,
    template_id: uuid.UUID,
    data: WorkflowTemplateUpdate,
) -> WorkflowTemplateRead:
    """Edit a template's TEXT (its ``(channel, kind, locale)`` identity is immutable by design)."""
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            row = await workflow_rules_service.update_template(
                session, tenant_id=ctx.tenant_id, template_id=template_id, data=data
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        if row is None:
            raise AdminActionError("Template not found")
        return WorkflowTemplateRead.model_validate(row)


async def delete_template_action(
    maker: Sessionmaker, *, tenant_slug: str | None, template_id: uuid.UUID
) -> None:
    """Delete a template; refused while it is the last body a live step can render.

    An absent row is an ERROR, not a quiet success: ``delete_template`` returns ``False`` for one,
    and a handler that reports that as "deleted" tells the operator it removed something that was
    never there (the same no-op ``deactivate_event_type`` already guards against).
    """
    async with maker() as session, session.begin():
        ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
        try:
            deleted = await workflow_rules_service.delete_template(
                session, tenant_id=ctx.tenant_id, template_id=template_id
            )
        except workflow_rules_service.WorkflowRuleError as exc:
            raise AdminActionError(str(exc)) from exc
        if not deleted:
            raise AdminActionError("Template not found")


__all__ = [
    "AdminActionError",
    "AdminContext",
    "AdminError",
    "AdminMetrics",
    "AdminSetupError",
    "BookingForm",
    "ConnectionRead",
    "EventTypeForm",
    "HostForm",
    "HostRead",
    "cancel_booking_action",
    "create_booking_action",
    "create_event_type_action",
    "create_host_action",
    "create_schedule_action",
    "create_template_action",
    "create_workflow_action",
    "deactivate_event_type_action",
    "delete_host_action",
    "delete_schedule_action",
    "delete_template_action",
    "designate_calendar_action",
    "list_bookings_view",
    "list_connections_view",
    "list_event_types_view",
    "list_hosts_view",
    "list_schedules_view",
    "list_templates_view",
    "list_workflows_view",
    "mark_no_show_action",
    "metrics_view",
    "reschedule_booking_action",
    "resolve_admin_context",
    "set_workflow_active_action",
    "update_event_type_action",
    "update_host_action",
    "update_schedule_action",
    "update_template_action",
    "update_workflow_action",
]
