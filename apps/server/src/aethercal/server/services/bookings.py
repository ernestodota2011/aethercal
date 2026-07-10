"""Booking lifecycle service (F1-05, RF-04/RF-07/RF-16): create / cancel / reschedule.

This is the integration hub of F1: it validates a requested slot against the F1-04 slots engine,
persists the booking, durably queues the F1-09 webhook in the SAME transaction, and drives the
optional side-effects (F1-06 guest tokens, F1-07 Google event, F1-08 email, F1-10 reminder) through
an injected :class:`BookingEffects` bundle so the core stays testable offline. Transaction control
(commit/rollback) belongs to the caller (``get_session`` or the test session), never to this module.

Anti-double-booking (RF-04) is enforced in TWO independent layers, both required:

1. **Per-host serialization lock.** At the start of a create/reschedule transaction we take a
   PostgreSQL transaction-scoped advisory lock keyed by a stable 64-bit hash of ``(tenant, host)``
   (:func:`_serialize_host`). Two concurrent bookings for the same host then run one-after-another:
   the loser, on re-reading availability, sees the winner's committed row and finds the slot no
   longer on offer. Released automatically at transaction end. A no-op on SQLite (which serializes
   writes anyway) so the offline suite is unaffected.
2. **DB partial unique index backstop.** Even if two transactions race past the availability read,
   the ``uq_bookings_active_slot`` partial unique index (``WHERE status <> 'cancelled'``) admits at
   most one active booking per ``(tenant, event_type, start)``. The losing INSERT raises
   ``IntegrityError``; we catch it inside a SAVEPOINT (mirroring ``services/event_types.py``) and
   surface it as a clean :class:`SlotUnavailableError` → the router maps it to 409.

Together they guarantee that of two concurrent requests for the same slot exactly one confirms; the
Postgres-only ``test_booking_concurrency.py`` proves it end-to-end.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus, TimeInterval
from aethercal.server.db.models import Booking, EventType, ExternalConnection
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.integrations.smtp.sender import EmailSender
from aethercal.server.jobs.reminders import TaskRunner, schedule_reminder
from aethercal.server.services.calendars import (
    CalendarSyncError,
    create_event_for_booking,
    delete_event_for_booking,
    reschedule_event_for_booking,
)
from aethercal.server.services.event_types import get_event_type
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    issue_guest_token,
)
from aethercal.server.services.notifications import send_booking_notification
from aethercal.server.services.slots import SlotsResult, compute_slots
from aethercal.server.services.webhooks import enqueue_event

_logger = logging.getLogger(__name__)

# How far before the start the 24 h reminder fires (RF-10).
_REMINDER_LEAD = timedelta(hours=24)


# --------------------------------------------------------------------------------------
# Errors — each maps to one clean HTTP status at the router (RF-16, no internal leak).
# --------------------------------------------------------------------------------------


class BookingError(Exception):
    """Base class for booking-service errors the API maps to a clean HTTP status."""


class EventTypeNotFoundError(BookingError):
    """The event type does not exist for the tenant (→ HTTP 404)."""


class BookingNotFoundError(BookingError):
    """No booking with that id exists for the tenant (→ HTTP 404)."""


class SlotUnavailableError(BookingError):
    """The requested slot is not on offer or is already booked (→ HTTP 409)."""


class AvailabilityUnavailableError(BookingError):
    """The host's external calendar could not be established, so no slot may be offered (→ 503)."""


class BookingNotActiveError(BookingError):
    """The booking is not in a reschedulable state (e.g. already cancelled) (→ HTTP 409)."""


# --------------------------------------------------------------------------------------
# Inputs — the request data and the injected side-effect dependencies.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BookingParams:
    """The guest-supplied inputs for a new booking (RF-07). ``end`` is derived from the duration."""

    event_type_id: uuid.UUID
    start: datetime
    guest_name: str
    guest_email: str
    guest_timezone: str
    guest_notes: str | None = None
    answers: dict[str, Any] | None = None
    locale: str = "es"


@dataclass(frozen=True, slots=True)
class BookingEffects:
    """The optional runtime dependencies for a booking's non-DB side-effects (F1-06/07/08/10).

    Injected so the core create/cancel/reschedule stays unit-testable and the effects are pluggable.
    ``signer`` + ``booking_base_url`` are always present when a bundle is supplied (guest links are
    minted and built); everything else degrades gracefully when absent — no ``sender`` skips the
    email, no ``connection``/``google_service`` skips the calendar sync, no ``reminder_runner``
    skips the reminder. A booking is NEVER failed on a missing or failing effect (all best-effort).
    """

    signer: GuestTokenSigner
    booking_base_url: str
    sender: EmailSender | None = None
    reminder_runner: TaskRunner | None = None
    connection: ExternalConnection | None = None
    google_service: Any = None


# --------------------------------------------------------------------------------------
# Anti-double-booking layer 1 — per-host serialization lock (PostgreSQL only).
# --------------------------------------------------------------------------------------


def _host_lock_key(tenant_id: uuid.UUID, host_id: uuid.UUID) -> int:
    """A stable signed 64-bit key for the ``(tenant, host)`` advisory lock (RF-04).

    ``pg_advisory_xact_lock`` takes a signed ``bigint``; an 8-byte BLAKE2b digest read as a signed
    integer fits exactly and is deterministic across processes, so every booker for a given host
    contends on the same key.
    """
    digest = hashlib.blake2b(f"{tenant_id}:{host_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _serialize_host(
    session: AsyncSession, *, tenant_id: uuid.UUID, host_id: uuid.UUID
) -> None:
    """Serialize concurrent bookings for one host on PostgreSQL (RF-04, layer 1).

    Takes a transaction-scoped advisory lock so two concurrent create/reschedule transactions for
    the same host run one-after-another (each sees the other's committed rows on re-read), released
    automatically at transaction end. On SQLite (the offline test backend) this is a harmless no-op:
    SQLite serializes writes anyway.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _host_lock_key(tenant_id, host_id)},
    )


# --------------------------------------------------------------------------------------
# Slot validation + serialization helpers.
# --------------------------------------------------------------------------------------


def _to_utc(moment: datetime) -> datetime:
    """Normalize any datetime to an aware UTC instant (SQLite drops tzinfo on round-trip)."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def _require_slot_on_offer(result: SlotsResult | None, *, start: datetime, end: datetime) -> None:
    """Assert the requested ``[start, end)`` is a slot the availability engine actually offers.

    ``None`` means the event type vanished (race) → not found; ``unavailable`` means the external
    calendar could not be established → refuse (RF-13); otherwise the exact interval must be in the
    offered set, else the slot is not bookable (out of window / already taken) → 409.
    """
    if result is None:
        raise EventTypeNotFoundError("event type not found")
    if result.availability == "unavailable":
        raise AvailabilityUnavailableError("host availability could not be established")
    if TimeInterval(start=start, end=end) not in result.slots:
        raise SlotUnavailableError(f"slot {start.isoformat()} is not on offer")


async def _validate_slot(  # noqa: PLR0913 - the window + injected clock are the validation inputs
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: EventType,
    start: datetime,
    end: datetime,
    now: datetime,
) -> None:
    """Confirm ``[start, end)`` is on offer for ``event_type`` (RF-03/RF-13).

    The window is padded by a day on each side so a slot whose local date differs from its UTC date
    is still computed; the request path injects no ``service_factory`` (RNF-6: read the busy cache
    only, never call Google in-band).
    """
    result = await compute_slots(
        session,
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        window_from=(start - timedelta(days=1)).date(),
        window_to=(end + timedelta(days=1)).date(),
        now=now,
    )
    _require_slot_on_offer(result, start=start, end=end)


def _serialize_booking(booking: Booking) -> dict[str, object]:
    """A JSON-serializable snapshot of a booking for the webhook envelope (RF-17).

    Every value is a primitive/str/None so ``enqueue_event`` can canonicalize it for signing; the
    internal ``external_event_id`` is intentionally omitted from the public event.
    """
    return {
        "id": str(booking.id),
        "tenant_id": str(booking.tenant_id),
        "event_type_id": str(booking.event_type_id),
        "status": booking.status.value,
        "start": _to_utc(booking.start_at).isoformat(),
        "end": _to_utc(booking.end_at).isoformat(),
        "guest_name": booking.guest_name,
        "guest_email": booking.guest_email,
        "guest_timezone": booking.guest_timezone,
        "answers": booking.answers,
        "meeting_url": booking.meeting_url,
        "rescheduled_from_id": (
            str(booking.rescheduled_from_id) if booking.rescheduled_from_id is not None else None
        ),
    }


async def _load_booking(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_id: uuid.UUID
) -> Booking | None:
    """The tenant's booking by id, or ``None`` (tenant-scoped — never another tenant's row)."""
    return (
        await session.scalars(
            select(Booking).where(Booking.id == booking_id, Booking.tenant_id == tenant_id)
        )
    ).one_or_none()


# --------------------------------------------------------------------------------------
# create_booking
# --------------------------------------------------------------------------------------


async def create_booking(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    params: BookingParams,
    now: datetime,
    effects: BookingEffects | None = None,
) -> Booking:
    """Book ``params.start`` for a tenant's event type (RF-04/RF-07).

    Validates the slot is on offer, serializes the host (layer 1), inserts the ``confirmed`` booking
    catching the partial-index conflict as :class:`SlotUnavailableError` (layer 2), and durably
    queues the ``booking.created`` webhook in the SAME transaction. When ``effects`` is supplied it
    then mints the cancel/reschedule guest tokens, best-effort sends the confirmation email, syncs
    the Google event (keeping the booking on failure), and schedules the 24 h reminder. Raises
    :class:`EventTypeNotFoundError` (404), :class:`SlotUnavailableError` (409) or
    :class:`AvailabilityUnavailableError` (503). Flushes; the caller owns the commit.
    """
    event_type = await get_event_type(
        session, tenant_id=tenant_id, event_type_id=params.event_type_id
    )
    if event_type is None:
        raise EventTypeNotFoundError("event type not found")

    start = _to_utc(params.start)
    end = start + timedelta(seconds=event_type.duration_seconds)

    await _serialize_host(session, tenant_id=tenant_id, host_id=event_type.host_id)
    await _validate_slot(
        session, tenant_id=tenant_id, event_type=event_type, start=start, end=end, now=now
    )

    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=end,
        status=BookingStatus.CONFIRMED,
        guest_name=params.guest_name,
        guest_email=params.guest_email,
        guest_timezone=params.guest_timezone,
        guest_notes=params.guest_notes,
        answers=dict(params.answers) if params.answers is not None else {},
    )
    await _insert_active(session, booking, start=start)
    await enqueue_event(
        session,
        tenant_id=tenant_id,
        event="booking.created",
        data=_serialize_booking(booking),
        now=now,
    )
    if effects is not None:
        await _apply_create_effects(
            session,
            booking=booking,
            event_type=event_type,
            effects=effects,
            now=now,
            locale=params.locale,
        )
    return booking


async def _insert_active(session: AsyncSession, booking: Booking, *, start: datetime) -> None:
    """Insert an active booking inside a SAVEPOINT, mapping the partial-index conflict to 409.

    The SAVEPOINT (like ``services/event_types.py``'s duplicate-slug handling) rolls back only the
    offending INSERT so a partial-index violation (a concurrent active booking on the exact same
    slot, RF-04 layer 2) surfaces as :class:`SlotUnavailableError` without poisoning the caller's
    transaction.
    """
    try:
        async with session.begin_nested():
            session.add(booking)
            await session.flush()
    except IntegrityError as exc:
        raise SlotUnavailableError(f"slot {start.isoformat()} is already booked") from exc


# --------------------------------------------------------------------------------------
# cancel_booking
# --------------------------------------------------------------------------------------


async def cancel_booking(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    now: datetime,
    effects: BookingEffects | None = None,
) -> Booking:
    """Cancel a booking, freeing its slot (RF-07). Idempotent.

    Sets ``status=cancelled`` + ``cancelled_at`` and queues the ``booking.cancelled`` webhook in the
    same transaction; best-effort deletes the Google event and sends the cancellation email when
    ``effects`` is supplied. Cancelling an already-cancelled booking is a no-op (no second webhook).
    Raises :class:`BookingNotFoundError` (404) if the tenant has no such booking.
    """
    booking = await _load_booking(session, tenant_id=tenant_id, booking_id=booking_id)
    if booking is None:
        raise BookingNotFoundError("booking not found")
    if booking.status == BookingStatus.CANCELLED:
        return booking

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    await session.flush()
    await enqueue_event(
        session,
        tenant_id=tenant_id,
        event="booking.cancelled",
        data=_serialize_booking(booking),
        now=now,
    )
    if effects is not None:
        await _sync_google_delete(booking=booking, effects=effects)
        await _send_email(
            session,
            kind=NotificationKind.CANCELLATION,
            booking=booking,
            cancel_url=None,
            reschedule_url=None,
            effects=effects,
            now=now,
        )
    return booking


# --------------------------------------------------------------------------------------
# reschedule_booking
# --------------------------------------------------------------------------------------


async def reschedule_booking(  # noqa: PLR0913 - the spec-mandated keyword contract for this seam
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    new_start: datetime,
    now: datetime,
    effects: BookingEffects | None = None,
) -> Booking:
    """Move a booking to ``new_start`` by opening a new booking and cancelling the old (RF-07).

    Keeping the old row (cancelled) preserves history and lets the partial unique index guard the
    new slot exactly as a fresh booking would. Both writes happen inside ONE SAVEPOINT so a conflict
    on the new slot (RF-04 layer 2) rolls BOTH back — the original is never left cancelled without a
    replacement. Queues ``booking.rescheduled`` in the same transaction; best-effort updates the
    Google event, sends the reschedule email and re-schedules the reminder. Raises
    :class:`BookingNotFoundError` (404), :class:`BookingNotActiveError` (409, already cancelled),
    :class:`SlotUnavailableError` (409) or :class:`AvailabilityUnavailableError` (503).
    """
    old = await _load_booking(session, tenant_id=tenant_id, booking_id=booking_id)
    if old is None:
        raise BookingNotFoundError("booking not found")
    if old.status == BookingStatus.CANCELLED:
        raise BookingNotActiveError("a cancelled booking cannot be rescheduled")

    event_type = await get_event_type(session, tenant_id=tenant_id, event_type_id=old.event_type_id)
    if event_type is None:  # pragma: no cover - the FK guarantees the row exists
        raise EventTypeNotFoundError("event type not found")

    start = _to_utc(new_start)
    end = start + timedelta(seconds=event_type.duration_seconds)

    await _serialize_host(session, tenant_id=tenant_id, host_id=event_type.host_id)
    await _validate_slot(
        session, tenant_id=tenant_id, event_type=event_type, start=start, end=end, now=now
    )

    new = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=end,
        status=BookingStatus.CONFIRMED,
        guest_name=old.guest_name,
        guest_email=old.guest_email,
        guest_timezone=old.guest_timezone,
        guest_notes=old.guest_notes,
        answers=dict(old.answers),
        rescheduled_from_id=old.id,
    )
    await _swap_booking(session, old=old, new=new, now=now, start=start)
    await enqueue_event(
        session,
        tenant_id=tenant_id,
        event="booking.rescheduled",
        data=_serialize_booking(new),
        now=now,
    )
    if effects is not None:
        await _apply_reschedule_effects(
            session, old=old, new=new, event_type=event_type, effects=effects, now=now
        )
    return new


async def _swap_booking(
    session: AsyncSession, *, old: Booking, new: Booking, now: datetime, start: datetime
) -> None:
    """Cancel ``old`` and insert ``new`` atomically in one SAVEPOINT (RF-04/RF-07).

    ``old`` is cancelled first (freeing its slot for the partial index) then ``new`` is inserted; a
    conflict on the new slot rolls BOTH back, so a refused reschedule never leaves the original
    cancelled without its replacement.
    """
    try:
        async with session.begin_nested():
            old.status = BookingStatus.CANCELLED
            old.cancelled_at = now
            await session.flush()
            session.add(new)
            await session.flush()
    except IntegrityError as exc:
        raise SlotUnavailableError(f"slot {start.isoformat()} is already booked") from exc


# --------------------------------------------------------------------------------------
# Read paths.
# --------------------------------------------------------------------------------------


async def get_booking(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_id: uuid.UUID
) -> Booking | None:
    """Return the tenant's booking by id, or ``None`` if absent (tenant-scoped)."""
    return await _load_booking(session, tenant_id=tenant_id, booking_id=booking_id)


async def list_bookings(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: BookingStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Booking]:
    """List the tenant's bookings, optionally filtered by ``status`` and a start-date window.

    ``date_from`` / ``date_to`` are inclusive calendar dates matched against ``start_at`` (UTC).
    Ordered by start then id for a stable page.
    """
    stmt = select(Booking).where(Booking.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    if date_from is not None:
        stmt = stmt.where(Booking.start_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to is not None:
        upper = datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
        stmt = stmt.where(Booking.start_at < upper)
    stmt = stmt.order_by(Booking.start_at, Booking.id)
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------------------
# Side-effects (F1-06/07/08/10) — every one best-effort; never fails the booking.
# --------------------------------------------------------------------------------------


def _guest_link(base_url: str, action: str, token: str) -> str:
    """Build a public self-serve link carrying the signed guest ``token`` (F1-06/10)."""
    return f"{base_url.rstrip('/')}/{action}?token={token}"


def _guest_token_ttl(start: datetime, now: datetime) -> timedelta:
    """A guest link stays valid until just after the appointment it manages (min one day)."""
    return max(timedelta(days=1), (start - now) + timedelta(days=1))


async def _mint_guest_links(
    session: AsyncSession, *, booking: Booking, effects: BookingEffects, now: datetime
) -> tuple[str, str]:
    """Mint the cancel + reschedule guest tokens (F1-06) and return their public URLs."""
    ttl = _guest_token_ttl(_to_utc(booking.start_at), now)
    cancel = await issue_guest_token(
        session,
        effects.signer,
        booking_id=booking.id,
        tenant_id=booking.tenant_id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=ttl,
    )
    reschedule = await issue_guest_token(
        session,
        effects.signer,
        booking_id=booking.id,
        tenant_id=booking.tenant_id,
        purpose=GuestTokenPurpose.RESCHEDULE,
        ttl=ttl,
    )
    return (
        _guest_link(effects.booking_base_url, "cancel", cancel),
        _guest_link(effects.booking_base_url, "reschedule", reschedule),
    )


async def _apply_create_effects(  # noqa: PLR0913 - each effect input is part of the contract
    session: AsyncSession,
    *,
    booking: Booking,
    event_type: EventType,
    effects: BookingEffects,
    now: datetime,
    locale: str,
) -> None:
    """Run create-time side-effects: tokens → Google event → email → reminder (all best-effort)."""
    cancel_url, reschedule_url = await _mint_guest_links(
        session, booking=booking, effects=effects, now=now
    )
    await _sync_google_upsert(session, booking=booking, event_type=event_type, effects=effects)
    await _send_email(
        session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
        effects=effects,
        now=now,
        locale=locale,
    )
    _schedule_reminder(effects, booking=booking)


async def _apply_reschedule_effects(  # noqa: PLR0913 - each effect input is part of the contract
    session: AsyncSession,
    *,
    old: Booking,
    new: Booking,
    event_type: EventType,
    effects: BookingEffects,
    now: datetime,
) -> None:
    """Run reschedule-time side-effects: fresh tokens → Google update → email → reminder."""
    cancel_url, reschedule_url = await _mint_guest_links(
        session, booking=new, effects=effects, now=now
    )
    await _sync_google_reschedule(session, old=old, new=new, event_type=event_type, effects=effects)
    await _send_email(
        session,
        kind=NotificationKind.RESCHEDULE,
        booking=new,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
        effects=effects,
        now=now,
    )
    _schedule_reminder(effects, booking=new)


def _schedule_reminder(effects: BookingEffects, *, booking: Booking) -> None:
    """Schedule the 24 h reminder for ``booking`` if a runner is wired (F1-10); else skip."""
    if effects.reminder_runner is None:
        return
    schedule_reminder(
        effects.reminder_runner,
        booking=booking,
        send_at=_to_utc(booking.start_at) - _REMINDER_LEAD,
    )


async def _send_email(  # noqa: PLR0913 - the composer needs the full booking + link context
    session: AsyncSession,
    *,
    kind: NotificationKind,
    booking: Booking,
    cancel_url: str | None,
    reschedule_url: str | None,
    effects: BookingEffects,
    now: datetime,
    locale: str = "es",
) -> None:
    """Best-effort send of a transactional email (F1-08); an SMTP failure never fails a booking."""
    if effects.sender is None:
        return
    try:
        await send_booking_notification(
            session,
            kind=kind,
            booking=booking,
            cancel_url=cancel_url,
            reschedule_url=reschedule_url,
            sender=effects.sender,
            now=now,
            locale=locale,
        )
    except Exception:
        _logger.exception("booking %s: %s email failed (best-effort, kept)", booking.id, kind.value)


async def _sync_google_upsert(
    session: AsyncSession, *, booking: Booking, event_type: EventType, effects: BookingEffects
) -> None:
    """Create the Google event for a booking (F1-07); keep the booking and leave NULL on failure."""
    if effects.connection is None or effects.google_service is None:
        return
    try:
        external_id, meeting_url = await create_event_for_booking(
            connection=effects.connection,
            request=_meet_request(booking, event_type),
            service=effects.google_service,
        )
    except CalendarSyncError:
        _logger.exception(
            "booking %s: Google event create failed; leaving external_event_id NULL for retry",
            booking.id,
        )
        return
    booking.external_event_id = external_id
    booking.meeting_url = meeting_url
    await session.flush()


async def _sync_google_reschedule(
    session: AsyncSession,
    *,
    old: Booking,
    new: Booking,
    event_type: EventType,
    effects: BookingEffects,
) -> None:
    """Move the Google event to the new booking (F1-07); best-effort, kept on failure."""
    if effects.connection is None or effects.google_service is None:
        return
    request = _meet_request(new, event_type)
    try:
        if old.external_event_id is not None:
            external_id, meeting_url = await reschedule_event_for_booking(
                connection=effects.connection,
                external_event_id=old.external_event_id,
                request=request,
                service=effects.google_service,
            )
        else:
            external_id, meeting_url = await create_event_for_booking(
                connection=effects.connection, request=request, service=effects.google_service
            )
    except CalendarSyncError:
        _logger.exception("booking %s: Google reschedule failed (best-effort, kept)", new.id)
        return
    new.external_event_id = external_id
    new.meeting_url = meeting_url
    await session.flush()


async def _sync_google_delete(*, booking: Booking, effects: BookingEffects) -> None:
    """Delete a cancelled booking's Google event (F1-07); best-effort, never fails the cancel."""
    if (
        effects.connection is None
        or effects.google_service is None
        or booking.external_event_id is None
    ):
        return
    try:
        await delete_event_for_booking(
            connection=effects.connection,
            external_event_id=booking.external_event_id,
            service=effects.google_service,
        )
    except CalendarSyncError:
        _logger.exception("booking %s: Google event delete failed (best-effort)", booking.id)


def _meet_request(booking: Booking, event_type: EventType) -> MeetEventRequest:
    """Build the Google Meet event request for a booking (F1-07)."""
    return MeetEventRequest(
        summary=event_type.title,
        start=_to_utc(booking.start_at),
        end=_to_utc(booking.end_at),
        timezone=booking.guest_timezone,
        guest_email=booking.guest_email,
    )


__all__ = [
    "AvailabilityUnavailableError",
    "BookingEffects",
    "BookingError",
    "BookingNotActiveError",
    "BookingNotFoundError",
    "BookingParams",
    "EventTypeNotFoundError",
    "SlotUnavailableError",
    "cancel_booking",
    "create_booking",
    "get_booking",
    "list_bookings",
    "reschedule_booking",
]
