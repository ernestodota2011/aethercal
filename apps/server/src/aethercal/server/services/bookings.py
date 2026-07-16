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
Postgres-only ``test_booking_concurrency.py`` proves it end-to-end."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus, TimeInterval
from aethercal.server.db.models import Booking, EventType
from aethercal.server.db.models.booking import guest_columns
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.calendars import load_active_connections
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
from aethercal.server.services.slots import SlotsResult, compute_slots, day_is_at_cap
from aethercal.server.services.webhooks import enqueue_event
from aethercal.server.services.workflows import BookingTransition, apply_booking_transition

_logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
"""The ceiling on ``GET /bookings``. ==Unbounded, that route is an availability problem for the
OWNER
of the data==: every booking a business ever took — each carrying the guest's name, address and
free-text notes — materialised into one response, on a route anybody holding the key can call in a
loop. A caller-chosen ``limit`` with no maximum is the same unbounded query wearing a parameter, so
the maximum is enforced at the edge and the default is modest."""


# --------------------------------------------------------------------------------------
# Errors — each maps to one clean HTTP status at the router (RF-16, no internal leak).
# --------------------------------------------------------------------------------------


class BookingError(Exception):
    """Base class for booking-service errors the API maps to a clean HTTP status."""


class EventTypeNotFoundError(BookingError):
    """The event type does not exist for the tenant (→ HTTP 404)."""


class EventTypeInactiveError(BookingError):
    """The event type is deactivated, so it takes no new bookings (RF-14) (→ HTTP 404).

    ==404, not 409, and the API renders it with the SAME code and message as
    :class:`EventTypeNotFoundError`.== To a guest, a withdrawn service and a service that never
    existed must look identical, or the 404s become an oracle for enumerating which of a
    business's event types were switched off. It is a distinct class only so the OPERATOR — who
    is looking right at the row in their admin list — can be told the useful thing ("it is
    deactivated") instead of the baffling one ("not found").
    """


class BookingNotFoundError(BookingError):
    """No booking with that id exists for the tenant (→ HTTP 404)."""


class SlotUnavailableError(BookingError):
    """The requested slot is not on offer or is already booked (→ HTTP 409)."""


class DayFullError(BookingError):
    """The day has already reached the event type's ``max_per_day`` (RF-14) (→ HTTP 409).

    Its OWN error, not a flavour of :class:`SlotUnavailableError`, because "the day is full" and
    "that time is taken" are different facts about the world. Told the latter, a guest reasonably
    tries another hour — and every hour that day will refuse them, for a reason nothing ever states.
    """


class AvailabilityUnavailableError(BookingError):
    """The host's external calendar could not be established, so no slot may be offered (→ 503)."""


class BookingNotActiveError(BookingError):
    """The booking is not in a reschedulable state (e.g. already cancelled) (→ HTTP 409)."""


class BookingNotEndedError(BookingError):
    """The appointment has not finished yet, so it cannot be marked a no-show (→ HTTP 409)."""


# --------------------------------------------------------------------------------------
# Inputs — the request data and the injected side-effect dependencies.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BookingParams:
    """The guest-supplied inputs for a new booking (RF-07). ``end`` is derived from the duration.

    ``guest_phone_consent`` is a BOOLEAN here, never a timestamp: the caller reports *that* the box
    was ticked, and :func:`create_booking` stamps ``guest_phone_consent_at`` from the server's own
    clock. So a caller cannot back-date a tick, and cannot assert one for a number it did not also
    supply (see :func:`_consent_stamp`).
    """

    event_type_id: uuid.UUID
    start: datetime
    guest_name: str
    guest_email: str
    guest_timezone: str
    guest_notes: str | None = None
    answers: dict[str, Any] | None = None
    locale: str = "es"
    #: The phone typed into the booking form, in E.164 (validated at the edge by ``BookingCreate``),
    #: or ``None``. Whoever is booking typed it in; nobody verified they own it.
    guest_phone: str | None = None
    #: Whether the consent box was EXPLICITLY ticked on the form. Never assumed: an unticked box and
    #: an absent field mean the same thing — no consent. It records a TICK, not verified permission
    #: from the number's owner (declared gap — ``docs/phone-channels.md``).
    guest_phone_consent: bool = False
    #: The address this booking was made from, or ``None``. ==Only the PUBLIC router ever sets it==
    #: (``api/public.py``), because that is the only path a stranger can reach: the admin's own
    #: bookings and the tenant's API key carry no client address, and must not be throttled by one.
    #: It is what the per-IP daily cap — required at boot since RF-24 — finally has to count.
    source_ip: str | None = None


@dataclass(frozen=True, slots=True)
class BookingEffects:
    """The runtime dependencies for a booking's side-effects that are decided at request time.

    Injected so the core create/cancel/reschedule stays unit-testable. ``signer`` +
    ``booking_base_url`` are always present (the guest links are minted + built in-txn). The durable
    effects (email, Google sync) are NOT gated on a live client here — they are ENQUEUED to the
    outbox and the drain worker supplies the client, so a momentarily-absent SMTP/Google never drops
    a domain-required effect.

    There is no ``reminder_runner`` any more. The 24 h reminder used to be scheduled here onto a
    SECOND scheduler (APScheduler, with a Postgres jobstore) that carried its own idempotency
    barrier. That is now a tenant-editable **workflow rule** materialised into this same outbox, so
    a booking has exactly ONE thing that can decide to send it a reminder — the alternative was a
    guest receiving two, since the ledger key and the outbox dedupe key never knew about each other.

    And there is no ``connection`` field any more either. It used to be one, and the API layer had
    no way to fill it — the host is only known once the event type is loaded, INSIDE this service —
    so it was always ``None`` and every single booking skipped the Google sync without a word. The
    host's calendar is now resolved from the database here, where the host is actually known.
    """

    signer: GuestTokenSigner
    booking_base_url: str


# --------------------------------------------------------------------------------------
# Anti-double-booking layer 1 — per-host serialization lock (PostgreSQL only).
# --------------------------------------------------------------------------------------


def _host_lock_key(tenant_id: uuid.UUID, host_id: uuid.UUID) -> int:
    """A stable signed 64-bit key for the ``(tenant, host)`` advisory lock (RF-04).

    ``pg_advisory_xact_lock`` takes a signed ``bigint``; an 8-byte BLAKE2b digest read as a signed
    integer fits exactly and is deterministic across processes, so every booker for a given host
    contends on the same key."""
    digest = hashlib.blake2b(f"{tenant_id}:{host_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _serialize_host(
    session: AsyncSession, *, tenant_id: uuid.UUID, host_id: uuid.UUID
) -> None:
    """Serialize concurrent bookings for one host on PostgreSQL (RF-04, layer 1).

    Takes a transaction-scoped advisory lock so two concurrent create/reschedule transactions for
    the same host run one-after-another (each sees the other's committed rows on re-read), released
    automatically at transaction end. On SQLite (the offline test backend) this is a harmless no-op:
    SQLite serializes writes anyway."""
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


def _require_bookable(event_type: EventType) -> None:
    """Refuse to SELL a deactivated event type (RF-14).

    Guards the two paths that open a new booking — ``create_booking`` and ``reschedule_booking``.
    ==It deliberately does NOT guard ``cancel_booking`` or ``mark_no_show``:== those act on an
    appointment that already exists, and a business withdrawing a service must never leave its
    existing guests holding a booking that nobody is allowed to cancel.
    """
    if not event_type.active:
        raise EventTypeInactiveError(
            f"event type '{event_type.slug}' is deactivated and is not taking bookings; "
            "reactivate it to make it bookable again"
        )


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
    exclude_booking_id: uuid.UUID | None = None,
) -> None:
    """Confirm ``[start, end)`` is on offer for ``event_type`` (RF-03/RF-13).

    The window is padded by a day on each side so a slot whose local date differs from its UTC date
    is still computed; the request path injects no ``service_factory`` (RNF-6: read the busy cache
    only, never call Google in-band).

    ``exclude_booking_id`` is forwarded to the daily-cap count (RF-14) for the reschedule path: the
    booking being moved is still ``confirmed`` here, and it must not be counted as filling the day
    it is trying to leave. It does NOT free that booking's interval — the slot itself stays busy.
    """
    result = await compute_slots(
        session,
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        window_from=(start - timedelta(days=1)).date(),
        window_to=(end + timedelta(days=1)).date(),
        now=now,
        exclude_booking_id=exclude_booking_id,
    )
    _require_slot_on_offer(result, start=start, end=end)


async def _require_day_capacity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: EventType,
    start: datetime,
    exclude_booking_id: uuid.UUID | None = None,
) -> None:
    """Refuse the booking when ``start``'s day has already reached ``max_per_day`` (RF-14).

    .. rubric:: Why this exists when :func:`_validate_slot` would refuse anyway

    ``compute_slots`` no longer OFFERS a full day's slots, so a request for one would already fall
    out as :class:`SlotUnavailableError`. This gate runs FIRST anyway, for two reasons:

    * **It tells the truth.** "That time is no longer available" sends a guest hunting for another
      hour on a day that has no room at any hour. ``DayFullError`` names the actual reason.
    * **It is the fail-closed backstop.** The offering side and the accepting side now agree by
      construction (both count through ``services.slots``), but they are still two reads. If the
      filter ever regressed, this is what keeps the cap from being silently exceeded — and a cap
      that can be exceeded is exactly the class of bug this whole change is repairing.

    .. rubric:: Concurrency

    ==The daily cap has no database backstop.== The partial unique index enforces "one active
    booking per slot"; it knows nothing about "N per day", and no index can express it. So the
    per-host advisory lock (:func:`_serialize_host`, taken by both write paths BEFORE this runs) is
    the whole guard, and it is sufficient: an event type has exactly one host, so every booking that
    counts toward this cap contends on the same key. Two concurrent requests for a day's last place
    serialize; the loser acquires the lock only after the winner's transaction has ended, and its
    count — a READ COMMITTED read taken under the lock, exactly as the availability re-read is —
    then SEES the winner's committed row and refuses. On SQLite the lock is a no-op, which is safe
    for the same reason it is elsewhere here: SQLite serializes writes anyway.
    """
    if await day_is_at_cap(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        moment=start,
        exclude_booking_id=exclude_booking_id,
    ):
        raise DayFullError(
            f"{start.date().isoformat()} has reached its limit of {event_type.max_per_day} "
            "bookings for this event type"
        )


def _serialize_booking(booking: Booking) -> dict[str, object]:
    """A JSON-serializable snapshot of a booking for the webhook envelope (RF-17).

    Every value is a primitive/str/None so ``enqueue_event`` can canonicalize it for signing; the
    internal ``external_event_id`` is intentionally omitted from the public event."""
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


def _consent_stamp(params: BookingParams, *, now: datetime) -> datetime | None:
    """When the form's consent box was ticked — or ``None`` if it was not.

    This is the ONLY place ``bookings.guest_phone_consent_at`` is written on the create path, and it
    writes the SERVER's ``now``, never a client-supplied instant: the column is evidence that the
    box was ticked at THIS moment, so a client must not be able to author it.

    What it is evidence OF is narrow, and worth saying out loud: that whoever filled in this form
    ticked the box. NOT that the owner of the number agreed — nothing here verifies possession of
    the number (declared gap, ``docs/phone-channels.md``).

    A tick with no number is refused here rather than stamped. ``BookingCreate`` already rejects
    that payload at the edge (422), but the service is reachable without it — the admin builds
    :class:`BookingParams` directly — and a stamp on a row with no phone would assert agreement to
    be messaged at a number that does not exist. Two belts, because the thing on the other side of
    them is an unsolicited message to a real person's phone.
    """
    if not params.guest_phone_consent or params.guest_phone is None:
        return None
    return now


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
    # A DEACTIVATED event type is withdrawn from sale and takes no new bookings (RF-14). The row is
    # fetched unfiltered above and checked here, rather than through ``get_bookable_event_type``,
    # only so the OPERATOR can be told *why* — the guest is answered with an indistinguishable 404.
    _require_bookable(event_type)

    start = _to_utc(params.start)
    end = start + timedelta(seconds=event_type.duration_seconds)

    await _serialize_host(session, tenant_id=tenant_id, host_id=event_type.host_id)
    # Under the lock, so the count sees every committed rival (see :func:`_require_day_capacity`).
    # Before the slot check, so a guest whose day is FULL is told that, and not sent hunting for
    # another hour on a day where no hour can be had.
    await _require_day_capacity(session, tenant_id=tenant_id, event_type=event_type, start=start)
    await _validate_slot(
        session, tenant_id=tenant_id, event_type=event_type, start=start, end=end, now=now
    )

    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=end,
        status=BookingStatus.CONFIRMED,
        # The stamp moves WITH the status, in the same statement — never in a later one. It is what
        # licenses every outbound this booking will ever produce (B-05a), so a confirmed booking
        # that reached the database without it would be one nothing ever speaks for: no email, no
        # reminder, no webhook, no calendar event — and no error to say so.
        #
        # Today every booking is born confirmed, on every path (the public page, the admin and the
        # API key alike). When holds arrive (B-05b) this line becomes the ARBITER's: the payment
        # that wins the conditional UPDATE stamps it, and nothing else may.
        confirmed_at=now,
        guest_name=params.guest_name,
        guest_email=params.guest_email,
        guest_timezone=params.guest_timezone,
        guest_phone=params.guest_phone,
        guest_phone_consent_at=_consent_stamp(params, now=now),
        guest_notes=params.guest_notes,
        answers=dict(params.answers) if params.answers is not None else {},
        source_ip=params.source_ip,
    )
    await _insert_active(session, booking, start=start)
    await enqueue_event(
        session,
        booking=booking,
        event="booking.created",
        data=_serialize_booking(booking),
        now=now,
    )
    # The booking's workflow steps are materialised IN THIS TRANSACTION, exactly like the webhook
    # above: they are tenant-configured domain behaviour, not a runtime effect, so they are NOT
    # gated on the `effects` bundle. This is what carries RF-10 now that the APScheduler reminder is
    # gone — without it, every new booking would silently have no reminder at all.
    await apply_booking_transition(
        session,
        booking=booking,
        transition=BookingTransition.CONFIRM,
        now=now,
        locale=params.locale,
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
    transaction."""
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

    ==A booking that was never confirmed (a hold, ``confirmed_at is None``) is cancelled SILENTLY:==
    its slot is freed but nothing is announced — no webhook, no ``on_cancel`` workflow, no sequence
    bump. You do not announce the cancellation of an appointment nobody was ever told existed.
    """
    booking, event_type = await _lock_and_reload_booking(
        session, tenant_id=tenant_id, booking_id=booking_id
    )
    if booking.status == BookingStatus.CANCELLED:
        return booking  # already cancelled under the lock → no-op, no duplicate webhook (RF-04)

    if booking.confirmed_at is None:
        # ==A hold nobody paid for is being abandoned.== It was never announced, so its cancellation
        # is not announced either — you cannot retract an appointment nobody was ever told about.
        # Free the slot (that part of a cancel is real: ``status <> 'cancelled'`` reopens it), and
        # STOP. No ``booking.cancelled`` webhook, no ``apply_booking_transition(CANCEL)`` (which
        # would try to materialise the ``on_cancel`` workflow), and no iCal SEQUENCE bump for an
        # event that never existed.
        #
        # The funnels would suppress every one of those effects anyway (``confirmed_at`` is NULL) —
        # this is the root-cause short-circuit that never builds the announcement at all, rather
        # rather than building it and relying on the belt to throw it away. The guard keys on
        # ``confirmed_at``, never on ``status``: a CONFIRMED booking being cancelled reads
        # ``cancelled`` too and its guest is owed the notice (that path is below, untouched).
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = now
        await session.flush()
        return booking

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    # Bump the persisted iCal SEQUENCE so the cancellation .ics strictly supersedes the confirmation
    # (RFC 5545, F1-08); the drained cancellation email reads this value.
    booking.sequence += 1
    await session.flush()
    await enqueue_event(
        session,
        booking=booking,
        event="booking.cancelled",
        data=_serialize_booking(booking),
        now=now,
    )
    # CANCEL, not RESCHEDULE_PREDECESSOR: this is the OPERATION the guest asked for, so the
    # `on_cancel` step is materialised and everything else still queued is retired. A reschedule's
    # swap also leaves a booking cancelled, but it is NOT this transition — see services/workflows.
    await apply_booking_transition(
        session, booking=booking, transition=BookingTransition.CANCEL, now=now
    )
    if effects is not None:
        await _enqueue_google(
            session, booking=booking, event_type=event_type, operation=GoogleOperation.DELETE
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

    .. rubric:: The successor inherits the guest — INCLUDING their phone-consent stamp

    Every ``guest_*`` column is carried over, read off the model by :func:`guest_columns` rather
    than named in a literal here (a hand-copied list already dropped two of them in silence).

    ``guest_phone_consent_at`` is inherited DELIBERATELY, and it is worth arguing rather than
    assuming, because it is a record about a real person's agreement to be messaged. It is the same
    guest, the same phone number, and the same appointment — moved, not replaced. The consent they
    gave was to be messaged ABOUT THIS APPOINTMENT, and a reschedule is precisely the moment that
    matters most: it is the message that tells them the time changed and reminds them of the new
    one. Re-asking would be absurd (nobody re-consents to a reminder because the meeting moved an
    hour), and DROPPING it — which is what the old code did by accident — silently withdraws a
    consent the guest never withdrew, and ends the messages they asked for.

    ==Its meaning does not widen by being inherited.== It remains a stamp that the box was ticked on
    the form, at that original instant, by whoever filled it in — never proof that the OWNER of the
    number agreed (an unverified number is a DECLARED GAP, ``docs/phone-channels.md``). The stamp
    carried forward is the ORIGINAL tick's, not ``now``: back-dating it would be a lie, and
    re-stamping it would forge a fresh agreement nobody gave. A guest who never ticked the box
    inherits ``NULL`` and stays unmessaged, exactly as before.
    """
    old, event_type = await _lock_and_reload_booking(
        session, tenant_id=tenant_id, booking_id=booking_id
    )
    if old.status != BookingStatus.CONFIRMED:
        raise BookingNotActiveError("only a confirmed booking can be rescheduled")
    # A reschedule OPENS A NEW BOOKING (the successor row below), so it is a sale — and a
    # deactivated event type is withdrawn from sale (RF-14). Refused explicitly, rather than left to
    # fall out of an empty slot list, because it is the honest answer: the business publishes this
    # service on no day at all, so there is no time we could truthfully offer instead. ==Cancelling
    # is NOT gated on ``active``== (see :func:`cancel_booking`) — withdrawing a service must never
    # trap the guests already holding an appointment for it.
    _require_bookable(event_type)

    start = _to_utc(new_start)
    end = start + timedelta(seconds=event_type.duration_seconds)

    # RF-14. ``old`` is excluded from the daily count on both gates: it is still ``confirmed`` at
    # this point and is about to be cancelled, so counting it would let a booking be blocked by
    # itself — a capped day could never be rescheduled WITHIN. The exemption is exactly one booking
    # wide: a day filled by somebody else still refuses (``DayFullError``).
    await _require_day_capacity(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        start=start,
        exclude_booking_id=old.id,
    )
    await _validate_slot(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        start=start,
        end=end,
        now=now,
        exclude_booking_id=old.id,
    )

    new = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=end,
        status=BookingStatus.CONFIRMED,
        # EVERY guest column, DERIVED from the model (:func:`guest_columns`) instead of named here.
        # The literal that used to sit in this spot listed four of the six and silently dropped the
        # other two — see that function's docstring for what it cost the guest.
        **{name: getattr(old, name) for name in guest_columns()},
        # ``answers`` is COPIED, not aliased: one shared dict would let a later edit of either row
        # rewrite the other's history.
        answers=dict(old.answers),
        # INHERITED, never re-minted. The successor is the SAME appointment, moved — so it carries
        # the instant that appointment was first confirmed, and a reschedule does not change that
        # fact. Re-stamping it with ``now`` would forge a new confirmation; leaving it NULL would be
        # far worse — the belt would treat the moved booking as an unannounced hold and SILENCE it,
        # so the very email telling the guest their time changed would never be sent.
        #
        # A predecessor is always confirmed here (the guard above refuses anything else), so this is
        # never NULL in practice. It is the same chain B-05b re-points the payment onto.
        confirmed_at=old.confirmed_at,
        rescheduled_from_id=old.id,
        # Carry the predecessor's iCal SEQUENCE forward + 1 so successive reschedules strictly
        # increase (RFC 5545, F1-08); the drained reschedule email snapshots this value.
        sequence=old.sequence + 1,
        # Inherit the predecessor's stable UID so every update addresses the SAME calendar event —
        # without this the strictly-increasing sequence would be spread across distinct UIDs and a
        # client would treat each reschedule as a brand-new event instead of an update.
        ical_uid=old.ical_uid,
        # ==The successor inherits the ADDRESS the appointment came from, and it must.==
        #
        # ``source_ip`` does not start with ``guest_``, so :func:`guest_columns` — which carries
        # every
        # guest field across this swap precisely because a hand-written list already drifted once —
        # does not see it. Named here, deliberately, rather than renamed to fit the prefix: it is an
        # observation the SERVER made about a request, not a value the guest supplied, and the purge
        # classifies it on those terms too.
        #
        # Dropping it would open a free reset of the per-IP cap: book, reschedule, and the successor
        # (whose reminders are the ones that actually go out) has no address to count against. The
        # ceiling would hold perfectly in a unit test and mean nothing against anyone who read the
        # code. It is the ORIGINAL address, not the rescheduling one — the appointment is the same
        # appointment, moved, and the traffic being bounded is the traffic that created it.
        source_ip=old.source_ip,
    )
    await _swap_booking(session, old=old, new=new, now=now, start=start)
    await enqueue_event(
        session,
        booking=new,
        event="booking.rescheduled",
        data=_serialize_booking(new),
        now=now,
    )
    # Two transitions, and the FIRST is the one that is easy to forget: the predecessor's still-
    # pending steps are retired — including the staleness-EXEMPT ones, or its `after_end` follow-up
    # would still fire, at the hour of a meeting that never happened. Then the successor (a NEW row,
    # so a plain INSERT: nothing can conflict) gets its own fresh steps.
    await apply_booking_transition(
        session, booking=old, transition=BookingTransition.RESCHEDULE_PREDECESSOR, now=now
    )
    await apply_booking_transition(
        session, booking=new, transition=BookingTransition.RESCHEDULE_SUCCESSOR, now=now
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
    cancelled without its replacement."""
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
# mark_no_show (RF-25)
# --------------------------------------------------------------------------------------


async def mark_no_show(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    now: datetime,
) -> Booking:
    """Mark a finished appointment as a no-show (RF-25). Idempotent.

    Only from ``confirmed``, and only once the appointment has ENDED — "no-show" is a statement
    about an event that already happened, so allowing it beforehand would let a host
    cancel-by-another-name
    (and, since a no-show keeps its slot, quietly destroy the guest's booking without freeing it).

    ==The slot stays occupied.== The status change is deliberately NOT a release: the
    time has passed, so freeing it would corrupt history and let a booking be written retroactively
    over it. This is exactly why the ``WHERE status <> 'cancelled'`` partial index needed no change.

    Fans out ``booking.no_show`` (RF-25) in the same transaction, so a subscriber's CRM finally sees
    the one lifecycle outcome it was blind to: it learned about a cancellation and a reschedule, but
    a guest who simply never turned up was invisible to it.

    Raises :class:`BookingNotFoundError` (404), :class:`BookingNotActiveError` (409, not confirmed)
    or :class:`BookingNotEndedError` (409, the appointment is still running or in the future).
    """
    booking, _event_type = await _lock_and_reload_booking(
        session, tenant_id=tenant_id, booking_id=booking_id
    )
    if booking.status == BookingStatus.NO_SHOW:
        return booking  # already marked under the lock → no-op (idempotent)
    if booking.status != BookingStatus.CONFIRMED:
        raise BookingNotActiveError("only a confirmed booking can be marked a no-show")
    if _to_utc(booking.end_at) > now:
        raise BookingNotEndedError("a booking can only be marked a no-show after it has ended")

    booking.status = BookingStatus.NO_SHOW
    booking.no_show_at = now
    await session.flush()
    # Fan the event out (RF-25) in the SAME transaction as the status change — exactly like the
    # cancel and the reschedule. The flush above is what makes `_serialize_booking` carry the status
    # this transition just wrote instead of the `confirmed` it came in with.
    #
    # Queued only on the REAL transition: the idempotent early return above never reaches this line,
    # so a retried admin click cannot tell a subscriber the guest failed to show up TWICE for one
    # appointment — nor inflate the host's no-show rate with a duplicate.
    await enqueue_event(
        session,
        booking=booking,
        event="booking.no_show",
        data=_serialize_booking(booking),
        now=now,
    )
    # Materialise the `on_no_show` step and VOID the pending `after_end` follow-up — otherwise the
    # guest who did not show up receives "thanks for meeting with us".
    await apply_booking_transition(
        session, booking=booking, transition=BookingTransition.NO_SHOW, now=now
    )
    return booking


# --------------------------------------------------------------------------------------
# Read paths.
# --------------------------------------------------------------------------------------


async def get_booking(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_id: uuid.UUID
) -> Booking | None:
    """Return the tenant's booking by id, or ``None`` if absent (tenant-scoped)."""
    return await _load_booking(session, tenant_id=tenant_id, booking_id=booking_id)


def _booking_filters(
    tenant_id: uuid.UUID,
    *,
    status: BookingStatus | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    """The WHERE clauses shared by a page and its count. ==Declared once, on purpose.==

    A ``total`` computed over a different predicate from the ``items`` beside it is worse than no
    total at all: the caller pages towards a number that never arrives, or stops early believing
    they
    have everything. Two predicates would eventually disagree, and the disagreement would be silent.
    """
    clauses: list[Any] = [Booking.tenant_id == tenant_id]
    if status is not None:
        clauses.append(Booking.status == status)
    if date_from is not None:
        clauses.append(Booking.start_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to is not None:
        upper = datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
        clauses.append(Booking.start_at < upper)
    return clauses


async def list_bookings(  # noqa: PLR0913 - each parameter is one filter of a single query
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: BookingStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Booking]:
    """List the tenant's bookings, optionally filtered by ``status`` and a start-date window.

    ``date_from`` / ``date_to`` are inclusive calendar dates matched against ``start_at`` (UTC).
    Ordered by start then id — which is what makes ``offset`` mean anything: paging over an
    unordered
    query is how one row gets served twice and another is never served at all.

    ``limit=None`` means "no ceiling", and it stays the default because the ADMIN reads through here
    with a date window it chose itself. ==The HTTP route never passes ``None``==: it is capped at
    :data:`MAX_PAGE_SIZE` at the edge (``api/bookings.py``).
    """
    stmt = (
        select(Booking)
        .where(*_booking_filters(tenant_id, status=status, date_from=date_from, date_to=date_to))
        .order_by(Booking.start_at, Booking.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def count_bookings(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: BookingStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    """How many bookings those same filters match — the ``total`` that travels beside a page.

    Without it, a page is a SILENT TRUNCATION: a caller asks for their bookings, receives a hundred,
    and has no way to learn that four thousand exist.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(*_booking_filters(tenant_id, status=status, date_from=date_from, date_to=date_to))
    )
    return int(total or 0)


# --------------------------------------------------------------------------------------
# Side-effects (F1-06/07/08/10) — durable, transactional, post-commit.
# --------------------------------------------------------------------------------------

# The external effects (email + Google) are NOT run inline before the caller commits. Instead each
# is persisted as a TRANSACTIONAL OUTBOX intent row in the SAME transaction as the booking mutation
# (:func:`enqueue_effect`), so it commits atomically with the booking — or rolls back with it, never
# firing for a booking that never persisted (the ordering bug the old best-effort inline wrapping
# could not close). A scheduler-driven poller (``services.outbox.drain_outbox``) executes the intent
# afterwards, at-least-once with idempotency (retries + dead-letter). The guest tokens are still
# minted in-txn here (they are DB rows, atomic with the booking, and the email payload needs their
# URLs); the 24 h reminder stays inline because its job re-checks the booking is still confirmed at
# fire time (self-healing against a rolled-back booking).


def _guest_link(
    base_url: str,
    action: str,
    token: str,
    *,
    booking_id: uuid.UUID,
    event_type_id: uuid.UUID | None = None,
) -> str:
    """Build the public self-serve link we email the guest (F1-06/09/10).

    The signed token *authorises* the action; ``booking`` (and ``event_type``, for a reschedule) are
    the context the page needs to render anything at all. Minting the token without them produced a
    link that always answered "missing context", so no guest could ever cancel or reschedule from
    their email. Each half was internally consistent and unit-tested, which is exactly why only a
    test crossing the seam could see it.
    """
    params = {"token": token, "booking": str(booking_id)}
    if event_type_id is not None:
        params["event_type"] = str(event_type_id)
    return f"{base_url.rstrip('/')}/{action}?{urlencode(params)}"


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
        _guest_link(
            effects.booking_base_url,
            "cancel",
            cancel,
            booking_id=booking.id,
        ),
        _guest_link(
            effects.booking_base_url,
            "reschedule",
            reschedule,
            booking_id=booking.id,
            event_type_id=booking.event_type_id,
        ),
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
    booking drops their intents."""
    cancel_url, reschedule_url = await _mint_guest_links(
        session, booking=booking, effects=effects, now=now
    )
    await _enqueue_google(
        session, booking=booking, event_type=event_type, operation=GoogleOperation.UPSERT
    )
    await _enqueue_email(
        session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
        locale=locale,
    )


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
        session, booking=new, event_type=event_type, operation=GoogleOperation.RESCHEDULE
    )
    await _enqueue_email(
        session,
        kind=NotificationKind.RESCHEDULE,
        booking=new,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
    )


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
    minted in-txn, so the links persist atomically with the booking."""
    await enqueue_effect(
        session,
        booking=booking,
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


async def _chain_has_external_event(session: AsyncSession, booking: Booking) -> bool:
    """True when this booking — or an ancestor it was rescheduled from — already has a live event.

    A reschedule successor holds no event of its own until its own sync drains, so the walk up the
    ``rescheduled_from_id`` chain is what makes "this chain is already in someone's calendar"
    answerable at ENQUEUE time, which is when the decision to sync at all gets made.
    """
    current: Booking | None = booking
    seen: set[uuid.UUID] = set()
    while current is not None and current.id not in seen:
        if current.external_event_id is not None:
            return True
        seen.add(current.id)
        if current.rescheduled_from_id is None:
            return False
        current = await session.get(Booking, current.rescheduled_from_id)
    return False


async def _enqueue_google(
    session: AsyncSession,
    *,
    booking: Booking,
    event_type: EventType,
    operation: GoogleOperation,
) -> None:
    """Enqueue a Google-Calendar sync intent for the booking — RF-11, the link that was missing.

    THE GATE IS THE EXISTENCE OF A CONNECTED CALENDAR, and the two ways that can read "no calendar"
    are kept apart on purpose:

    * The host has NO active connection → there is genuinely nothing to sync (the self-hoster who
      never linked Google, RNF-9). No intent is enqueued; the booking is complete. Benign, logged at
      debug.
    * The host HAS a connection now, so the intent IS enqueued — and if the calendar cannot be
      resolved LATER, at drain time, the effect raises (``CalendarTargetMissingError`` /
      ``AmbiguousCalendarTargetError``): it retries, dead-letters, and lands in the outbox backlog
      with an error log. It is never quietly marked delivered. Before this wave both cases collapsed
      into one silent ``return``, which is precisely why no booking ever reached a calendar and no
      one noticed.

    ⚠️ **A CHAIN THAT ALREADY HAS AN EVENT IS ALWAYS SYNCED**, whatever the host's calendars look
    like today. What a DELETE — or the move half of a RESCHEDULE — must act on is not the current
    configuration; it is the event this booking ALREADY put in someone's calendar. A host who
    revokes their Google account between the confirmation and the cancellation has no active
    connection, so gating on that alone would drop the intent entirely: the guest is cancelled, the
    meeting stays in the host's calendar forever, and nobody is ever told. The drain resolves the
    event's RECORDED home (``_event_home``); if even that cannot be reached, the intent
    dead-letters — which is a signal. Silence is not.

    Only the HOST is captured here (``host_id``), never a specific connection id: the exact target
    calendar is resolved at drain time from the live configuration, so there is one source of truth
    for "where does this event go" instead of a snapshot that can rot between enqueue and drain. The
    live client is exclusively the executor's job (its ``service_factory``), so the producer stays
    decoupled from momentary Google availability. A DELETE carries no event data — the drain
    resolves the event (and the calendar it lives in) from the booking chain.
    """
    host_id = event_type.host_id
    connections = await load_active_connections(
        session, tenant_id=booking.tenant_id, user_id=host_id
    )
    if not connections and not await _chain_has_external_event(session, booking):
        _logger.debug(
            "booking %s: host %s has no connected calendar and the chain has no external event; "
            "nothing to sync",
            booking.id,
            host_id,
        )
        return

    payload: dict[str, object] = {"operation": operation.value, "host_id": str(host_id)}
    if operation is not GoogleOperation.DELETE:
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
        booking=booking,
        effect=OutboxEffect.GOOGLE,
        dedupe_key=google_dedupe_key(operation),
        payload=payload,
    )


__all__ = [
    "AvailabilityUnavailableError",
    "BookingEffects",
    "BookingError",
    "BookingNotActiveError",
    "BookingNotEndedError",
    "BookingNotFoundError",
    "BookingParams",
    "DayFullError",
    "EventTypeInactiveError",
    "EventTypeNotFoundError",
    "SlotUnavailableError",
    "cancel_booking",
    "count_bookings",
    "create_booking",
    "get_booking",
    "list_bookings",
    "mark_no_show",
    "reschedule_booking",
]
