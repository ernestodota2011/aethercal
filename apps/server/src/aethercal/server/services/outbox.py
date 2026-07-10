"""The transactional-outbox service: enqueue an effect intent in-txn, drain it post-commit (F1-05).

This is the post-commit half of the fix the booking service's inline best-effort wrapping could not
make: a booking's external effects (the confirmation/cancellation/reschedule **email**, the **Google
Calendar** sync) are no longer called inline before the caller commits — where an effect could fire
for a booking whose transaction then rolls back. Instead the booking service persists an
:class:`~aethercal.server.db.models.outbox.Outbox` *intent* row in the SAME transaction as the
booking mutation (:func:`enqueue_effect`), and a scheduler-driven poller (:func:`drain_outbox`)
executes the intent afterwards.

The design mirrors the durable webhook-delivery queue exactly:

* **enqueue** — an idempotent insert (unique ``dedupe_key`` per booking); a duplicate is a no-op.
* **drain** — select the due rows (``pending``, or ``failed`` past ``next_retry_at``), *claim* them
  with ``FOR UPDATE SKIP LOCKED`` (so two overlapping ticks never double-run one intent — a no-op on
  SQLite, which serializes writers), and run each through an injected ``execute`` callable inside
  its OWN ``SAVEPOINT`` (a failing effect rolls back only its own partial writes, isolated from the
  of the batch and from the retry bookkeeping). Success marks ``delivered``; a failure retries with
  exponential backoff until ``max_attempts``, then parks the row ``dead``.

Delivery is **at-least-once with best-effort dedup**, not exactly-once. The effect handlers reduce
duplicates — the email handler reserves the :class:`~aethercal.server.db.models.SentNotification`
ledger row (so a re-drain of an already-committed send is a no-op) and the Google handler reconciles
to the booking's current state — but a crash in the narrow window AFTER an external effect succeeds
and BEFORE its outbox row commits ``delivered`` will replay the effect (a duplicate email / calendar
event). Closing that window fully needs provider-side idempotency (an SMTP idempotency key, a
deterministic Google event id or search-before-create); until the providers are wired live that
residual is accepted (a duplicate confirmation is safer than a missing one) and documented, matching
the webhook queue's at-least-once contract. ``execute`` is fully injected so the mechanism is
unit-testable offline with a fake; :func:`make_booking_effect_executor` builds the live dispatcher
(email → SMTP, Google → the calendar client).

Ops residual: an intent that exhausts ``max_attempts`` is parked ``dead`` (a distinct ``error`` log
marks it) and is NOT retried automatically. Metrics/alerting on the ``dead`` state and a safe
requeue/replay operation are the remaining operational surface (deferred), so a dead intent is
visible in logs today but needs a human to replay it.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import case, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import Booking, ExternalConnection, Outbox
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.integrations.smtp.sender import EmailSender
from aethercal.server.services.calendars import (
    ServiceFactory,
    create_event_for_booking,
    delete_event_for_booking,
    reschedule_event_for_booking,
)
from aethercal.server.services.notifications import send_booking_notification

_logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 30
"""First-retry delay; each subsequent failure doubles it (30s, 60s, 120s, ...)."""

BACKOFF_CAP_SECONDS = 3600
"""Upper bound on a single backoff step (one hour)."""

DEFAULT_MAX_ATTEMPTS = 6
"""Attempts before an intent is parked as ``dead``."""

DEFAULT_DRAIN_BATCH_SIZE = 100
"""Max intents one drain pass claims + processes, so a tick's open transaction (and its external
I/O) is bounded; the next tick picks up the rest. ``FOR UPDATE SKIP LOCKED`` lets extra workers run
disjoint batches concurrently."""

_PENDING = "pending"
_FAILED = "failed"
_DELIVERED = "delivered"
_DEAD = "dead"


class OutboxEffect(StrEnum):
    """The effect kinds the outbox carries (the ``Outbox.effect`` discriminator)."""

    EMAIL = "email"
    GOOGLE = "google"


class GoogleOperation(StrEnum):
    """The Google-Calendar operations an outbox intent can request."""

    UPSERT = "upsert"
    RESCHEDULE = "reschedule"
    DELETE = "delete"


# The injected effect runner: given the drain's session, an intent row, and the tick's ``now``, it
# performs the side-effect (or raises to trigger a retry). Injected so the drain is testable offline
# with a fake.
OutboxExecutor = Callable[[AsyncSession, Outbox, datetime], Awaitable[None]]


def backoff_delay(
    attempts: int,
    *,
    base: int = BACKOFF_BASE_SECONDS,
    cap: int = BACKOFF_CAP_SECONDS,
) -> timedelta:
    """Exponential backoff after the ``attempts``-th failure (1-based): ``base * 2**(attempts-1)``.

    Capped at ``cap`` seconds so a long-broken effect never schedules an absurd retry.
    """
    exponent = max(attempts - 1, 0)
    return timedelta(seconds=min(base * (2**exponent), cap))


def email_dedupe_key(kind: NotificationKind) -> str:
    """The idempotency key for an email intent (one per booking + notification kind)."""
    return f"{OutboxEffect.EMAIL.value}:{kind.value}"


def google_dedupe_key(operation: GoogleOperation) -> str:
    """The idempotency key for a Google-sync intent (one per booking + operation)."""
    return f"{OutboxEffect.GOOGLE.value}:{operation.value}"


class OutboxDeferred(Exception):
    """Raised by an effect handler to POSTPONE its intent without consuming an attempt.

    The effect is not failing — it waits on a sibling intent to run first (e.g. an email waiting on
    its booking's Google Meet link). The drain reschedules it soon and leaves ``attempts`` alone, so
    a legitimate wait never counts toward the dead-letter budget.
    """


DEFER_DELAY_SECONDS = 30
"""How soon a deferred (dependency-waiting) intent is retried."""


@dataclass
class OutboxReport:
    """The outcome of one :func:`drain_outbox` pass: the intent ids by terminal/retry bucket."""

    delivered: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    dead: list[uuid.UUID] = field(default_factory=list)
    deferred: list[uuid.UUID] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        """How many intents this pass actually tried to run to completion (excludes deferrals)."""
        return len(self.delivered) + len(self.failed) + len(self.dead)


async def enqueue_effect(  # noqa: PLR0913 - the intent's identity + payload are the keyword contract
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    effect: OutboxEffect,
    dedupe_key: str,
    payload: dict[str, object],
) -> Outbox | None:
    """Persist a ``pending`` effect intent for a booking, INSIDE the caller's transaction (F1-05).

    Idempotent on ``(tenant_id, booking_id, dedupe_key)``: the insert runs in a ``SAVEPOINT`` and a
    unique-constraint conflict (the same transition already enqueued this exact intent) returns
    ``None`` without poisoning the transaction. Flushes; the caller owns the commit, so the intent
    commits atomically with the booking, or rolls back with it (never fires for a booking
    that never persisted). Returns the created row, or ``None`` when it was already queued.
    """
    row = Outbox(
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=effect.value,
        dedupe_key=dedupe_key,
        payload=payload,
        status=_PENDING,
        attempts=0,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    return row


async def drain_outbox(
    session: AsyncSession,
    *,
    now: datetime,
    execute: OutboxExecutor,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    batch_size: int = DEFAULT_DRAIN_BATCH_SIZE,
) -> OutboxReport:
    """Run one bounded batch of due intents through ``execute`` and record the outcome. Returns an
    :class:`OutboxReport`.

    "Due" = status ``pending``, or ``failed`` with ``next_retry_at`` unset or ``<= now``. At most
    ``batch_size`` rows are claimed per pass (so a tick's open transaction and its external I/O stay
    bounded; the next tick drains the rest), each with ``FOR UPDATE SKIP LOCKED`` (so a concurrent
    tick never double-runs it — and can drain a disjoint batch; harmless no-op on SQLite) and
    executed inside its own ``SAVEPOINT``: a raising effect rolls back only its own partial writes
    (e.g. an email ledger reservation), leaving the rest of the batch and the retry bookkeeping
    intact, and is retried with exponential backoff until ``max_attempts`` (then ``dead``). Flushes
    the updated rows; the caller owns the commit.
    """
    due = (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.status.in_((_PENDING, _FAILED)),
                or_(Outbox.next_retry_at.is_(None), Outbox.next_retry_at <= now),
            )
            .order_by(
                Outbox.created_at,
                # Within one instant (intents enqueued in the same transaction share a stamp), run
                # the Google sync BEFORE the email — so the confirmation/reschedule notice carries
                # the Meet link the sync just wrote onto the booking: a deterministic causal order,
                # not the non-deterministic tie-break the created_at stamp alone would leave.
                case((Outbox.effect == OutboxEffect.GOOGLE.value, 0), else_=1),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()

    report = OutboxReport()
    for row in due:
        try:
            # The effect's own SAVEPOINT: on failure it rolls back to here (undoing any partial
            # write such as a reserved notification-ledger row), so a retry starts clean and one
            # poisoned effect never aborts the sibling intents or the bookkeeping below.
            async with session.begin_nested():
                await execute(session, row, now)
        except OutboxDeferred:
            # Waiting on a dependency (e.g. an email waiting for its Google Meet link): reschedule
            # soon WITHOUT counting an attempt, so a legitimate wait never dead-letters.
            row.next_retry_at = now + timedelta(seconds=DEFER_DELAY_SECONDS)
            report.deferred.append(row.id)
            continue
        except Exception:
            row.attempts += 1
            row.last_attempt_at = now
            if row.attempts >= max_attempts:
                row.status = _DEAD
                row.next_retry_at = None
                report.dead.append(row.id)
                # A distinct, louder line: this intent is PARKED — no further automatic retry. It
                # needs operator attention (metrics/alerting on the ``dead`` state + a safe replay
                # are the remaining ops surface; see the module residual note).
                _logger.error(
                    "outbox intent %s (%s) for booking %s DEAD after %d attempts; parked, no retry",
                    row.id,
                    row.effect,
                    row.booking_id,
                    row.attempts,
                )
            else:
                row.status = _FAILED
                row.next_retry_at = now + backoff_delay(row.attempts)
                report.failed.append(row.id)
                _logger.exception(
                    "outbox intent %s (%s) for booking %s failed; will retry",
                    row.id,
                    row.effect,
                    row.booking_id,
                )
            continue

        row.attempts += 1
        row.last_attempt_at = now
        row.status = _DELIVERED
        row.next_retry_at = None
        report.delivered.append(row.id)

    await session.flush()
    return report


# --------------------------------------------------------------------------------------
# Live effect handlers + the dispatcher the scheduler tick injects as ``execute``.
# --------------------------------------------------------------------------------------


async def run_email_effect(
    session: AsyncSession, outbox: Outbox, now: datetime, *, sender: EmailSender
) -> None:
    """Execute an email intent: send the ``kind`` notification for its booking (idempotent).

    Reloads the booking (a vanished one — e.g. a cascade delete — is a silent no-op) and delegates
    to :func:`send_booking_notification`, whose reserve-first ledger makes a re-drain never mail
    twice. ``cancel_url`` / ``reschedule_url`` / ``locale`` ride in the intent payload (the guest
    tokens were minted in-txn at enqueue time); the persisted booking ``sequence`` drives SEQUENCE.

    DISCARDS a STALE notification: with at-least-once retries a confirmation/reschedule that kept
    failing could otherwise deliver AFTER a later cancellation or reschedule already went out — the
    guest must never receive a "confirmed"/"rescheduled" once the chain has moved on. So a
    confirmation/reschedule whose booking is no longer the chain's live member (superseded → its row
    is cancelled) is dropped (marked delivered, never sent). A CANCELLATION is the terminal, legit
    transition and is ALWAYS sent — it is never discarded as stale.

    DEFERS (raising :class:`OutboxDeferred`, no attempt consumed) while the booking still has an
    undelivered Google sync that will produce its Meet link — so the notice carries the link even if
    the sync only succeeds on a later retry, not only when it drains first. Once the sync delivers
    (link set) or dead-letters (no longer pending), the email proceeds.
    """
    booking = await session.get(Booking, outbox.booking_id)
    if booking is None:  # pragma: no cover - defensive: the FK cascade makes this near-impossible
        return
    payload = outbox.payload
    kind = NotificationKind(payload["kind"])
    if kind in (NotificationKind.CONFIRMATION, NotificationKind.RESCHEDULE) and not (
        await _is_chain_current(session, booking)
    ):
        # A later transition superseded this booking: drop the stale notice (never mail a
        # "confirmed" after a "cancelled" / for a replaced slot). Cancellation still sends below.
        return
    if booking.meeting_url is None and await _awaiting_meeting_sync(session, booking.id):
        raise OutboxDeferred(f"email for booking {booking.id} awaits its Google Meet link")
    await send_booking_notification(
        session,
        kind=kind,
        booking=booking,
        cancel_url=payload.get("cancel_url"),
        reschedule_url=payload.get("reschedule_url"),
        sender=sender,
        now=now,
        locale=payload.get("locale", "es"),
        # Use the sequence snapshotted at the transition, not the booking's live (possibly later-
        # bumped) value, so the chain's emails stay strictly increasing per UID (F1-08).
        sequence=payload.get("sequence"),
    )


async def _awaiting_meeting_sync(session: AsyncSession, booking_id: uuid.UUID) -> bool:
    """True while a non-terminal Google intent that WOULD write the booking's Meet link is queued.

    Only an ``upsert``/``reschedule`` (which set ``meeting_url``) counts — a ``delete`` never blocks
    an email. A dead-lettered sync no longer counts, so a permanently-failing Google never wedges an
    email forever (it goes out without the link, degraded but not lost).
    """
    rows = (
        await session.scalars(
            select(Outbox).where(
                Outbox.booking_id == booking_id,
                Outbox.effect == OutboxEffect.GOOGLE.value,
                Outbox.status.in_((_PENDING, _FAILED)),
            )
        )
    ).all()
    producing = {GoogleOperation.UPSERT.value, GoogleOperation.RESCHEDULE.value}
    return any(row.payload.get("operation") in producing for row in rows)


def _chain_lock_key(ical_uid: str) -> int:
    """A stable signed 64-bit advisory-lock key for a booking chain (its shared ``ical_uid``)."""
    digest = hashlib.blake2b(ical_uid.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _serialize_google_chain(session: AsyncSession, ical_uid: str) -> None:
    """Serialize a booking chain's Google effects across drain workers (PostgreSQL only).

    ``FOR UPDATE SKIP LOCKED`` lets two workers claim DIFFERENT rows of the same booking (its create
    and its cancel) at once, which could race an event's create against its delete and orphan it. A
    transaction-scoped advisory lock keyed by the chain's ``ical_uid`` makes those effects run
    one-after-another (the loser re-reads the committed state and reconciles). No-op on SQLite (the
    offline backend serializes writers), so the single-process deployment and tests are unaffected.
    """
    if session.get_bind().dialect.name != "postgresql":  # pragma: no cover - offline is SQLite
        return
    await session.execute(  # pragma: no cover - live: exercised only against a real PostgreSQL
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _chain_lock_key(ical_uid)}
    )


async def run_google_effect(
    session: AsyncSession, outbox: Outbox, now: datetime, *, service_factory: ServiceFactory
) -> None:
    """Execute a Google-sync intent: create / reschedule / delete the booking's calendar event.

    Rebuilds the live client from the connection referenced in the payload (via ``service_factory``,
    exactly as the busy-cache refresh does) and writes the resulting ``external_event_id`` /
    ``meeting_url`` back onto the booking. A missing booking or connection is a no-op; a Google
    failure raises (:class:`CalendarSyncError`) so the intent retries. All Google effects of one
    booking chain are serialized by a per-``ical_uid`` advisory lock (multi-worker safety), and the
    booking is re-read under it so the reconciliation below sees the committed state.
    """
    booking = await session.get(Booking, outbox.booking_id)
    if booking is None:  # pragma: no cover - defensive: the FK cascade makes this near-impossible
        return
    await _serialize_google_chain(session, booking.ical_uid)
    await session.refresh(booking)
    payload = outbox.payload
    connection = await session.get(ExternalConnection, uuid.UUID(str(payload["connection_id"])))
    if (
        connection is None
    ):  # pragma: no cover - defensive: a revoked connection between enqueue+drain
        return
    service = service_factory(connection)
    operation = GoogleOperation(payload["operation"])
    # Resolve the target event id at DRAIN time from current DB state, never from the enqueue-time
    # snapshot: an intent enqueued before the booking's CREATE drained would have captured a NULL id
    # (the event did not exist yet); the create having run first (or the reschedule predecessor) has
    # populated ``external_event_id`` by then.
    external_event_id = await _resolve_event_id(session, booking, payload)

    if operation is GoogleOperation.DELETE:
        if external_event_id is not None:
            await delete_event_for_booking(
                connection=connection, external_event_id=external_event_id, service=service
            )
        return

    # Reconcile to the chain's CURRENT desired state rather than trusting drain order: a create/move
    # runs ONLY for the booking that is the chain's live (non-cancelled) member. Any predecessor a
    # reschedule already replaced is skipped — even if its own UPSERT/RESCHEDULE drains AFTER the
    # successor's (two workers, inverted order) — so a replaced predecessor never (re)creates an
    # event the chain moved on from. This is order-independent (no reliance on the ``created_at``
    # tie-break) and, with the per-``ical_uid`` advisory lock above, multi-worker safe.
    if not await _is_chain_current(session, booking):
        return

    request = _meet_request_from_payload(payload)
    if operation is GoogleOperation.RESCHEDULE and external_event_id is not None:
        new_id, meeting_url = await reschedule_event_for_booking(
            connection=connection,
            external_event_id=external_event_id,
            request=request,
            service=service,
        )
    else:
        new_id, meeting_url = await create_event_for_booking(
            connection=connection, request=request, service=service
        )
    booking.external_event_id = new_id
    booking.meeting_url = meeting_url
    await session.flush()


async def _is_chain_current(session: AsyncSession, booking: Booking) -> bool:
    """True iff ``booking`` is the chain's single live (non-cancelled) member — the one a create or
    move acts for. A reschedule successor inherits its predecessor's ``ical_uid``, so the chain has
    one UID; the sole non-cancelled row is the current booking. A replaced predecessor (now
    cancelled), or an ambiguous 0/>1-active state (a conservative skip), returns ``False``.
    """
    active = (
        await session.scalars(
            select(Booking.id).where(
                Booking.ical_uid == booking.ical_uid,
                Booking.status != BookingStatus.CANCELLED,
            )
        )
    ).all()
    return set(active) == {booking.id}


async def _resolve_event_id(
    session: AsyncSession, booking: Booking, payload: dict[str, object]
) -> str | None:
    """The Google event id to act on, resolved at drain time (falls back to the payload snapshot).

    Prefer live DB state over the enqueue-time snapshot (an intent queued before the create drained
    captured a NULL id). The booking's own ``external_event_id`` wins; if it is unset — a successor
    whose own create/move has not drained yet — walk the ``rescheduled_from_id`` chain to the live
    event the chain already has. Without this, cancelling a not-yet-synced reschedule (the successor
    has no id of its own) would resolve to NULL and orphan the predecessor's still-active event.
    """
    if booking.external_event_id is not None:
        return booking.external_event_id
    ancestor_id = booking.rescheduled_from_id
    seen: set[uuid.UUID] = set()
    while ancestor_id is not None and ancestor_id not in seen:
        seen.add(ancestor_id)
        ancestor = await session.get(Booking, ancestor_id)
        if ancestor is None:  # pragma: no cover - defensive: SET NULL only on a parent-row delete
            break
        if ancestor.external_event_id is not None:
            return ancestor.external_event_id
        ancestor_id = ancestor.rescheduled_from_id
    raw = payload.get("external_event_id")
    return raw if isinstance(raw, str) else None


def _meet_request_from_payload(payload: dict[str, object]) -> MeetEventRequest:
    """Rebuild the Google Meet event request from a Google intent's stored primitives."""
    return MeetEventRequest(
        summary=str(payload["summary"]),
        start=datetime.fromisoformat(str(payload["start"])),
        end=datetime.fromisoformat(str(payload["end"])),
        timezone=str(payload["timezone"]),
        guest_email=str(payload["guest_email"]),
    )


def make_booking_effect_executor(
    *, sender: EmailSender | None, service_factory: ServiceFactory | None
) -> OutboxExecutor:
    """Build the live ``execute`` the drain tick injects: dispatch each intent to its handler.

    ``sender`` / ``service_factory`` come from the app runtime (SMTP config + a Fernet-built Google
    factory). An email intent with no ``sender`` (or a Google intent with no factory) raises, so the
    misconfiguration surfaces as a retrying/dead-lettered row rather than a silently dropped effect.
    """

    async def _execute(session: AsyncSession, outbox: Outbox, now: datetime) -> None:
        effect = OutboxEffect(outbox.effect)
        if effect is OutboxEffect.EMAIL:
            if sender is None:  # pragma: no cover - live misconfiguration guard
                raise RuntimeError("outbox email intent has no configured SMTP sender")
            await run_email_effect(session, outbox, now, sender=sender)
        else:
            if service_factory is None:  # pragma: no cover - live misconfiguration guard
                raise RuntimeError("outbox Google intent has no configured service factory")
            await run_google_effect(session, outbox, now, service_factory=service_factory)

    return _execute


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_DRAIN_BATCH_SIZE",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFER_DELAY_SECONDS",
    "GoogleOperation",
    "OutboxDeferred",
    "OutboxEffect",
    "OutboxExecutor",
    "OutboxReport",
    "backoff_delay",
    "drain_outbox",
    "email_dedupe_key",
    "enqueue_effect",
    "google_dedupe_key",
    "make_booking_effect_executor",
    "run_email_effect",
    "run_google_effect",
]
