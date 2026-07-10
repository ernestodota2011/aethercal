"""Booking lifecycle service (F1-05, RF-04/RF-07/RF-16): create / cancel / reschedule.

This is the integration hub of F1: it validates a requested slot against the F1-04 slots engine,
persists the booking, durably queues the F1-09 webhook in the SAME transaction, and drives the
optional side-effects (F1-06 guest tokens, F1-07 Google event, F1-08 email, F1-10 reminder) through
an injected :class:`BookingEffects` bundle so the core stays testable offline. Transaction control
(commit/rollback) belongs to the caller (``get_session`` or the test session), never to this module.

Anti-double-booking (RF-04) is enforced in TWO independent layers, both required:

1. **Per-host serialization lock.** At the start of a create/cancel/reschedule transaction we take
   a PostgreSQL transaction-scoped advisory lock keyed by a stable 64-bit hash of ``(tenant, host)``
   (:func:`_serialize_host`). Two concurrent bookings for the same host then run one-after-another:
   the loser, on re-reading availability, sees the winner's committed row and finds the slot no
   longer on offer. For a cancel/reschedule the lock is taken FIRST, then the booking is re-loaded
   under it (:func:`_lock_and_reload_booking`) and re-validated as still active before mutating, so
   two concurrent cancels/reschedules cannot both act on a stale view (a cancel would emit a second
   webhook; a reschedule to a different ``start_at`` would open a second active replacement the
   partial index cannot catch). Released automatically at transaction end. A no-op on SQLite (which
   serializes writes anyway) so the offline suite is unaffected.
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
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.jobs.reminders import TaskRunner, schedule_reminder
from aethercal.server.services.event_types import get_event_type
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    issue_guest_token,
)
from aethercal.server.services.outbox import (
    GoogleOperation,
    OutboxEffect,
    email_dedupe_key,
    enqueue_effect,
    google_dedupe_key,
)
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
    """The runtime dependencies for a booking's side-effects that are decided at request time.

    Injected so the core create/cancel/reschedule stays unit-testable. ``signer`` +
    ``booking_base_url`` are always present (the guest links are minted + built in-txn). The durable
    effects (email, Google sync) are NOT gated on a live client here — they are ENQUEUED to the
    outbox and the drain worker supplies the client, so a momentarily-absent SMTP/Google never drops
    a domain-required effect. ``connection`` names the host's calendar link (its id rides in the
    Google intent; the live client is built later by the executor's ``service_factory``); ``None``
    means the host has no external calendar, so nothing to sync. ``reminder_runner`` schedules the
    24 h reminder inline (self-healing at fire time), skipped when absent.
    """

    signer: GuestTokenSigner
    booking_base_url: str
    reminder_runner: TaskRunner | None = None
    connection: ExternalConnection | None = None


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


async def _lock_and_reload_booking(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_id: uuid.UUID
) -> tuple[Booking, EventType]:
    """Load a booking, take the per-host lock FIRST, then re-load it under the lock (RF-04).

    The correctness-critical ordering shared by the cancel/reschedule mutation paths: acquire the
    host's transaction-scoped advisory lock (:func:`_serialize_host`) BEFORE trusting the booking's
    state, then ``refresh`` it so any concurrent mutation that committed while we waited on the lock
    is now visible (a READ COMMITTED re-read, exactly as ``create_booking`` re-reads availability).
    The caller then re-validates the committed-consistent status before mutating, so two racing
    cancel/reschedule requests cannot both act on a stale "still active" view (the double-booking
    hole a partial index cannot close, since a reschedule to a new ``start_at`` never collides).

    Returns the reloaded booking and its event type (whose ``host_id`` keys the lock). Raises
    :class:`BookingNotFoundError` (404) if the tenant has no such booking. On SQLite the lock is a
    no-op and the refresh is a harmless re-read (writes are already serialized there).
    """
    booking = await _load_booking(session, tenant_id=tenant_id, booking_id=booking_id)
    if booking is None:
        raise BookingNotFoundError("booking not found")
    event_type = await get_event_type(
        session, tenant_id=tenant_id, event_type_id=booking.event_type_id
    )
    if event_type is None:  # pragma: no cover - the FK guarantees the row exists
        raise EventTypeNotFoundError("event type not found")
    await _serialize_host(session, tenant_id=tenant_id, host_id=event_type.host_id)
    await session.refresh(booking)
    return booking, event_type


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
    """Cancel a booking, freeing its slot (RF-07). Idempotent, even under concurrency (RF-04).

    Takes the per-host advisory lock FIRST and re-loads the booking under it (committed state), so
    two concurrent cancels serialize instead of racing: the first transitions ``status=cancelled`` +
    ``cancelled_at`` and queues the ``booking.cancelled`` webhook in the same transaction; the loser
    sees it already cancelled and is a no-op that queues NO second webhook. Best-effort deletes the
    Google event and sends the cancellation email when ``effects`` is supplied. Raises
    :class:`BookingNotFoundError` (404) if the tenant has no such booking.
    """
    booking, _event_type = await _lock_and_reload_booking(
        session, tenant_id=tenant_id, booking_id=booking_id
    )
    if booking.status == BookingStatus.CANCELLED:
        return booking  # already cancelled under the lock → no-op, no duplicate webhook (RF-04)

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    # Bump the persisted iCal SEQUENCE so the cancellation .ics strictly supersedes the confirmation
    # (RFC 5545, F1-08); the drained cancellation email reads this value.
    booking.sequence += 1
    await session.flush()
    await enqueue_event(
        session,
        tenant_id=tenant_id,
        event="booking.cancelled",
        data=_serialize_booking(booking),
        now=now,
    )
    if effects is not None:
        await _enqueue_google(
            session,
            booking=booking,
            effects=effects,
            operation=GoogleOperation.DELETE,
            external_event_id=booking.external_event_id,
        )
        await _enqueue_email(
            session,
            kind=NotificationKind.CANCELLATION,
            booking=booking,
            cancel_url=None,
            reschedule_url=None,
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
    :class:`BookingNotFoundError` (404), :class:`BookingNotActiveError` (409, no longer confirmed),
    :class:`SlotUnavailableError` (409) or :class:`AvailabilityUnavailableError` (503).

    Concurrency correctness (RF-04): the per-host advisory lock is taken FIRST (via
    :func:`_lock_and_reload_booking`) and the booking is re-loaded + re-validated as still
    ``confirmed`` under it. Two concurrent reschedules of the same booking to DIFFERENT slots would
    otherwise each see it active and each open a replacement (different ``start_at`` slips past the
    partial index) — a double-booking hole. Serializing on the lock and re-checking the committed
    status closes it: the loser sees it already cancelled/rescheduled and is refused (not active).
    """
    old, event_type = await _lock_and_reload_booking(
        session, tenant_id=tenant_id, booking_id=booking_id
    )
    if old.status != BookingStatus.CONFIRMED:
        raise BookingNotActiveError("only a confirmed booking can be rescheduled")

    start = _to_utc(new_start)
    end = start + timedelta(seconds=event_type.duration_seconds)

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
        # Carry the predecessor's iCal SEQUENCE forward + 1 so successive reschedules strictly
        # increase (RFC 5545, F1-08); the drained reschedule email snapshots this value.
        sequence=old.sequence + 1,
        # Inherit the predecessor's stable UID so every update addresses the SAME calendar event —
        # without this the strictly-increasing sequence would be spread across distinct UIDs and a
        # client would treat each reschedule as a brand-new event instead of an update.
        ical_uid=old.ical_uid,
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
# Side-effects (F1-06/07/08/10) — durable, transactional, post-commit.
# --------------------------------------------------------------------------------------

# The external effects (email + Google) are NOT run inline before the caller commits. Instead each
# is persisted as a TRANSACTIONAL OUTBOX intent row in the SAME transaction as the booking mutation
# (:func:`enqueue_effect`), so it commits atomically with the booking — or rolls back with it, never
# firing for a booking that never persisted (the ordering bug the old best-effort inline wrapping
# could not close). A scheduler-driven poller (``services.outbox.drain_outbox``) executes the intent
# afterwards, at-least-once with idempotency (retries + dead-letter). The guest
# tokens are still minted in-txn here (they are DB rows, atomic with the booking, and the email
# payload needs their URLs); the 24 h reminder stays inline because its job re-checks the booking is
# still confirmed at fire time (self-healing against a rolled-back booking).


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
    """Wire create-time effects: mint tokens in-txn, enqueue Google + email intents, schedule the
    reminder. The email/Google effects are drained post-commit (durable outbox); a rolled-back
    booking drops their intents.
    """
    cancel_url, reschedule_url = await _mint_guest_links(
        session, booking=booking, effects=effects, now=now
    )
    await _enqueue_google(
        session,
        booking=booking,
        effects=effects,
        operation=GoogleOperation.UPSERT,
        event_type=event_type,
    )
    await _enqueue_email(
        session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
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
    """Wire reschedule-time effects: fresh tokens in-txn, enqueue the Google-move + reschedule email
    intents (drained post-commit), and re-schedule the reminder.
    """
    cancel_url, reschedule_url = await _mint_guest_links(
        session, booking=new, effects=effects, now=now
    )
    await _enqueue_google(
        session,
        booking=new,
        effects=effects,
        operation=GoogleOperation.RESCHEDULE,
        event_type=event_type,
        external_event_id=old.external_event_id,
    )
    await _enqueue_email(
        session,
        kind=NotificationKind.RESCHEDULE,
        booking=new,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
    )
    _schedule_reminder(effects, booking=new)


def _schedule_reminder(effects: BookingEffects, *, booking: Booking) -> None:
    """Schedule the 24 h reminder for ``booking`` if a runner is wired (F1-10); else skip.

    Best-effort (RF-10): a scheduler that raises must NEVER roll the committed booking back, so the
    scheduling call is guarded — on failure it is logged and the booking stands.
    """
    if effects.reminder_runner is None:
        return
    try:
        schedule_reminder(
            effects.reminder_runner,
            booking=booking,
            send_at=_to_utc(booking.start_at) - _REMINDER_LEAD,
        )
    except Exception:
        _logger.exception("booking %s: reminder scheduling failed (best-effort, kept)", booking.id)


async def _enqueue_email(  # noqa: PLR0913 - the composer needs the full booking + link context
    session: AsyncSession,
    *,
    kind: NotificationKind,
    booking: Booking,
    cancel_url: str | None,
    reschedule_url: str | None,
    locale: str = "es",
) -> None:
    """Enqueue the transactional-email intent for the booking — ALWAYS, not gated on a live sender.

    A booking's confirmation/cancellation/reschedule notice is domain-required, so the durable
    intent is persisted regardless of whether SMTP is configured at this instant; the drain worker
    sends it post-commit via the live sender (idempotent through the notification ledger) and simply
    retries — then dead-letters, surfacing the misconfiguration — if SMTP is momentarily absent.
    Gating the enqueue on the live sender would silently drop the notice, defeating durability. The
    intent carries the kind + guest links + locale + the SEQUENCE snapshot; the guest tokens were
    minted in-txn, so the links persist atomically with the booking.
    """
    await enqueue_effect(
        session,
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        effect=OutboxEffect.EMAIL,
        dedupe_key=email_dedupe_key(kind),
        payload={
            "kind": kind.value,
            "cancel_url": cancel_url,
            "reschedule_url": reschedule_url,
            "locale": locale,
            # Snapshot the SEQUENCE at the transition (F1-08): if a LATER mutation bumps the booking
            # before this email drains, the .ics must still carry the sequence of ITS transition, so
            # the chain's emails stay strictly increasing per UID regardless of drain interleaving.
            "sequence": booking.sequence,
        },
    )


async def _enqueue_google(  # noqa: PLR0913 - the sync operation + its event context are the contract
    session: AsyncSession,
    *,
    booking: Booking,
    effects: BookingEffects,
    operation: GoogleOperation,
    event_type: EventType | None = None,
    external_event_id: str | None = None,
) -> None:
    """Enqueue a Google-Calendar sync intent for the booking, when the host has a linked calendar.

    Gated only on a persisted ``connection`` (its id rides in the intent) — NOT on a live client:
    building the Google client is exclusively the executor's job (its ``service_factory``), so the
    producer stays decoupled from momentary Google availability. ``None`` means the host has no
    external calendar, so there is genuinely nothing to sync. The intent stores the primitives the
    drain worker rebuilds the event request from; a DELETE needs only the ``external_event_id``.
    """
    if effects.connection is None:
        return
    payload: dict[str, object] = {
        "operation": operation.value,
        "connection_id": str(effects.connection.id),
        "external_event_id": external_event_id,
    }
    if operation is not GoogleOperation.DELETE and event_type is not None:
        payload.update(
            {
                "summary": event_type.title,
                "start": _to_utc(booking.start_at).isoformat(),
                "end": _to_utc(booking.end_at).isoformat(),
                "timezone": booking.guest_timezone,
                "guest_email": booking.guest_email,
            }
        )
    await enqueue_effect(
        session,
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        effect=OutboxEffect.GOOGLE,
        dedupe_key=google_dedupe_key(operation),
        payload=payload,
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
