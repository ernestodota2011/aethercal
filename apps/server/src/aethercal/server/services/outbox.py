"""The transactional outbox: enqueue an effect intent in-txn, drain it post-commit (F1-05 / R8).

The booking service persists an :class:`~aethercal.server.db.models.outbox.Outbox` *intent* row in
the SAME transaction as the booking mutation (:func:`enqueue_effect`), so an external effect can
never fire for a booking whose transaction later rolled back. A scheduler-driven worker
(:func:`drain_outbox`) executes the intent afterwards. It doubles as the product's **durable
scheduler**: an intent's ``next_retry_at`` is simply its send time, which is why RF-10's 24 h
reminder no longer needs a second (APScheduler) scheduler carrying a second idempotency barrier.

.. rubric:: claim / execute / settle (R8)

The drain used to ``SELECT ... FOR UPDATE SKIP LOCKED`` a whole batch and then run every SMTP and
Google call **inside that still-open transaction** — so one tick held N row locks and a pool
connection for the length of N network round-trips. With several workflow steps per booking across
slow channels (WhatsApp, SMS) that is a pool-exhaustion bug waiting to happen. The drain now runs:

1. **recover** — any row whose lease elapsed (a worker died mid-send) returns to ``pending``,
   WITHOUT consuming an attempt: the worker failed, the effect did not.
2. **claim** — one short transaction takes the due batch with ``FOR UPDATE SKIP LOCKED``, marks it
   ``claimed`` with a ``claimed_by`` + ``lease_expires_at``, and **commits**. Every row lock is
   released here. From now on it is ``status='claimed'``, not a held lock, that keeps a second
   worker off these rows.
3. **execute** — the network I/O runs with **no transaction open at all**. Each handler is phased
   (read → send → record) precisely so that holds.
4. **settle** — a second short transaction records the outcome (``delivered`` / ``failed`` + backoff
   / ``dead``) — but **only if the lease is still ours**. The write is a conditional update gated on
   ``status = 'claimed' AND claimed_by = <this worker>``. A lease is not a lock, so it can be lost:
   if our send overran the TTL, the recovery pass has already handed the row back and another worker
   owns it. Then our result is stale, and we DISCARD it loudly instead of stomping theirs. Writing
   where you no longer have the right is the same silent no-op, just pointed the other way. To keep
   that from happening at all, every provider call is bounded by
   :data:`PROVIDER_TIMEOUT_CEILING` < :data:`DEFAULT_LEASE`.

Delivery stays **at-least-once**: a crash after a provider accepts but before the settle commits
replays the effect. That errs toward a duplicate rather than a lost message, which is the deliberate
choice — and the handlers reduce duplicates anyway (the email checks its ledger, the Google handler
reconciles to the booking's current state).

.. rubric:: The staleness contract

An effect that is queued and then overtaken by a later transition (a cancel, a reschedule) must
usually be DROPPED — nobody should get a "confirmed" email for a booking that has since been
replaced. That is :func:`_is_chain_current`. But it is a **trap** for the terminal effects, because
``_is_chain_current`` is False for a CANCELLED booking *by construction* (its id is never in the
chain's active set) — and a cancellation notice acts on a cancelled booking BY DEFINITION. Wire an
``on_cancel`` workflow step into the same guard the informational steps use and its message is
marked delivered and **never sent**: the guest is never told that their booking was cancelled.

So the guard is not a scattered ``if``. :data:`_STALENESS` is an explicit table, and for a workflow
step it classifies **by trigger** (:func:`trigger_staleness`), exhaustively — ``assert_never`` makes
a newly added trigger a type error rather than a silent default.

A step whose booking is replaced is not "moved" by an upsert (that would match zero rows — a
reschedule opens a NEW booking id): it is retired with :func:`void_pending_steps`, driven by the
transition table in ``services/workflows.py``."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, assert_never, cast

from sqlalchemy import CursorResult, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, ExternalConnection, Outbox, Workflow
from aethercal.server.db.models.outbox import OutboxStatus, due_filter
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.integrations.messaging.guard import (
    ChannelUnavailable,
    PhoneChannelSender,
    SendOutcomeUnknown,
    SendRefused,
    enforce_phone_cap,
)
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.integrations.smtp.sender import EmailSender
from aethercal.server.observability import observe_drain
from aethercal.server.services.calendars import (
    CalendarTarget,
    CalendarTargetMissingError,
    ServiceFactory,
    create_event_for_booking,
    delete_event_for_booking,
    reschedule_event_for_booking,
    resolve_calendar_target,
)
from aethercal.server.services.notifications import (
    compose_booking_notification,
    notification_already_sent,
    record_booking_notification,
)
from aethercal.server.services.templates import (
    TemplateError,
    build_template_context,
    load_template,
    render_template,
)

_logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 30
"""First-retry delay; each subsequent failure doubles it (30s, 60s, 120s, ...)."""

BACKOFF_CAP_SECONDS = 3600
"""Upper bound on a single backoff step (one hour)."""

DEFAULT_MAX_ATTEMPTS = 6
"""Attempts before an intent is parked as ``dead``."""

DEFAULT_DRAIN_BATCH_SIZE = 100
"""Max intents one drain pass claims + processes."""

PROVIDER_TIMEOUT_CEILING = timedelta(minutes=2)
"""The hard ceiling on every provider call: **strictly less than** :data:`DEFAULT_LEASE`, and
ENFORCED — :func:`drain_outbox` runs every effect inside an ``asyncio.timeout`` of exactly this, and
overrunning it is a retryable failure.

The enforcement IS the point. A ceiling that is only a constant, a comment and a test of its own
arithmetic is a **declared invariant that nothing applies**, and the code goes on doing precisely
what the invariant forbids. Here that is not academic: an unbounded send outlives its lease, the
recovery pass hands the row to another worker, and the guest is messaged twice.

The lease is not self-renewing. A worker whose send outlives the TTL loses its claim, and its result
is discarded at settle (see :func:`_settle`) — after the provider has already done the work, so the
guest can be messaged twice. The only two ways out are lease RENEWAL or provider timeouts bounded
below the TTL, and this codebase takes the second: it is one number to get right instead of a
heartbeat to keep alive, and every client here already takes a timeout.

2 min vs a 5 min TTL leaves a 3 min margin for the DB round-trips either side of the call. The
``lost`` counter on :class:`OutboxReport` is what proves the assumption in production: if it is ever
non-zero, this ceiling (or the TTL) is wrong.
"""

DEFAULT_LEASE = timedelta(minutes=5)
"""The lease TTL: how long a claim is honoured before another worker may take the row over.

Bounded from BELOW by the slowest single provider round-trip — a lease shorter than a send lets a
second worker take a row a healthy worker is still working on, and the message goes out twice. That
bound is not left to hope: :data:`PROVIDER_TIMEOUT_CEILING` (2 min) is the contract every provider
call must honour, and it is strictly under this TTL.

Bounded from ABOVE by how long a dead worker's rows may sit idle. Which is why the recovery pass
runs
at the top of EVERY drain (see :func:`drain_outbox`), i.e. once per
``DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS`` (60 s) — far shorter than the lease.
Get that relationship backwards and a crashed worker turns into hours of silence: the rows stay
``claimed``, nothing is due, and no alarm fires. So the invariant is ``recovery interval << lease
TTL``, and the worst-case delay a crash can add is ``lease TTL + one drain interval`` ≈ 6 minutes.
"""

# The status vocabulary is DECLARED ONCE, on the row itself (``db/models/outbox.OutboxStatus``), and
# merely re-bound here for terse internal use. It used to be seven private literals living only in
# this module, which left every other reader of these rows — the metrics endpoint, the readiness
# probe, the replay CLI — free to re-type them and drift.
_PENDING = OutboxStatus.PENDING.value
_CLAIMED = OutboxStatus.CLAIMED.value
_FAILED = OutboxStatus.FAILED.value
_DELIVERED = OutboxStatus.DELIVERED.value
_DEAD = OutboxStatus.DEAD.value
_SKIPPED = OutboxStatus.SKIPPED.value
"""Terminal, and NOT a failure: the step could never run, so retrying it is pointless.

A channel with no credentials is a DISABLED FEATURE, not an error. Treat it as a failure and every
reminder on that channel burns six attempts of exponential backoff and lands in the dead-letter —
noise in the backlog, and the message still does not arrive. The step is retired with its reason
instead, loudly in the log and visibly in the row. """
_UNKNOWN = OutboxStatus.UNKNOWN.value
"""Handed to the provider; the answer was lost. Terminal, and NOT retried — see OutboxStatus."""
_VOIDED = OutboxStatus.VOIDED.value
"""Retired before it ever ran: the booking's life changed under it (see
:func:`void_pending_steps`)."""

# A claimed row is NOT terminal — it is mid-flight. Anything waiting on a sibling intent (an email
# waiting for its Meet link; a calendar delete waiting for the create that will produce the event id
# it must remove) has to treat it as still coming, or it acts on a half-written world.
_NON_TERMINAL = (_PENDING, _CLAIMED, _FAILED)


class OutboxEffect(StrEnum):
    """The effect kinds the outbox carries (the ``Outbox.effect`` discriminator)."""

    EMAIL = "email"
    GOOGLE = "google"
    NOTIFY = "notify"
    """One workflow step, on one channel (RF-24). Handler: the workflow-engine cut."""


class GoogleOperation(StrEnum):
    """The Google-Calendar operations an outbox intent can request."""

    UPSERT = "upsert"
    RESCHEDULE = "reschedule"
    DELETE = "delete"


class Staleness(StrEnum):
    """Whether an effect is dropped when its booking is no longer the chain's live member."""

    SUBJECT = "subject"
    """Informational. A later transition overtook it → do not send it."""
    EXEMPT = "exempt"
    """Terminal. It acts on a booking that is *supposed* to be gone → always run it."""


# The staleness contract. EXHAUSTIVE by construction: staleness_policy() raises for an effect that
# has not declared itself, so a new handler must state its intent instead of inheriting a default.
#
# EMAIL SUBJECT, except a CANCELLATION (terminal - it IS the transition). GOOGLE SUBJECT, except a
# DELETE (terminal - it removes a cancelled booking's event). NOTIFY classified BY TRIGGER (below),
# never by eyeballing "informational vs terminal".
_TERMINAL_EMAIL_KINDS = frozenset({NotificationKind.CANCELLATION})
_TERMINAL_GOOGLE_OPERATIONS = frozenset({GoogleOperation.DELETE})


def trigger_staleness(trigger: WorkflowTrigger) -> Staleness:
    """Whether a workflow step fired by ``trigger`` survives its booking being overtaken.

    Classified by TRIGGER, never by a hand-waved "informational vs terminal" judgement. The reason
    is
    exact: an ``on_cancel`` step acts on a booking that is CANCELLED, and :func:`_is_chain_current`
    is False for a cancelled booking *by construction*. Gate an ``on_cancel`` notice on staleness
    and the cancellation message is marked delivered and **never sent** - the guest is never told
    that their booking was cancelled.

    ``assert_never`` makes the mapping exhaustive at TYPE-CHECK time: adding a trigger without
    classifying it here fails pyright, instead of silently inheriting a default that drops messages.

    * ``on_booking`` / ``before_start`` -> **SUBJECT**. They speak about an appointment still
      supposed to happen; if the chain moved on, the message is wrong and must not go out.
    * ``after_end`` / ``on_cancel`` / ``on_no_show`` -> **EXEMPT**. The appointment is over or gone.
      "The chain moved on" is not a reason to suppress them - it is the very thing they report.
    """
    match trigger:
        case WorkflowTrigger.ON_BOOKING | WorkflowTrigger.BEFORE_START:
            return Staleness.SUBJECT
        case WorkflowTrigger.AFTER_END | WorkflowTrigger.ON_CANCEL | WorkflowTrigger.ON_NO_SHOW:
            return Staleness.EXEMPT
        case _ as unreachable:
            assert_never(unreachable)


_STALENESS: Mapping[OutboxEffect, Callable[[Mapping[str, Any]], Staleness]] = {
    OutboxEffect.EMAIL: lambda payload: (
        Staleness.EXEMPT
        if NotificationKind(payload["kind"]) in _TERMINAL_EMAIL_KINDS
        else Staleness.SUBJECT
    ),
    OutboxEffect.GOOGLE: lambda payload: (
        Staleness.EXEMPT
        if GoogleOperation(payload["operation"]) in _TERMINAL_GOOGLE_OPERATIONS
        else Staleness.SUBJECT
    ),
    OutboxEffect.NOTIFY: lambda payload: trigger_staleness(WorkflowTrigger(payload["trigger"])),
}


def staleness_policy(effect: OutboxEffect, payload: Mapping[str, Any]) -> Staleness:
    """Whether ``effect`` is dropped when its booking is no longer the chain's live member.

    Raises :class:`KeyError` for an effect that has not declared itself in :data:`_STALENESS`. That
    is the whole point: a terminal effect silently inheriting SUBJECT is a message that gets marked
    delivered and never sent."""
    try:
        rule = _STALENESS[effect]
    except KeyError as exc:  # pragma: no cover - guarded by test_every_effect_declares_a_staleness
        raise KeyError(
            f"outbox effect {effect.value!r} has not declared a staleness policy; "
            "add it to _STALENESS (SUBJECT for informational, EXEMPT for terminal)"
        ) from exc
    return rule(payload)


DEFER_DELAY_SECONDS = 30
"""How soon a deferred (dependency-waiting) intent is retried."""

PAUSED_RULE_RECHECK = timedelta(minutes=15)
"""How often a step PAUSED by a switched-off rule asks again whether that rule has come back.

Not :data:`DEFER_DELAY_SECONDS`: a sibling intent lands in seconds, whereas a tenant re-enables a
rule in minutes, or never. Polling a disabled rule's whole backlog every 30 s would be a hot loop
waiting on a condition that only a human can change."""

TERMINAL_MESSAGE_GRACE = timedelta(days=7)
"""How long a TERMINAL message (a follow-up, a cancellation notice) is still worth sending after its
moment. Unlike a reminder it remains TRUE afterwards — but a row cannot wait for ever."""


class OutboxDeferred(Exception):
    """Raised by an effect handler to POSTPONE its intent without consuming an attempt.

    The effect is not failing — it is WAITING. Two kinds of wait, and they are not the same:

    * on a SIBLING INTENT (an email waiting for its booking's Google Meet link; a calendar delete
      waiting for the create that will produce the event id it must remove) — seconds;
    * on a HUMAN: the step's workflow rule is switched off, and the tenant may switch it back on —
      minutes, or never (:data:`PAUSED_RULE_RECHECK`).

    Hence ``retry_after``. The drain returns the row to ``pending`` at that distance and leaves
    ``attempts`` alone, so a legitimate wait never counts toward the dead-letter budget.

    ==This is the ONLY non-terminal way for a handler to decline to send.== The distinction from
    :class:`OutboxSkipped` is not stylistic: a TEMPORARY condition must never produce a TERMINAL
    outcome, or the message is destroyed by a state the tenant can simply undo."""

    def __init__(self, message: str, *, retry_after: timedelta | None = None) -> None:
        super().__init__(message)
        self.retry_after = (
            timedelta(seconds=DEFER_DELAY_SECONDS) if retry_after is None else retry_after
        )


class OutboxUnknownOutcome(Exception):
    """The provider was given this message and we never learned whether it went out.

    Raised either by the sender (a lost answer, mid-flight) or by the READ phase of a LATER drain,
    which finds the in-flight marker still set on a row whose ledger entry never landed — i.e. the
    worker died in the window between "the provider accepted" and "the ledger committed".

    The drain parks it as :attr:`OutboxStatus.UNKNOWN`: no retry, an error log, a metric. ==It is
    never re-sent blind.=="""


class OutboxSkipped(Exception):
    """Raised by a handler when its effect can NEVER run, so retrying it is meaningless.

    The distinction from a failure is the whole point. A WhatsApp step on an instance with no
    WhatsApp credentials is not "broken" — it is switched off. Retried like a failure it would burn
    the entire backoff budget and dead-letter, filling the queue with noise while still delivering
    nothing. Terminal, recorded with its reason, and it consumes no attempt.

    ==Terminal means IRREVERSIBLE, so it may only carry a condition that cannot be undone.== A rule
    that is merely switched OFF is not one of those — the tenant can switch it back on, and a step
    retired while it was off could never be delivered afterwards. That is a wait, and it belongs in
    :class:`OutboxDeferred`."""


@dataclass(frozen=True, slots=True)
class OutboxWork:
    """A claimed intent, DETACHED from any session.

    The whole point of R8 is that a handler runs its network I/O with no session and no transaction.
    Handing it a live ORM row would make that impossible to enforce — any attribute access could
    emit a lazy SELECT and silently reopen a transaction — so the claim snapshots what the handler
    needs into a frozen value."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    booking_id: uuid.UUID
    effect: OutboxEffect
    dedupe_key: str
    payload: dict[str, Any]
    attempts: int
    claimed_by: str
    """The worker holding this row's lease. The settle REFUSES to write without it.

    Without this, the lease is only half a mechanism. A worker claims a row, its network I/O
    overruns the TTL, the recovery pass hands the row back to ``pending``, a SECOND worker claims it
    — and then the first worker settles its own stale result on top, marking ``delivered`` an intent
    the second worker is still executing. So the settle is a CONDITIONAL update, gated on this
    value. """


# The injected effect runner. It receives NO session: it opens its own short-lived ones around the
# I/O. Injected so the drain is testable offline with a fake.
OutboxExecutor = Callable[[OutboxWork, datetime], Awaitable[None]]

Sessionmaker = async_sessionmaker[AsyncSession]

Clock = Callable[[], datetime]
"""Reads the wall clock. A lease is a wall-clock deadline, so the drain needs REAL elapsed time."""


def _elapsed_clock(now: datetime) -> Clock:
    """A clock anchored at ``now`` and advanced by the real time that has elapsed since.

    NOT ``lambda: now``. A frozen clock would stamp every item's lease with the same deadline no
    matter how long the batch actually took — which is precisely the bug that per-item claiming
    exists to remove. ``monotonic`` because this measures a DURATION, and the wall clock can step
    backwards (NTP, DST) while a duration cannot.
    """
    started = time.monotonic()
    return lambda: now + timedelta(seconds=time.monotonic() - started)


@dataclass
class OutboxReport:
    """The outcome of one :func:`drain_outbox` pass: the intent ids by terminal/retry bucket."""

    delivered: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    dead: list[uuid.UUID] = field(default_factory=list)
    deferred: list[uuid.UUID] = field(default_factory=list)
    recovered: list[uuid.UUID] = field(default_factory=list)
    """Rows a dead worker had claimed, returned to ``pending`` by this pass's lease recovery."""
    unclaimed: list[uuid.UUID] = field(default_factory=list)
    """Planned, but claimed by somebody else (or voided) before we reached them.

    Not ours, and not errors.
    """
    unknown: list[uuid.UUID] = field(default_factory=list)
    """Handed to the provider; the answer was lost. ==THE bucket to alarm on.==

    Each one is a message that may or may not have reached a real person, and which this system will
    NOT resend on its own. It is neither noise nor routine: a non-empty ``unknown`` is a human task,
    and the entire point of the state is that it cannot be ignored quietly.
    """
    skipped: list[uuid.UUID] = field(default_factory=list)
    """Steps that could never run (an unconfigured channel, a kind with no template).

    NOT failures. """
    voided_midflight: list[uuid.UUID] = field(default_factory=list)
    """Retired by a booking transition WHILE we were sending them. Routine, expected, and NOT lost.

    Kept out of :attr:`lost` deliberately: that counter exists to alert on a broken timing
    assumption, and an alarm that also fires on every ordinary cancellation is one nobody reads.
    """
    lost: list[uuid.UUID] = field(default_factory=list)
    """Rows whose LEASE EXPIRED mid-send, so this worker's result was DISCARDED at settle time.

    Never silent: each one is an error-level log line. A non-empty ``lost`` means a provider call
    outran its lease despite :data:`PROVIDER_TIMEOUT_CEILING`, and the effect may well have been
    executed twice. It is THE metric to alert on — which is exactly why a step retired by a routine
    cancellation goes to :attr:`voided_midflight` instead, and never in here.
    """

    @property
    def attempted(self) -> int:
        """How many intents this pass actually tried to run to completion (excludes deferrals)."""
        return len(self.delivered) + len(self.failed) + len(self.dead)


def backoff_delay(
    attempts: int,
    *,
    base: int = BACKOFF_BASE_SECONDS,
    cap: int = BACKOFF_CAP_SECONDS,
) -> timedelta:
    """Exponential backoff after the ``attempts``-th failure (1-based): ``base * 2**(attempts-1)``.

    Capped at ``cap`` seconds so a long-broken effect never schedules an absurd retry."""
    exponent = max(attempts - 1, 0)
    return timedelta(seconds=min(base * (2**exponent), cap))


def email_dedupe_key(kind: NotificationKind) -> str:
    """The idempotency key for an email intent (one per booking + notification kind)."""
    return f"{OutboxEffect.EMAIL.value}:{kind.value}"


def google_dedupe_key(operation: GoogleOperation) -> str:
    """The idempotency key for a Google-sync intent (one per booking + operation)."""
    return f"{OutboxEffect.GOOGLE.value}:{operation.value}"


def workflow_step_dedupe_key(workflow_id: uuid.UUID, step_id: uuid.UUID, channel: Channel) -> str:
    """The idempotency key for one workflow step on one channel (RF-24's exactly-once guarantee).

    The existing ``UniqueConstraint(tenant_id, booking_id, dedupe_key)`` turns a double-enqueue into
    a silent no-op. Nothing invents a second idempotency mechanism."""
    return f"wf:{workflow_id}:{step_id}:{channel.value}"


def new_worker_id() -> str:
    """A stable-enough identity for a drain worker: who holds a lease (host:pid:nonce)."""
    return f"{socket.gethostname()[:24]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def as_utc(moment: datetime) -> datetime:
    """SQLite drops tzinfo on the round-trip; normalise before doing arithmetic on a stored instant.

    Lives HERE, in the layer everything else sits on, because both the send-time arithmetic
    (``services/workflows.py``) and the deadline arithmetic (:func:`message_deadline`) need it — and
    two copies of "what does a naive timestamp from the database mean" is how two answers appear."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def enqueue_effect(  # noqa: PLR0913 - the intent's identity + payload are the keyword contract
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    effect: OutboxEffect,
    dedupe_key: str,
    payload: dict[str, object],
    next_retry_at: datetime | None = None,
) -> Outbox | None:
    """Persist a ``pending`` effect intent for a booking, INSIDE the caller's transaction (F1-05).

    Idempotent on ``(tenant_id, booking_id, dedupe_key)``: the insert runs in a ``SAVEPOINT`` and a
    unique-constraint conflict (the same transition already enqueued this exact intent) returns
    ``None`` without poisoning the transaction. Flushes; the caller owns the commit, so the intent
    commits atomically with the booking, or rolls back with it (and never fires for a booking that
    never persisted). Returns the created row, or ``None`` when it was already queued.

    ``next_retry_at`` is the intent's earliest send time. ``None`` means "as soon as the next drain
    runs"; a future instant is how the outbox doubles as the durable SCHEDULER — a 24 h reminder is
    just an intent that is not due until ``start - 24h``.

    **What happens on a conflict is decided by** :func:`conflict_policy`, per effect — it is not
    always "do nothing". For a time-bearing effect (a workflow step: ``before_start`` + an offset) a
    silent no-op is a BUG: re-materialising the step for a booking whose start has MOVED would leave
    the queued row on its old ``next_retry_at``, and the reminder would fire 24 h before the OLD
    start. Those effects RE-TIME the existing row instead — but only while it is still ``pending``,
    so
    a message that already went out is never re-sent, and a mid-flight (``claimed``) row is not
    yanked out from under its worker."""
    row = Outbox(
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=effect.value,
        dedupe_key=dedupe_key,
        payload=payload,
        status=_PENDING,
        attempts=0,
        next_retry_at=next_retry_at,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    return row


async def void_pending_steps(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    triggers: Collection[WorkflowTrigger],
) -> list[uuid.UUID]:
    """Retire this booking's LIVE workflow steps for ``triggers``. Returns the ids voided.

    This — not an upsert — is how a booking's queued steps are corrected when its life changes.
    ``reschedule_booking`` does NOT mutate a booking: it opens a NEW row (the successor inherits the
    ``ical_uid``; the predecessor is marked cancelled). Outbox uniqueness keys on ``booking_id``, so
    a successor's steps can never collide with the predecessor's — an ``ON CONFLICT ... DO UPDATE``
    that tried to "move" a step would match ZERO rows, raise nothing, and pass every test. That is
    the silent no-op this whole design exists to kill, so the predecessor's steps are voided
    explicitly instead.

    Voiding covers the staleness-EXEMPT steps too, which is the entire point: an ``after_end``
    follow-up is exempt (the appointment is over — "the chain moved on" is not a reason to suppress
    it), so a predecessor's copy left alive WILL be delivered, at the hour of a meeting that never
    happened.

    .. rubric:: "Live" means ``pending`` AND ``failed`` AND ``claimed``

    Retiring only ``pending`` rows is a bug with a real victim. A step whose provider was down sits
    in ``failed`` with a ``next_retry_at`` in the future, and it is **completely alive**: cancel the
    booking and that step retries an hour later, messaging a guest about an appointment that no
    longer exists. Reschedule, and the predecessor's failed steps fire at the OLD time. So the void
    covers every non-terminal state:

    * ``pending`` / ``failed`` → voided outright. They had not started; now they never will.
    * ``claimed`` → voided as well. This is the case whose policy has to be WRITTEN DOWN, because a
      claimed step may be in the provider's hands right now, and there is no such thing as
      un-sending. The policy: ==**we do not try to recall it; we guarantee it is never RETRIED; and
      we refuse to let its worker write the outcome.**== Marking it ``voided`` does all three — the
      row becomes terminal, and the worker's settle (gated on ``status='claimed' AND
      claimed_by=<me>``, see :func:`_settle`) no longer matches, so its result is discarded and
      logged. The message may still reach the guest: that is the honest, unavoidable residual of an
      at-least-once queue, and it is RECORDED rather than pretended away.

    ``FOR UPDATE`` makes this atomic against :func:`claim_one`. A concurrent drain about to claim
    one of these rows blocks on the lock until the booking transition commits, and its conditional
    UPDATE
    then finds ``status='voided'`` and matches nothing. A step cannot slip from ``pending`` into a
    worker's hands *while* the cancellation that retires it is committing."""
    wanted = {trigger.value for trigger in triggers}
    if not wanted:
        return []
    rows = (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.tenant_id == tenant_id,
                Outbox.booking_id == booking_id,
                Outbox.effect == OutboxEffect.NOTIFY.value,
                Outbox.status.in_(_NON_TERMINAL),
            )
            .with_for_update()
        )
    ).all()
    voided = _retire([row for row in rows if row.payload.get("trigger") in wanted])
    await session.flush()
    return voided


def _retire(rows: Collection[Outbox]) -> list[uuid.UUID]:
    """Mark every row ``voided`` — the ONE place a live step is retired. Returns the ids.

    Extracted rather than repeated: :func:`void_pending_steps` (the BOOKING's life changed) and
    :func:`reconcile_workflow_steps` (the RULE changed) both need it, and a second copy of "which
    fields have to be cleared" is how a claimed row keeps its lease and gets silently retried later.
    """
    voided: list[uuid.UUID] = []
    for row in rows:
        if row.status == _CLAIMED:
            # In flight. We cannot un-send it — we can only guarantee it is never retried, and that
            # its worker's result is discarded rather than written. Say so, out loud.
            _logger.warning(
                "outbox intent %s (booking %s) was VOIDED while a worker held it: the send may "
                "already have reached the provider and cannot be recalled. It will NOT be retried, "
                "and the worker's result will be discarded",
                row.id,
                row.booking_id,
            )
        row.status = _VOIDED
        row.next_retry_at = None
        row.claimed_by = None
        row.lease_expires_at = None
        voided.append(row.id)
    return voided


def workflow_key_prefix(workflow_id: uuid.UUID) -> str:
    """The dedupe-key prefix every step of ``workflow_id`` shares (see
    :func:`workflow_step_dedupe_key`).

    A UUID carries no LIKE wildcard, so the prefix match is exact — and it finds a rule's queued
    rows without querying INSIDE the payload JSON, which SQLite and PostgreSQL spell
    differently."""
    return f"wf:{workflow_id}:"


@dataclass(frozen=True, slots=True)
class StepSchedule:
    """What ONE workflow step's queued row ought to look like, per the rule as it stands NOW.

    Produced by ``services/workflow_rules.py`` (which owns what a rule MEANS) and executed by
    :func:`reconcile_workflow_steps` (which owns what an outbox row may DO). ``send_at`` is ``None``
    for the event-shaped triggers — they fire on the next drain, so there is nothing to re-time."""

    dedupe_key: str
    payload: dict[str, Any]
    send_at: datetime | None


@dataclass
class ReconcileReport:
    """What a rule change did to one booking's queue."""

    materialised: list[uuid.UUID] = field(default_factory=list)
    retimed: list[uuid.UUID] = field(default_factory=list)
    voided: list[uuid.UUID] = field(default_factory=list)
    left: list[uuid.UUID] = field(default_factory=list)
    """Mid-flight (``claimed``), or event-shaped: not a rule edit's business to move."""


async def reconcile_workflow_steps(  # noqa: PLR0913 - the row's identity IS the keyword contract
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    workflow_id: uuid.UUID,
    wanted: Collection[StepSchedule],
    now: datetime,
    timing_changed: bool,
) -> ReconcileReport:
    """Bring ONE booking's queued steps for ONE workflow in line with ``wanted``.

    This is what makes an edit to a rule TRUE. Without it, changing "remind 24 h before" to "2 h
    before" rewrites a row in ``workflows`` and changes nothing a guest can perceive: every booking
    already on the books keeps its step queued at ``start - 24h``, because the send time lives in
    the outbox row's ``next_retry_at``, not in the rule. The rule would say one thing and the queue
    would do another, with no error anywhere — the silent no-op, one layer up.

    It is emphatically **not an upsert**. ``ON CONFLICT ... DO UPDATE`` expresses none of the
    outcomes below, and the UNIQUE constraint on ``(tenant_id, booking_id, dedupe_key)`` is also why
    a naive "just re-materialise it" fails: a key that already exists — even on a ``voided`` or
    ``delivered`` row — makes :func:`enqueue_effect` return ``None``, silently. So every row is
    decided explicitly:

    * **not in** ``wanted`` → **voided**. The step was removed from the rule; its message must not
      still arrive next Tuesday.
    * **in** ``wanted``, live, send time **in the future** → **re-timed in place**: the SAME row
      (so the message stays exactly-once), carrying the rule's new instant and payload.
    * **in** ``wanted``, but its send time is **in the past** → it depends on ``timing_changed``,
      and the difference is a message's life:

      - ``timing_changed`` (the EDIT dragged it backwards into the past — say -60 became -1440 for a
        booking three hours out) → **voided**. That message's moment never existed; a
        ``next_retry_at`` in the past drains IMMEDIATELY, so re-timing it there would fire the
        "reminder" at once, after the fact.
      - **not** ``timing_changed`` (the rule's clock did not move; the row is simply OVERDUE — it
        was PAUSED while the tenant had the rule switched off) → **made due now**. Voiding it here
        would destroy, on re-activation, exactly the message the re-activation exists to deliver.

    * **in** ``wanted``, with **no live row** → **materialised**, but only when its send time is a
      real future instant. An event-shaped step (``send_at is None``) is never back-filled: it
      reports something that has already happened, and queueing it now would tell somebody who
      booked last week that their booking is confirmed.

    A ``claimed`` row is left alone: a worker is sending it right now, and re-timing a message that
    is already in the provider's hands is meaningless.

    Nothing here needs to ask whether the message is still *worth* sending — an overdue step made
    due
    now is re-checked against :func:`message_deadline` at the send, so a rule switched back on a
    week
    late still cannot fire a reminder for a meeting that has already happened.
    """
    by_key = {schedule.dedupe_key: schedule for schedule in wanted}
    rows = (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.tenant_id == tenant_id,
                Outbox.booking_id == booking_id,
                Outbox.effect == OutboxEffect.NOTIFY.value,
                Outbox.status.in_(_NON_TERMINAL),
                Outbox.dedupe_key.startswith(workflow_key_prefix(workflow_id)),
            )
            .with_for_update()
        )
    ).all()

    report = ReconcileReport()
    to_void: list[Outbox] = []
    seen: set[str] = set()
    for row in rows:
        seen.add(row.dedupe_key)
        schedule = by_key.get(row.dedupe_key)
        if schedule is None:
            to_void.append(row)  # this step is gone from the rule
        elif row.status == _CLAIMED or schedule.send_at is None:
            # Mid-flight, or event-shaped (due on the next drain anyway). Nothing to move.
            report.left.append(row.id)
        elif schedule.send_at <= now and timing_changed:
            _logger.info(
                "outbox intent %s (booking %s): the rule moved its send time to %s, which is "
                "already past — retiring it rather than firing it late",
                row.id,
                booking_id,
                schedule.send_at.isoformat(),
            )
            to_void.append(row)
        elif schedule.send_at <= now:
            # OVERDUE, not mistimed: the rule's clock never moved, so this is the step that was
            # PAUSED while the rule was switched off. Make it due NOW. Voiding it — the branch above
            # — would destroy, at the very moment of re-activation, the message that re-activation
            # exists to deliver. Whether it is still worth sending is the SEND's question
            # (``message_deadline``), not this one's.
            row.next_retry_at = now
            report.retimed.append(row.id)
        else:
            row.payload = dict(schedule.payload)
            row.next_retry_at = schedule.send_at
            report.retimed.append(row.id)
    report.voided.extend(_retire(to_void))

    for schedule in by_key.values():
        if schedule.dedupe_key in seen or schedule.send_at is None or schedule.send_at <= now:
            continue
        created = await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            effect=OutboxEffect.NOTIFY,
            dedupe_key=schedule.dedupe_key,
            payload=dict(schedule.payload),
            next_retry_at=schedule.send_at,
        )
        if created is not None:
            report.materialised.append(created.id)
        # ``None`` = a TERMINAL row already owns this key (delivered / skipped / voided). That
        # message has had its moment, and the UNIQUE constraint is what stops a rule edit re-sending
        # it. Deliberately not an error: reconciling a rule is not a request to re-send anything.
    await session.flush()
    return report


# --------------------------------------------------------------------------------------
# The drain: recover → claim → execute (no txn) → settle.
# --------------------------------------------------------------------------------------


async def recover_expired_leases(
    session: AsyncSession, *, now: datetime, limit: int = DEFAULT_DRAIN_BATCH_SIZE
) -> list[uuid.UUID]:
    """Return rows whose lease elapsed to ``pending`` so another worker can pick them up.

    A claimed row whose lease has passed means the worker holding it died (or was killed) mid-send.
    Its ``attempts`` is deliberately NOT bumped: the WORKER failed, the effect never got its turn,
    so charging it an attempt would push a perfectly healthy intent toward the dead-letter for
    somebody
    else's crash. The row is simply due again.
    """
    rows = (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.status == _CLAIMED,
                Outbox.lease_expires_at.is_not(None),
                Outbox.lease_expires_at <= now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        _logger.warning(
            "outbox intent %s (%s): the lease held by %s expired; returning it to pending",
            row.id,
            row.effect,
            row.claimed_by,
        )
        row.status = _PENDING
        row.claimed_by = None
        row.lease_expires_at = None
    await session.flush()
    return [row.id for row in rows]


async def select_due(
    session: AsyncSession, *, now: datetime, limit: int = DEFAULT_DRAIN_BATCH_SIZE
) -> list[uuid.UUID]:
    """The ids of the intents that are due, in the order they must run. It claims NOTHING.

    "Due" = ``pending``, or ``failed`` with ``next_retry_at`` unset or ``<= now`` — the predicate is
    :func:`due_filter`, declared once on the model, because observability COUNTS the same rows and
    two hand-written copies of the rule would drift invisibly (the drain would go on sending exactly
    the right intents while the backlog alarm quietly measured something else).

    Deliberately no lock and no claim: this is a *plan*, and a plan made now can already be stale by
    the time the tenth item of it actually runs. :func:`claim_one` is what arbitrates, item by item,
    at the moment each one begins."""
    return list(
        (
            await session.scalars(
                select(Outbox.id)
                .where(due_filter(now))
                .order_by(
                    Outbox.created_at,
                    # Within one instant (intents enqueued in the same transaction share a stamp),
                    # run the Google sync BEFORE the email — so the confirmation/reschedule notice
                    # carries the Meet link the sync just wrote onto the booking: a deterministic
                    # causal order, not the non-deterministic tie-break created_at alone would
                    # leave.
                    case((Outbox.effect == OutboxEffect.GOOGLE.value, 0), else_=1),
                )
                .limit(limit)
            )
        ).all()
    )


async def claim_one(
    session: AsyncSession,
    *,
    intent_id: uuid.UUID,
    now: datetime,
    worker_id: str,
    lease: timedelta = DEFAULT_LEASE,
) -> OutboxWork | None:
    """Claim ONE intent, at the instant it is about to run. ``None`` = somebody else got there.

    ==Claimed per ITEM, on purpose. This is the fix for a real duplicate-send bug.==

    The obvious design claims the whole batch up front and then works through it serially. But a
    lease is a WALL-CLOCK deadline: stamp all fifty rows with ``now + 5 min``, then spend six
    minutes on the first forty, and rows 41-50 have leases that expired **while they were still
    waiting their turn**. The recovery pass hands them to a second worker, the second worker sends
    them, and this worker then sends them again. A duplicate email to a real guest — and the lease
    never protected against it, because the lease was built for a worker that DIED, not for one that
    is merely slow
    with a long batch. Slow-with-a-long-batch is the NORMAL case.

    Claiming each item as it begins makes the lease cover exactly what it was meant to cover: the
    execution of THIS item. Together with ``PROVIDER_TIMEOUT_CEILING`` < ``DEFAULT_LEASE``, an
    item's own send can no longer outlive its own claim.

    The claim is a conditional UPDATE, so of N workers racing for one row exactly one wins; the
    losers get ``None`` and move on. Due-ness is re-checked HERE, never trusted from the plan."""
    # A CursorResult, because only that carries `rowcount` — and `rowcount` IS the arbitration here:
    # it is how we learn whether WE won the claim or somebody else did.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Outbox)
            .where(
                Outbox.id == intent_id,
                Outbox.status.in_((_PENDING, _FAILED)),
                or_(Outbox.next_retry_at.is_(None), Outbox.next_retry_at <= now),
            )
            .values(status=_CLAIMED, claimed_by=worker_id, lease_expires_at=now + lease)
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount != 1:
        # Another worker claimed it, or a booking transition VOIDED it, between the plan and now.
        return None

    row = await session.get(Outbox, intent_id)
    if row is None:  # pragma: no cover - defensive: we just updated it
        return None
    await session.refresh(row)
    return OutboxWork(
        id=row.id,
        tenant_id=row.tenant_id,
        booking_id=row.booking_id,
        effect=OutboxEffect(row.effect),
        dedupe_key=row.dedupe_key,
        payload=dict(row.payload),
        attempts=row.attempts,
        claimed_by=worker_id,
    )


class _Outcome(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


async def drain_outbox(  # noqa: PLR0913 - one keyword per knob of a single well-defined pass
    sessionmaker: Sessionmaker,
    *,
    now: datetime,
    execute: OutboxExecutor,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    batch_size: int = DEFAULT_DRAIN_BATCH_SIZE,
    worker_id: str | None = None,
    lease: timedelta = DEFAULT_LEASE,
    clock: Clock | None = None,
    provider_timeout: timedelta = PROVIDER_TIMEOUT_CEILING,
) -> OutboxReport:
    """One drain pass: recover → plan → (claim → execute → settle) per item. Returns the report.

    Takes a ``sessionmaker``, not a session, because the whole design turns on owning the
    transaction
    BOUNDARIES: the claim commits before any network call and the settle opens a fresh transaction
    after it. A caller that handed us one long-lived session would reintroduce the very bug this
    replaces.

    The batch is PLANNED up front but CLAIMED item by item, each at the moment it begins — see
    :func:`claim_one`. Claiming the whole batch at once stamps every row with the same lease
    deadline, and the rows at the back of a slow batch have their leases expire while still waiting
    their turn. That is a duplicate send, and not a theoretical one.

    ``clock`` reads the wall clock. It defaults to ``now`` advanced by the REAL time elapsed since
    the pass began, because a lease is a wall-clock deadline — a frozen clock would put the deadline
    right back where the bug was.

    ``provider_timeout`` is ENFORCED, not merely declared: every effect runs inside
    ``asyncio.timeout``, and overrunning it is a retryable failure. That is what makes
    ``PROVIDER_TIMEOUT_CEILING < DEFAULT_LEASE`` a fact about the running system instead of an
    assertion in a docstring."""
    tick = clock or _elapsed_clock(now)
    worker = worker_id or new_worker_id()
    report = OutboxReport()

    async with sessionmaker() as session, session.begin():
        report.recovered.extend(await recover_expired_leases(session, now=tick(), limit=batch_size))

    async with sessionmaker() as session:
        planned = await select_due(session, now=tick(), limit=batch_size)

    for intent_id in planned:
        # The lease starts HERE, for THIS item — not back when the batch was planned.
        item_now = tick()
        async with sessionmaker() as session, session.begin():
            work = await claim_one(
                session, intent_id=intent_id, now=item_now, worker_id=worker, lease=lease
            )
        if work is None:
            # Somebody else claimed it while we worked through the earlier items, or a booking
            # transition voided it. Either way it is not ours: leave it alone.
            report.unclaimed.append(intent_id)
            continue

        # THE NETWORK I/O. No session, no transaction and no row lock is open across this line —
        # that is the entire point of R8. Each handler opens its own short transactions around it.
        #
        # And it is BOUNDED. `PROVIDER_TIMEOUT_CEILING` is not a constant, a comment and a hopeful
        # piece of arithmetic: the invariant "a send cannot outlive its own lease" is only TRUE if
        # something actually stops the send. This is that something.
        defer_for: timedelta | None = None
        try:
            async with asyncio.timeout(provider_timeout.total_seconds()):
                await execute(work, item_now)
        except TimeoutError:
            # Retryable — and neither of the two things it could be mistaken for. NOT a success (we
            # have no idea whether the provider acted). NOT a death (this worker is alive and still
            # holds its lease, precisely because the timeout fired strictly INSIDE it). It goes back
            # on the queue with backoff, exactly like any other transient provider failure.
            _logger.error(
                "outbox intent %s (%s) for booking %s: the provider call exceeded "
                "PROVIDER_TIMEOUT_CEILING (%ss) and was ABORTED. It retries with backoff. This is "
                "the guard that stops a send outliving its own lease — without it the row would be "
                "recovered under us and delivered twice",
                work.id,
                work.effect,
                work.booking_id,
                provider_timeout.total_seconds(),
            )
            outcome = _Outcome.FAILED
        except OutboxDeferred as deferred:
            _logger.debug("outbox intent %s deferred: %s", work.id, deferred)
            outcome = _Outcome.DEFERRED
            defer_for = deferred.retry_after
        except OutboxUnknownOutcome as unknown:
            # NOT a failure (a retry could duplicate) and NOT a skip (the guest may never have got
            # it). It is the third thing, and it is the one a human has to resolve.
            _logger.error(
                "outbox intent %s (%s) for booking %s: OUTCOME UNKNOWN - %s",
                work.id,
                work.effect,
                work.booking_id,
                unknown,
            )
            outcome = _Outcome.UNKNOWN
        except OutboxSkipped as skipped:
            # NOT a failure: this effect can never run, so retrying it would only burn the backoff
            # budget and dead-letter. Loud, terminal, and out of the queue.
            _logger.warning(
                "outbox intent %s (%s) for booking %s SKIPPED: %s",
                work.id,
                work.effect,
                work.booking_id,
                skipped,
            )
            outcome = _Outcome.SKIPPED
        except Exception:
            _logger.exception(
                "outbox intent %s (%s) for booking %s failed", work.id, work.effect, work.booking_id
            )
            outcome = _Outcome.FAILED
        else:
            outcome = _Outcome.DELIVERED
        await _settle(
            sessionmaker,
            work,
            now=tick(),
            outcome=outcome,
            report=report,
            max_attempts=max_attempts,
            defer_for=defer_for,
        )

    # R9. Recorded HERE, not by the scheduler tick: a counter that only moves when the caller
    # remembers to report it reads zero for whichever path somebody forgot — and a zero meaning
    # "never measured" is indistinguishable from a zero meaning "nothing went wrong". `lost` is the
    # signal that a send outran its lease and a guest may have been messaged twice; it is worth
    # nothing if it can silently fail to be counted.
    observe_drain(report)
    return report


async def _settle(  # noqa: PLR0913 - the settle needs the work, the clock, the outcome, the report
    sessionmaker: Sessionmaker,
    work: OutboxWork,
    *,
    now: datetime,
    outcome: _Outcome,
    report: OutboxReport,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    defer_for: timedelta | None = None,
) -> None:
    """Record one intent's outcome in its OWN short transaction, releasing the lease.

    ==Only if the lease is still OURS.== The write is gated on
    ``status = 'claimed' AND claimed_by = <this worker>``, re-checked in this transaction. If the
    row no longer matches, our lease expired mid-send, the recovery pass returned the row to
    ``pending``, and somebody else now owns it — so our result is STALE. We discard it and say so;
    applying it would let us mark ``delivered`` an intent another worker is still executing, or
    stomp its bookkeeping. Writing where you no longer have the right is the same silent no-op as
    before, just pointed the other way."""
    async with sessionmaker() as session, session.begin():
        row = await _lock_if_still_ours(session, work)
        if row is None:
            # WHY we lost it decides which bucket it lands in, and that matters: `lost` is the
            # metric that proves in production whether the timeout assumption holds. Fill it with
            # routine cancellations and a real duplicate-send signal drowns in noise — and an alarm
            # that always fires is an alarm nobody reads.
            if await _lost_because_voided(session, work):
                report.voided_midflight.append(work.id)
            else:
                report.lost.append(work.id)
            return
        row.claimed_by = None
        row.lease_expires_at = None

        if outcome is _Outcome.DEFERRED:
            # WAITING — on a sibling intent (seconds), or on a human who switched the rule off
            # (minutes, or never). Rescheduled at the handler's own distance, WITHOUT counting an
            # attempt, so a legitimate wait never dead-letters. The row stays ``pending``: the whole
            # point is that a wait is REVERSIBLE, and the message survives it.
            row.status = _PENDING
            row.next_retry_at = now + (
                timedelta(seconds=DEFER_DELAY_SECONDS) if defer_for is None else defer_for
            )
            report.deferred.append(row.id)
            return

        if outcome is _Outcome.SKIPPED:
            # Terminal, and it costs no attempt: nothing was tried, so nothing failed.
            row.status = _SKIPPED
            row.next_retry_at = None
            report.skipped.append(row.id)
            return

        if outcome is _Outcome.UNKNOWN:
            # Terminal, and it DID cost an attempt: we really did call the provider. Parked for a
            # human and never auto-retried - a retry could message the guest twice and under-count
            # the cap protecting them. The in-flight marker is left standing on the row on purpose:
            # it is the evidence of what happened, and `outbox resolve-unknown` is what clears it.
            row.attempts += 1
            row.last_attempt_at = now
            row.status = _UNKNOWN
            row.next_retry_at = None
            report.unknown.append(row.id)
            return

        row.attempts += 1
        row.last_attempt_at = now

        if outcome is _Outcome.DELIVERED:
            row.status = _DELIVERED
            row.next_retry_at = None
            report.delivered.append(row.id)
            return

        if outcome is _Outcome.FAILED:
            if row.attempts >= max_attempts:
                _park_dead(row)
                report.dead.append(row.id)
            else:
                row.status = _FAILED
                row.next_retry_at = now + backoff_delay(row.attempts)
                report.failed.append(row.id)
            return

        assert_never(outcome)


async def _lost_because_voided(session: AsyncSession, work: OutboxWork) -> bool:
    """Log why our result is being discarded; return whether it was a VOID rather than a lost lease.

    Both end in "we do not write", but they are different facts. One is the system working as
    designed: a cancellation retired the step under us — routine, expected, harmless. The other is a
    real failure of our own timing assumption: our send outran its lease, the row was recovered, and
    somebody else owns it now — which means the effect may have been executed TWICE.

    Collapsing them into one bucket would file routine cancellations into the very counter that is
    supposed to alert on duplicate sends.
    """
    current = await session.get(Outbox, work.id)
    status = current.status if current is not None else "gone"

    if status == _VOIDED:
        _logger.warning(
            "outbox intent %s (%s) for booking %s: VOIDED mid-flight by a booking transition "
            "(cancel / reschedule / no-show). Our result is discarded and the step will not be "
            "retried. The send may already have reached the provider — it cannot be recalled",
            work.id,
            work.effect,
            work.booking_id,
        )
        return True

    _logger.error(
        "outbox intent %s (%s) for booking %s: LEASE LOST mid-flight (we held it as %s; it is now "
        "%s). The result is discarded, not applied — somebody else owns this row. The send may "
        "have happened, so it can be delivered twice: a provider call outran the lease. Shorten "
        "PROVIDER_TIMEOUT_CEILING or lengthen DEFAULT_LEASE",
        work.id,
        work.effect,
        work.booking_id,
        work.claimed_by,
        status,
    )
    return False


async def _lock_if_still_ours(session: AsyncSession, work: OutboxWork) -> Outbox | None:
    """Re-read the row under a row lock, but ONLY while this worker still holds its lease.

    The predicate is the whole point: ``status = 'claimed' AND claimed_by = <us>``. A row that has
    been recovered (back to ``pending``) or re-claimed by somebody else matches nothing, and we get
    ``None`` — which the caller turns into "discard the result, loudly".

    ``FOR UPDATE`` (a no-op on SQLite, which serialises writers) makes the check-then-write atomic:
    without it, a recovery pass could slip between our SELECT and our UPDATE and we would be writing
    on a row we had just proven was ours a moment ago."""
    return (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.id == work.id,
                Outbox.status == _CLAIMED,
                Outbox.claimed_by == work.claimed_by,
            )
            .with_for_update()
        )
    ).one_or_none()


def _park_dead(row: Outbox) -> None:
    """Park an exhausted intent as ``dead``: no further automatic retry, and a loud line for ops."""
    row.status = _DEAD
    row.next_retry_at = None
    _logger.error(
        "outbox intent %s (%s) for booking %s DEAD after %d attempts; parked, no retry",
        row.id,
        row.effect,
        row.booking_id,
        row.attempts,
    )


# --------------------------------------------------------------------------------------
# Shared staleness / dependency reads (the handlers' READ phase leans on these).
# --------------------------------------------------------------------------------------


async def _is_chain_current(session: AsyncSession, booking: Booking) -> bool:
    """True iff ``booking`` is the chain's single live (non-cancelled) member.

    A reschedule successor inherits its predecessor's ``ical_uid``, so a chain has one UID and the
    sole non-cancelled row is the current booking. A replaced predecessor (now cancelled), or an
    ambiguous 0/>1-active state (a conservative skip), returns ``False``.

    NOTE the trap: for a CANCELLED booking this is False *by construction*. Only an effect that
    :func:`staleness_policy` calls SUBJECT may be gated on it — a terminal effect (a refund, a hold
    expiry, a cancellation notice) acts on a cancelled booking on purpose.
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


async def _should_skip_as_stale(session: AsyncSession, booking: Booking, work: OutboxWork) -> bool:
    """Whether this intent must be dropped because a later transition overtook its booking."""
    if staleness_policy(work.effect, work.payload) is Staleness.EXEMPT:
        return False
    return not await _is_chain_current(session, booking)


async def _chain_awaits_meeting_link(session: AsyncSession, booking: Booking) -> bool:
    """True while a non-terminal Google intent that WOULD write the chain's Meet link is queued.

    Only an ``upsert``/``reschedule`` (which set ``meeting_url`` / ``external_event_id``) counts — a
    ``delete`` never blocks anything. Scoped to the whole CHAIN (every booking sharing the
    ``ical_uid``), not just this booking, because a reschedule successor's event id is produced by
    its predecessor's intent. A dead-lettered sync no longer counts, so a permanently-failing Google
    never wedges an email forever (it goes out without the link — degraded, not lost).
    """
    rows = (
        await session.scalars(
            select(Outbox)
            .join(Booking, Booking.id == Outbox.booking_id)
            .where(
                Booking.ical_uid == booking.ical_uid,
                Outbox.effect == OutboxEffect.GOOGLE.value,
                Outbox.status.in_(_NON_TERMINAL),
            )
        )
    ).all()
    producing = {GoogleOperation.UPSERT.value, GoogleOperation.RESCHEDULE.value}
    return any(row.payload.get("operation") in producing for row in rows)


# --------------------------------------------------------------------------------------
# The email effect — read (no send) → send (no txn) → record (txn).
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _EmailPlan:
    """What the email send needs, snapshotted so the I/O touches no session."""

    booking_id: uuid.UUID
    kind: NotificationKind
    message: Any  # email.message.EmailMessage — kept loose so this stays a pure data carrier.


async def run_email_effect(
    sessionmaker: Sessionmaker, work: OutboxWork, now: datetime, *, sender: EmailSender
) -> None:
    """Execute an email intent: send the ``kind`` notification for its booking (idempotent).

    Phase 1 (a read transaction) decides whether to send and composes the message; phase 2 sends it
    with **no transaction open**; phase 3 records the ledger row. The notice is dropped when the
    booking has been overtaken (per :func:`staleness_policy` — a cancellation is exempt and always
    sends), and it DEFERS (no attempt consumed) while the chain still has an undelivered Google sync
    that will produce its Meet link, so the notice carries the link even when the sync only succeeds
    on a later retry."""
    async with sessionmaker() as session:
        plan = await _prepare_email(session, work)
        await session.rollback()  # a pure read: release the connection before any network call
    if plan is None:
        return

    await sender.send(plan.message)

    async with sessionmaker() as session, session.begin():
        booking = await session.get(Booking, plan.booking_id)
        if booking is None:  # pragma: no cover - defensive: cascade-deleted mid-send
            return
        await record_booking_notification(
            session, booking=booking, kind=plan.kind, now=now, channel=Channel.EMAIL
        )


async def _prepare_email(session: AsyncSession, work: OutboxWork) -> _EmailPlan | None:
    """The email's READ phase: decide, then compose. ``None`` = there is nothing to send."""
    booking = await session.get(Booking, work.booking_id)
    if booking is None:  # pragma: no cover - defensive: the FK cascade makes this near-impossible
        return None
    payload = work.payload
    kind = NotificationKind(payload["kind"])

    if await _should_skip_as_stale(session, booking, work):
        # A later transition superseded this booking: drop the notice (never mail a "confirmed"
        # after a "cancelled", nor a reminder for a slot that was rescheduled away).
        return None
    if booking.meeting_url is None and await _chain_awaits_meeting_link(session, booking):
        raise OutboxDeferred(f"email for booking {booking.id} awaits its Google Meet link")
    if await notification_already_sent(session, booking=booking, kind=kind, channel=Channel.EMAIL):
        # A replay of an intent whose send already landed (the settle crashed after the ledger row
        # committed). The ledger is the proof; do not mail the guest twice.
        return None

    message = await compose_booking_notification(
        session,
        kind=kind,
        booking=booking,
        cancel_url=payload.get("cancel_url"),
        reschedule_url=payload.get("reschedule_url"),
        locale=payload.get("locale", "es"),
        # Use the sequence snapshotted at the transition, not the booking's live (possibly
        # later-bumped) value, so the chain's emails stay strictly increasing per UID (F1-08).
        sequence=payload.get("sequence"),
    )
    return _EmailPlan(booking_id=booking.id, kind=kind, message=message)


# --------------------------------------------------------------------------------------
# The Google effect — read (no call) → call (no txn) → write back (txn).
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _GooglePlan:
    """What the Google call needs, snapshotted so the I/O touches no session.

    TWO calendars, deliberately, because they are not always the same one (RF-11):

    * ``home`` — where the chain's event ACTUALLY lives, read from the columns the create wrote onto
      the booking. A delete or a move must act HERE, on the calendar the event really went to.
    * ``target`` — where a NEW event belongs, resolved from the host's configuration right now.

    They diverge the moment an operator re-designates the booking calendar between the confirmation
    and the cancellation. Aiming the delete at ``target`` would hit a calendar the event was never
    written to: Google answers 404, the (correct) idempotent delete counts that as a success, and
    the real event sits in the host's original calendar forever while the system reports it gone.
    """

    booking_id: uuid.UUID
    operation: GoogleOperation
    external_event_id: str | None
    request: MeetEventRequest | None
    # PRIMITIVES ONLY across the I/O boundary. The plan outlives its session (the connection is
    # released before the network call, R8), so an ORM object here would be detached and reading
    # even its id — in a write-back, or merely to name it in an error — would raise
    # DetachedInstanceError instead of the CalendarSyncError the retry logic expects.
    #
    # Where the event LIVES; a delete/move acts here. ``None`` = the chain has no event yet.
    home_calendar_id: str | None
    home_service: Any
    # Where a NEW event goes; a create/move lands here. ``None`` only for a DELETE.
    target_connection_id: uuid.UUID | None
    target_calendar_id: str | None
    target_service: Any


async def run_google_effect(
    sessionmaker: Sessionmaker,
    work: OutboxWork,
    now: datetime,
    *,
    service_factory: ServiceFactory,
) -> None:
    """Execute a Google-sync intent: create / reschedule / delete the booking's calendar event.

    The per-``ical_uid`` advisory lock that used to serialise a chain's Google effects is **gone**,
    deliberately: it was a lock held across network I/O — the exact thing R8 forbids — and it
    existed
    only to stop a DELETE running before the CREATE that produces the event id it must remove (which
    would leave an orphaned event in the host's calendar). That is a *causal dependency*, not a
    mutual exclusion, and the outbox already has a first-class way to express one: a DELETE that
    cannot resolve an event id while the chain still has a live create/reschedule intent now DEFERS
    (see :func:`_prepare_google`). Lock-free, correct across processes, and it holds no connection.
    """
    async with sessionmaker() as session:
        plan = await _prepare_google(session, work, service_factory)
        await session.rollback()  # a pure read: release the connection before any network call
    if plan is None:
        return

    if plan.operation is GoogleOperation.DELETE:
        if plan.home_calendar_id is not None and plan.external_event_id is not None:
            # Delete where the event LIVES, never where the host is configured now.
            await delete_event_for_booking(
                calendar_id=plan.home_calendar_id,
                external_event_id=plan.external_event_id,
                service=plan.home_service,
            )
        return

    request = plan.request
    target_calendar_id = plan.target_calendar_id
    if request is None or target_calendar_id is None:  # pragma: no cover - prepare builds both
        return
    if (
        plan.operation is GoogleOperation.RESCHEDULE
        and plan.home_calendar_id is not None
        and plan.external_event_id is not None
    ):
        new_id, meeting_url = await reschedule_event_for_booking(
            source_calendar_id=plan.home_calendar_id,
            source_service=plan.home_service,
            target_calendar_id=target_calendar_id,
            target_service=plan.target_service,
            external_event_id=plan.external_event_id,
            request=request,
        )
    else:
        new_id, meeting_url = await create_event_for_booking(
            calendar_id=target_calendar_id, request=request, service=plan.target_service
        )

    async with sessionmaker() as session, session.begin():
        booking = await session.get(Booking, plan.booking_id)
        if booking is None:  # pragma: no cover - defensive: cascade-deleted mid-call
            return
        booking.external_event_id = new_id
        booking.meeting_url = meeting_url
        # WHERE it landed, written in the SAME transaction as the id itself. Without this pair a
        # later cancel can only guess the calendar, and a guess that misses is indistinguishable
        # from an event that was already deleted — a silent orphan (RF-11).
        booking.external_connection_id = plan.target_connection_id
        booking.external_calendar_id = target_calendar_id


async def _prepare_google(
    session: AsyncSession, work: OutboxWork, service_factory: ServiceFactory
) -> _GooglePlan | None:
    """The Google effect's READ phase: resolve, decide, build the client. ``None`` = skip.

    The intent names the HOST, not a connection: the calendar is resolved HERE, from the live
    configuration, so there is one source of truth for "where does this event go" rather than a
    snapshot taken at enqueue time that can rot before the drain.

    This raises rather than skipping when the host's calendar cannot be resolved, and the difference
    matters: the intent exists ONLY because the host had an active connection when the booking was
    taken (:func:`aethercal.server.services.bookings._enqueue_google` enqueues nothing otherwise).
    So "no calendar now" is not the self-hoster — it is a real failure with a real victim: the guest
    is confirmed and the host's calendar is empty. It retries, then dead-letters into the visible
    backlog, instead of passing as a delivered no-op.
    """
    booking = await session.get(Booking, work.booking_id)
    if booking is None:  # pragma: no cover - defensive: the FK cascade makes this near-impossible
        return None
    payload = work.payload
    host_id = await _payload_host_id(session, payload)
    if host_id is None:
        raise CalendarTargetMissingError(
            f"booking {booking.id}: the Google intent names neither a host nor a resolvable "
            "connection"
        )
    operation = GoogleOperation(payload["operation"])

    # Resolve the event id from CURRENT DB state, never from the enqueue-time snapshot: an intent
    # queued before the booking's CREATE drained captured nothing (the event did not exist yet); by
    # now the create — or the reschedule predecessor — has populated ``external_event_id``.
    external_event_id = await _resolve_event_id(session, booking, payload)
    home = (
        await _event_home(session, booking, host_id=host_id)
        if external_event_id is not None
        else None
    )

    if operation is GoogleOperation.DELETE:
        if external_event_id is None and await _chain_awaits_meeting_link(session, booking):
            # The event this delete must remove is still being CREATED by a sibling intent. Running
            # now would resolve a NULL id, no-op, and leave the created event orphaned in the host's
            # calendar forever. Wait for the create to settle — a causal dependency, expressed as a
            # deferral instead of as a lock held across the network call.
            raise OutboxDeferred(
                f"calendar delete for booking {booking.id} awaits the chain's pending create"
            )
        return _GooglePlan(
            booking_id=booking.id,
            operation=operation,
            external_event_id=external_event_id,
            request=None,
            home_calendar_id=home.calendar_id if home is not None else None,
            home_service=service_factory(home.connection) if home is not None else None,
            target_connection_id=None,
            target_calendar_id=None,
            target_service=None,
        )

    # Reconcile to the chain's CURRENT desired state rather than trusting drain order: a create/move
    # runs ONLY for the booking that is the chain's live member. A predecessor that a reschedule has
    # already replaced is skipped even if its own intent drains AFTER the successor's (two workers,
    # inverted order), so it never (re)creates an event the chain has moved on from.
    if await _should_skip_as_stale(session, booking, work):
        return None
    if operation is GoogleOperation.UPSERT and booking.external_event_id is not None:
        # A replay of an upsert whose event was already created (the settle crashed after the
        # write-back committed). Creating again would duplicate the event in the host's calendar.
        return None

    target = await _require_calendar_target(session, booking=booking, host_id=host_id)
    return _GooglePlan(
        booking_id=booking.id,
        operation=operation,
        external_event_id=external_event_id,
        request=_meet_request_from_payload(payload),
        home_calendar_id=home.calendar_id if home is not None else None,
        home_service=service_factory(home.connection) if home is not None else None,
        target_connection_id=target.connection.id,
        target_calendar_id=target.calendar_id,
        target_service=service_factory(target.connection),
    )


async def _payload_host_id(session: AsyncSession, payload: Mapping[str, Any]) -> uuid.UUID | None:
    """The host a Google intent is for — accepting the PREVIOUS payload shape as well.

    The intent used to name a ``connection_id``; it now names a ``host_id`` (the calendar is
    resolved from live configuration at drain time, so there is one source of truth rather
    than a snapshot that can rot). Rows queued by the previous build are sitting in the outbox
    the moment the new code deploys. A reader that cannot understand them fails them six times
    each and dead-letters the lot — a self-inflicted outage, on every upgrade, over a change
    that is purely internal.

    So the reader accepts both shapes and derives the host from the old connection. Migrating the
    rows instead would work too, but only if it ran BEFORE the new consumer; tolerating both formats
    does not depend on getting a deploy order right.
    """
    raw_host = payload.get("host_id")
    if isinstance(raw_host, str):
        return uuid.UUID(raw_host)
    raw_connection = payload.get("connection_id")
    if not isinstance(raw_connection, str):
        return None
    connection = await session.get(ExternalConnection, uuid.UUID(raw_connection))
    if connection is None:
        return None
    _logger.info(
        "google intent carries the legacy connection_id payload; resolved host %s from "
        "connection %s",
        connection.user_id,
        connection.id,
    )
    return connection.user_id


async def _require_calendar_target(
    session: AsyncSession, *, booking: Booking, host_id: uuid.UUID
) -> CalendarTarget:
    """The host's booking calendar, or a LOUD failure — never a silent skip.

    ``resolve_calendar_target`` itself raises :class:`AmbiguousCalendarTargetError` when the host's
    configuration does not name exactly ONE calendar (several connected accounts, or several linked
    calendars, with no designated target). Guessing there is what the old ``.first()`` did: it wrote
    a real client's meeting into an arbitrary calendar and reported success.
    """
    target = await resolve_calendar_target(session, tenant_id=booking.tenant_id, user_id=host_id)
    if target is None:
        _logger.error(
            "booking %s: host %s had a connected calendar when the booking was taken and none "
            "resolves now; the event was NOT synced",
            booking.id,
            host_id,
        )
        raise CalendarTargetMissingError(
            f"booking {booking.id}: no active calendar connection for host {host_id}"
        )
    return target


async def _event_home(
    session: AsyncSession, booking: Booking, *, host_id: uuid.UUID
) -> CalendarTarget:
    """The calendar the chain's existing event ACTUALLY lives in — read, never guessed.

    The create wrote ``external_connection_id`` / ``external_calendar_id`` in the same transaction
    as the event id, and this reads them back (walking the ``rescheduled_from_id`` chain, because a
    successor whose own sync has not drained yet inherits its predecessor's event).

    Two edge cases, kept apart:

    * **No recorded calendar** — reachable only for a booking whose event predates these columns.
      There is no other information to act on, so it falls back to the host's current target —
      EXPLICITLY, with a warning naming the booking, because it IS a guess: if the host has since
      moved their booking calendar, the legacy event is not there. An operator can act on a log
      line; they cannot act on a guess made quietly.
    * **The recorded connection row is gone** — the calendar the event lives in is unreachable by
      definition, and guessing another would report success while the event lives on. Raise.
    """
    owner = booking
    seen: set[uuid.UUID] = set()
    while owner.external_event_id is None and owner.rescheduled_from_id is not None:
        if owner.id in seen:  # pragma: no cover - defensive: the chain is acyclic by construction
            break
        seen.add(owner.id)
        ancestor = await session.get(Booking, owner.rescheduled_from_id)
        if ancestor is None:  # pragma: no cover - defensive: SET NULL only on a parent-row delete
            break
        owner = ancestor

    if owner.external_connection_id is None or owner.external_calendar_id is None:
        _logger.warning(
            "booking %s holds external event %s but no recorded calendar (it predates the column); "
            "falling back to host %s's currently configured booking calendar",
            owner.id,
            owner.external_event_id,
            host_id,
        )
        return await _require_calendar_target(session, booking=booking, host_id=host_id)

    connection = await session.get(ExternalConnection, owner.external_connection_id)
    if connection is None:
        raise CalendarTargetMissingError(
            f"booking {owner.id}: the connection its calendar event lives in is gone"
        )
    return CalendarTarget(connection=connection, calendar_id=owner.external_calendar_id)


async def _resolve_event_id(
    session: AsyncSession, booking: Booking, payload: Mapping[str, Any]
) -> str | None:
    """The Google event id to act on, resolved at drain time (falls back to the payload snapshot).

    Prefer live DB state over the enqueue-time snapshot. The booking's own ``external_event_id``
    wins; if it is unset — a successor whose own create/move has not drained yet — walk the
    ``rescheduled_from_id`` chain to the live event the chain already has. Without this, cancelling
    a
    not-yet-synced reschedule (the successor has no id of its own) would resolve to NULL and orphan
    the predecessor's still-active event."""
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


def _meet_request_from_payload(payload: Mapping[str, Any]) -> MeetEventRequest:
    """Rebuild the Google Meet event request from a Google intent's stored primitives."""
    return MeetEventRequest(
        summary=str(payload["summary"]),
        start=datetime.fromisoformat(str(payload["start"])),
        end=datetime.fromisoformat(str(payload["end"])),
        timezone=str(payload["timezone"]),
        guest_email=str(payload["guest_email"]),
    )


# --------------------------------------------------------------------------------------
# The NOTIFY effect — one workflow step, on one channel.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NotifyPlan:
    """What the step's send needs, snapshotted so the I/O touches no session."""

    booking_id: uuid.UUID
    kind: str
    """The ledger key. A ``str``, not a :class:`NotificationKind`: ``workflow_steps.kind`` is
    free-text BY DESIGN, so a tenant may define a ``follow_up`` step and give it a template."""
    channel: Channel
    step_id: uuid.UUID
    message: Any  # an EmailMessage for the email channel; a rendered plain body for the others.
    recipient: str


PROVIDER_CALL_MARKER = "provider_call_started_at"
"""Payload key: "this step was handed to a PHONE provider, and we have not recorded the answer yet".

Committed BEFORE the network call and cleared only once the outcome is KNOWN. If a later drain finds
it still set on a row whose ledger entry never landed, the worker died inside the window between
"the provider accepted" and "the ledger committed" - so the message may already be with the guest,
and re-sending it blind would both duplicate it AND under-count the daily cap that protects them
(the cap is derived from the very ledger row we failed to write). See
:class:`OutboxUnknownOutcome`.

PHONE channels only, deliberately. Email keeps its long-standing at-least-once residual: its
failure modes surface from ``aiosmtplib`` as an undifferentiated ``Exception``, so there is no
honest way to tell "the relay never took it" from "the relay took it and we lost the answer".
Marking email would park a step on every ordinary SMTP blip - a rare duplicate traded for a common
outage,
which is the worse deal.
"""


async def _mark_provider_call_started(
    sessionmaker: Sessionmaker, work: OutboxWork, now: datetime
) -> None:
    """Record, in its OWN committed transaction, that this step is about to reach the provider.

    It has to be committed BEFORE the I/O - that is the entire point. A marker written in the same
    transaction as the result would vanish along with the crash it exists to detect."""
    async with sessionmaker() as session, session.begin():
        row = await session.get(Outbox, work.id)
        if row is None:  # pragma: no cover - defensive: cascade-deleted mid-flight
            return
        # A NEW dict: SQLAlchemy does not track in-place mutation of a plain JSON column, so
        # mutating the existing one would flush nothing and leave the marker unwritten - the silent
        # no-op, sitting in the middle of the machinery built to catch one.
        row.payload = {**row.payload, PROVIDER_CALL_MARKER: now.isoformat()}


async def _clear_provider_call_marker(sessionmaker: Sessionmaker, work: OutboxWork) -> None:
    """Drop the marker: the outcome is KNOWN, and it is "the guest did not get this message"."""
    async with sessionmaker() as session, session.begin():
        row = await session.get(Outbox, work.id)
        if row is None:  # pragma: no cover - defensive
            return
        row.payload = {
            key: value for key, value in row.payload.items() if key != PROVIDER_CALL_MARKER
        }


async def run_notify_effect(
    sessionmaker: Sessionmaker,
    work: OutboxWork,
    now: datetime,
    *,
    sender: EmailSender | None,
    channels: Mapping[Channel, PhoneChannelSender],
) -> None:
    """Execute one workflow step: send its message on its channel (RF-24).

    The same three phases as every other handler — read, send with NO transaction open, record — so
    the network call holds no row lock and no pool connection.

    Two channel families, deliberately:

    * **email** goes through the existing composer, not through the plain-body sender. That is what
      carries the ``.ics`` invite, and it is what keeps the ledger key identical to the one the
      retired scheduler wrote — which is exactly what stops a live booking being reminded twice.
    * **whatsapp / sms** go through their :class:`PhoneChannelSender`, with the body RENDERED from
      the tenant's ``workflow_templates`` row (or the built-in fallback). An unconfigured channel is
      a DISABLED FEATURE, not an error: the step is SKIPPED with its reason, never failed.

    A send that the provider will NEVER accept (:class:`SendRefused` — a malformed number, an
    over-cap recipient) is turned into an :class:`OutboxSkipped`: terminal, no attempt consumed, out
    of the queue. A transient one (:class:`ChannelUnavailable`, a 5xx, a timeout) is left to
    propagate, so the drain fails it and retries with backoff. Collapsing the two would either fill
    the dead-letter with numbers that can never work, or throw away a message the provider would
    have accepted a minute later.
    """
    async with sessionmaker() as session:
        plan = await _prepare_notify(session, work, now, sender=sender, channels=channels)
        await session.rollback()  # a pure read: release the connection before any network call
    if plan is None:
        return

    if plan.channel is Channel.EMAIL:
        if sender is None:  # pragma: no cover - _prepare_notify already skipped this case
            raise RuntimeError("an email step reached the send with no configured SMTP sender")
        try:
            await sender.send(plan.message)
        except SendRefused as refused:
            raise OutboxSkipped(str(refused)) from refused
        await _record_notify_sent(sessionmaker, work, plan, now)
        return

    # A PHONE send. Everything below exists because the window between "the provider accepted" and
    # "the ledger committed" is not free: a crash inside it means the guest may already have the
    # message while nothing records it - so a blind retry would send it TWICE and under-count the
    # daily cap that protects them, because that cap is derived from the very ledger row we failed
    # to write. The two failures compound. So the intent to call the provider is PERSISTED first.
    await _mark_provider_call_started(sessionmaker, work, now)
    try:
        await channels[plan.channel].send(to=plan.recipient, subject=None, body=str(plan.message))
    except SendOutcomeUnknown as unknown:
        # The request left this machine and the answer was lost. LEAVE the marker standing: we do
        # not know what happened, and the drain must park this rather than guess.
        raise OutboxUnknownOutcome(
            f"{_UNKNOWN_OUTCOME}: the {plan.channel.value} send for booking {plan.booking_id} "
            f"reached the provider and the answer was lost ({unknown}). It is NOT retried - that "
            "could message the guest twice and under-count the cap protecting them. A human checks "
            "the provider, then runs: aethercal-admin outbox resolve-unknown"
        ) from unknown
    except (SendRefused, ChannelUnavailable):
        # A KNOWN non-delivery: the provider answered, or we never connected at all. Nothing is in
        # flight, so drop the marker and let the normal machinery retire it (SendRefused) or retry
        # it (ChannelUnavailable).
        await _clear_provider_call_marker(sessionmaker, work)
        raise
    except BaseException:
        # Anything else - including the CancelledError raised when the drain's PROVIDER_TIMEOUT
        # aborts the call mid-flight. We do NOT know whether the provider saw it, so the marker
        # STAYS and the next drain treats it as unknown instead of re-sending blind.
        raise

    await _record_notify_sent(sessionmaker, work, plan, now, clear_marker=True)


async def _record_notify_sent(
    sessionmaker: Sessionmaker,
    work: OutboxWork,
    plan: _NotifyPlan,
    now: datetime,
    *,
    clear_marker: bool = False,
) -> None:
    """Write the ledger row - and clear the in-flight marker in the SAME transaction.

    Atomic on purpose. Clear the marker in a separate transaction and a crash between the two puts
    the row straight back into the ambiguous state this whole mechanism exists to remove: no marker,
    no ledger row, and a message that may well already be on the guest's phone."""
    async with sessionmaker() as session, session.begin():
        booking = await session.get(Booking, plan.booking_id)
        if booking is None:  # pragma: no cover - defensive: cascade-deleted mid-send
            return
        await record_booking_notification(
            session,
            booking=booking,
            kind=plan.kind,
            now=now,
            channel=plan.channel,
            step_id=plan.step_id,
        )
        if clear_marker:
            row = await session.get(Outbox, work.id)
            if row is not None:
                row.payload = {
                    key: value for key, value in row.payload.items() if key != PROVIDER_CALL_MARKER
                }


# The skip reasons, as distinct machine-greppable prefixes. "We could not send" and "we were not
# ALLOWED to send" are completely different facts about the world, and an operator reading the log
# during an incident — or a regulator reading it afterwards — has to be able to tell them apart.
_NO_PHONE = "no-phone"
_NO_CONSENT = "no-phone-consent"
_CHANNEL_UNCONFIGURED = "channel-unconfigured"
_UNKNOWN_OUTCOME = "unknown-outcome"
"""The provider was given the message and the answer was lost. NEVER re-sent blind."""
_NO_TEMPLATE = "no-template"
"""The kind has no body: no tenant ``workflow_templates`` row, and no built-in fallback for it."""
_BAD_TEMPLATE = "bad-template"
"""The body exists but will not render (an unknown variable, an expression). The TENANT fixes it."""
_RULE_GONE = "workflow-gone"
_RULE_PAUSED = "workflow-inactive"
_TOO_LATE = "moment-passed"


def message_deadline(trigger: WorkflowTrigger, booking: Booking) -> datetime:
    """The last instant at which this step's message can still do its job.

    Exhaustive over the trigger (``assert_never``), because "how late is too late" is not the same
    question for every message and a silent default would answer it wrongly for four of the five:

    * ``on_booking`` / ``before_start`` → the booking's **start**. They speak about a meeting that
    is
      still coming. "Your booking is confirmed" or "your meeting is tomorrow", delivered after it
      began, is not a late message — it is a WRONG one. This is the same rule the materialiser
      already obeys when it refuses to queue a reminder whose moment has gone.
    * ``after_end`` / ``on_cancel`` / ``on_no_show`` → the **end** plus
      :data:`TERMINAL_MESSAGE_GRACE`. These remain TRUE after their moment (a booking really was
      cancelled; the meeting really did happen), so lateness does not falsify them — but a row may
      not wait for ever.

    It is what BOUNDS a pause: a step whose rule is switched off waits for the rule to come back,
    and
    stops waiting here. Without it, "pause instead of skip" would trade a message destroyed too
    early
    for a row that polls until the end of time — and for a "reminder" delivered after the
    meeting."""
    match trigger:
        case WorkflowTrigger.ON_BOOKING | WorkflowTrigger.BEFORE_START:
            return as_utc(booking.start_at)
        case WorkflowTrigger.AFTER_END | WorkflowTrigger.ON_CANCEL | WorkflowTrigger.ON_NO_SHOW:
            return as_utc(booking.end_at) + TERMINAL_MESSAGE_GRACE
        case _ as unreachable:
            assert_never(unreachable)


async def _gate_on_the_rule(
    session: AsyncSession,
    work: OutboxWork,
    booking: Booking,
    trigger: WorkflowTrigger,
    now: datetime,
) -> None:
    """Decide whether this step may be sent NOW — and, if not, whether that is a WAIT or an END.

    ==That distinction is the whole function.== ``active`` is already honoured when a booking's
    steps
    are QUEUED (``_active_workflows``), and that is not enough: a step is queued days before it is
    sent, so switching a rule off this afternoon would still send tomorrow's messages, and the
    tenant
    who turned it off would have no way to explain them. The flag has to govern at the SEND, which
    is
    the only moment anybody outside can observe.

    But an inactive rule is a **TEMPORARY** condition — the tenant can switch it back on — so it may
    NOT have a terminal outcome. Retiring the step (``OutboxSkipped``) would destroy the message a
    tenant could still want, exactly like voiding the row would; the damage simply comes in through
    the other door. So the step is **PAUSED** (:class:`OutboxDeferred`, still ``pending``, no
    attempt
    consumed) and asks again every :data:`PAUSED_RULE_RECHECK`. Switch the rule back on and the very
    same row — same dedupe key, same exactly-once identity — is delivered.

    The wait is bounded by :func:`message_deadline`, not by a counter: a paused step stops waiting
    when its message could no longer do its job. That single check also protects the send itself, so
    a rule re-enabled a week late cannot fire a "reminder" for a meeting that already happened.

    Terminal, by contrast, are the two things that cannot be undone:

    * the payload names no workflow at all — no rule can vouch for it (fail-closed);
    * the workflow is GONE (an event type deleted with ``ON DELETE CASCADE`` takes its workflows
      with it). Its queued steps would otherwise still be delivered — messages from a rule that
      exists nowhere. A deleted rule is not coming back.
    """
    if now > message_deadline(trigger, booking):
        raise OutboxSkipped(
            f"{_TOO_LATE}: this {trigger.value} step's moment has passed (deadline "
            f"{message_deadline(trigger, booking).isoformat()}); a message that arrives after the "
            "fact is noise, so it is retired rather than delivered late"
        )

    raw = work.payload.get("workflow_id")
    if raw is None:
        raise OutboxSkipped(
            f"{_RULE_GONE}: this workflow step carries no workflow_id, so no rule can vouch for it"
        )
    workflow = await session.get(Workflow, uuid.UUID(str(raw)))
    if workflow is None or workflow.tenant_id != work.tenant_id:
        raise OutboxSkipped(
            f"{_RULE_GONE}: workflow {raw} no longer exists for this tenant, so the step it queued "
            "must not be delivered"
        )
    if not workflow.active:
        raise OutboxDeferred(
            f"{_RULE_PAUSED}: workflow {raw} ({workflow.name!r}) is switched off, so this step is "
            "PAUSED, not retired — it waits, and is delivered if the rule is switched back on "
            f"before {message_deadline(trigger, booking).isoformat()}",
            retry_after=PAUSED_RULE_RECHECK,
        )


def _require_phone_consent(booking: Booking, channel: Channel) -> None:
    """Refuse to message a phone without BOTH a number and a recorded consent. Legal, not stylistic.

    ``bookings.guest_phone_consent_at`` exists to PROVE the guest agreed to be messaged on that
    number. A column that is written and then never read is decorative — and the thing it was meant
    to prevent (an unconsented message to a real phone) happens anyway. So this is the only door
    into the WhatsApp/SMS path, and it is closed by default:

    * no number at all → there is nothing to send to;
    * a number but NO ``guest_phone_consent_at`` → the guest never agreed. Silence is not consent;
    * consent WITHDRAWN (the stamp set back to NULL) → the same gate closes again, automatically.
      Revocation needs no special code path: it IS the absence of the stamp.

    Each case carries its OWN reason, never merged with "the channel is not configured"."""
    if not booking.guest_phone:
        raise OutboxSkipped(
            f"{_NO_PHONE}: the guest gave no phone number, so the {channel.value} step cannot run"
        )
    if booking.guest_phone_consent_at is None:
        raise OutboxSkipped(
            f"{_NO_CONSENT}: the guest has not consented to be messaged on their phone, so the "
            f"{channel.value} step must not run (consent is recorded, or it did not happen)"
        )


async def _prepare_notify(
    session: AsyncSession,
    work: OutboxWork,
    now: datetime,
    *,
    sender: EmailSender | None,
    channels: Mapping[Channel, PhoneChannelSender],
) -> _NotifyPlan | None:
    """The step's READ phase: decide, then compose/render. ``None`` = nothing to send.

    Raises :class:`OutboxSkipped` for anything that can NEVER work — an unconfigured channel, a
    guest with no phone or no consent, a recipient over the channel's daily cap, a kind with no
    template, a step whose moment has passed — so the step is retired with its reason instead of
    being retried into the dead-letter. Raises :class:`OutboxDeferred` for anything that merely
    cannot run YET — a rule the tenant has switched off — so the step WAITS instead of being
    destroyed."""
    booking = await session.get(Booking, work.booking_id)
    if booking is None:  # pragma: no cover - defensive: the FK cascade makes this near-impossible
        return None

    payload = work.payload
    channel = Channel(payload["channel"])
    step_id = uuid.UUID(str(payload["step_id"]))
    kind = str(payload["kind"])
    locale = str(payload.get("locale", "es"))
    trigger = WorkflowTrigger(str(payload["trigger"]))

    if await _should_skip_as_stale(session, booking, work):
        # A later transition overtook the booking, and this step's trigger is not a terminal one.
        return None

    # May this step be sent NOW? The rule that queued it must still be switched on (else the step
    # WAITS — it is not destroyed), and its own moment must not have passed (else it is retired,
    # which is also what bounds the waiting). It gates BOTH channel families, and it runs before any
    # body is composed or rendered: a step from a switched-off rule must not reach a provider, and a
    # step whose moment has gone must not be built at all.
    await _gate_on_the_rule(session, work, booking, trigger, now)

    if await notification_already_sent(
        session, booking=booking, kind=kind, channel=channel, step_id=step_id
    ):
        # Already on the ledger. For a reminder this is precisely what the migration's re-keying
        # buys: a booking the retired scheduler already reminded is never reminded a second time.
        return None

    if channel is Channel.EMAIL:
        return await _prepare_notify_email(
            session, booking=booking, kind=kind, step_id=step_id, locale=locale, sender=sender
        )

    # ==THE CRASH WE CANNOT SEE FROM ANYWHERE ELSE.== The marker was committed before the previous
    # attempt called the provider, and the ledger check above says the send was never recorded. So a
    # worker died (or its call was aborted) inside the window between "the provider accepted" and
    # "the ledger committed" - and the guest may ALREADY have this message.
    #
    # Retrying is the intuitive move and it is wrong twice over: it can message a real person a
    # second time, and because the per-phone daily cap is DERIVED from that same unwritten ledger
    # row, it also under-counts the ceiling that protects them from exactly that. Park it, shout,
    # and let a human look at the provider.
    if work.payload.get(PROVIDER_CALL_MARKER):
        raise OutboxUnknownOutcome(
            f"{_UNKNOWN_OUTCOME}: a previous attempt handed this {channel.value} step to the "
            f"provider at {work.payload[PROVIDER_CALL_MARKER]} and never recorded the outcome - "
            "the worker died in the window between the provider accepting and the ledger "
            "committing. The guest may already have this message, so it is NOT re-sent. A human "
            "checks the provider, then runs: aethercal-admin outbox resolve-unknown"
        )

    # THE CONSENT GATE, and it comes FIRST — before the channel registry, before the cap, before the
    # template. Not as a nicety of ordering: it must be impossible to reach a send plan for a phone
    # we have no permission to message, however the checks below are later reordered.
    _require_phone_consent(booking, channel)

    phone_sender = channels.get(channel)
    if phone_sender is None:
        raise OutboxSkipped(
            f"{_CHANNEL_UNCONFIGURED}: the {channel.value} channel has no sender on this instance "
            "(a channel without credentials is a disabled feature, not an error)"
        )

    # THE CAP, before the render and long before the network call: an over-cap message is never
    # built and never handed to a provider. The sender carries its own ceilings — a phone sender
    # WITHOUT caps is unrepresentable (see PhoneChannelSender) — so there is no path through here
    # that reaches a provider with no ceiling in force.
    try:
        await enforce_phone_cap(
            session, booking=booking, channel=channel, caps=phone_sender.caps, now=now
        )
    except SendRefused as refused:
        raise OutboxSkipped(str(refused)) from refused

    template = await load_template(
        session, tenant_id=booking.tenant_id, channel=channel, kind=kind, locale=locale
    )
    if template is None:
        # A tenant-authored kind with no body anywhere — no template row, and no built-in for it.
        # An empty message is worse than no message, so the step is retired, and it says why.
        raise OutboxSkipped(
            f"{_NO_TEMPLATE}: the {channel.value} step of kind {kind!r} has no template for locale "
            f"{locale!r}, and there is no built-in fallback for that kind"
        )

    context = await build_template_context(session, booking=booking, locale=locale)
    try:
        rendered = render_template(
            template.body, subject=template.subject, context=context, channel=channel
        )
    except TemplateError as exc:
        # A malformed template will not render on the tenth attempt either. Retire it and NAME it,
        # so the tenant learns their template is broken rather than watching messages quietly fail
        # to arrive.
        raise OutboxSkipped(
            f"{_BAD_TEMPLATE}: the {template.source} {channel.value} template for kind {kind!r} "
            f"cannot be rendered: {exc}"
        ) from exc

    phone = booking.guest_phone
    if phone is None:  # pragma: no cover - _require_phone_consent proved this above
        raise OutboxSkipped(f"{_NO_PHONE}: the booking lost its phone number mid-flight")

    return _NotifyPlan(
        booking_id=booking.id,
        kind=kind,
        channel=channel,
        step_id=step_id,
        message=rendered.body,
        recipient=phone,
    )


async def _prepare_notify_email(  # noqa: PLR0913 - the plan's identity IS the keyword contract
    session: AsyncSession,
    *,
    booking: Booking,
    kind: str,
    step_id: uuid.UUID,
    locale: str,
    sender: EmailSender | None,
) -> _NotifyPlan | None:
    """The EMAIL branch: the built-in composer, which is what carries the ``.ics`` invite.

    An email step keeps going through :func:`compose_booking_notification` rather than the
    plain-body template path, for two reasons that are not stylistic: it is what attaches the
    calendar invite, and it keeps the ledger key identical to the retired reminder scheduler's —
    which is the thing that stops a live booking being reminded a second time.

    So an email step's ``kind`` must be one of the four built-in ones. A tenant's own kind has no
    composer here, and is retired with that reason rather than mailing something empty."""
    if sender is None:
        raise OutboxSkipped("the email channel has no configured SMTP sender")
    try:
        composer_kind = NotificationKind(kind)
    except ValueError as exc:
        raise OutboxSkipped(
            f"{_NO_TEMPLATE}: the email step of kind {kind!r} has no built-in composer (the four "
            "built-in kinds are the ones carrying the .ics invite); a custom kind is supported on "
            "the phone channels, which render from workflow_templates"
        ) from exc

    message = await compose_booking_notification(
        session,
        kind=composer_kind,
        booking=booking,
        # A workflow step carries no guest links — the same shape the retired reminder job used.
        cancel_url=None,
        reschedule_url=None,
        locale=locale,
    )
    return _NotifyPlan(
        booking_id=booking.id,
        kind=kind,
        channel=Channel.EMAIL,
        step_id=step_id,
        message=message,
        recipient=booking.guest_email,
    )


# --------------------------------------------------------------------------------------
# The dispatcher the scheduler tick injects as ``execute``.
# --------------------------------------------------------------------------------------


def make_booking_effect_executor(
    *,
    sessionmaker: Sessionmaker,
    sender: EmailSender | None,
    service_factory: ServiceFactory | None,
    channels: Mapping[Channel, PhoneChannelSender] | None = None,
) -> OutboxExecutor:
    """Build the live ``execute`` the drain injects: dispatch each intent to its handler.

    The dispatch is **exhaustive over** :class:`OutboxEffect`, enforced by ``assert_never``: pyright
    fails the build if an effect is added without a branch here. It used to be ``if EMAIL … else
    GOOGLE``, where the ``else`` silently *assumed* Google — so every new effect would have been
    executed as a Google Calendar call.

    ``channels`` is the registry of PHONE senders (email is deliberately not in it — it goes
    through the composer, which carries the ``.ics``). Its value type is
    :class:`PhoneChannelSender`, not a bare sender, and that is load-bearing rather than
    decorative: such a sender carries the daily caps it must not exceed, so ==an uncapped
    WhatsApp/SMS sender cannot be registered here at all==. Fail-closed as a TYPE, not as a comment.

    An absent channel is not a gap: its steps SKIP with a reason, never fail."""
    registry = dict(channels or {})

    async def _execute(work: OutboxWork, now: datetime) -> None:
        effect = work.effect
        if effect is OutboxEffect.EMAIL:
            if sender is None:  # pragma: no cover - live misconfiguration guard
                raise RuntimeError("outbox email intent has no configured SMTP sender")
            await run_email_effect(sessionmaker, work, now, sender=sender)
        elif effect is OutboxEffect.GOOGLE:
            if service_factory is None:  # pragma: no cover - live misconfiguration guard
                raise RuntimeError("outbox Google intent has no configured service factory")
            await run_google_effect(sessionmaker, work, now, service_factory=service_factory)
        elif effect is OutboxEffect.NOTIFY:
            await run_notify_effect(sessionmaker, work, now, sender=sender, channels=registry)
        else:
            assert_never(effect)

    return _execute


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_DRAIN_BATCH_SIZE",
    "DEFAULT_LEASE",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFER_DELAY_SECONDS",
    "PAUSED_RULE_RECHECK",
    "PROVIDER_CALL_MARKER",
    "PROVIDER_TIMEOUT_CEILING",
    "TERMINAL_MESSAGE_GRACE",
    "Clock",
    "GoogleOperation",
    "OutboxDeferred",
    "OutboxEffect",
    "OutboxExecutor",
    "OutboxReport",
    "OutboxSkipped",
    "OutboxUnknownOutcome",
    "OutboxWork",
    "ReconcileReport",
    "Staleness",
    "StepSchedule",
    "as_utc",
    "backoff_delay",
    "claim_one",
    "drain_outbox",
    "email_dedupe_key",
    "enqueue_effect",
    "google_dedupe_key",
    "make_booking_effect_executor",
    "message_deadline",
    "new_worker_id",
    "reconcile_workflow_steps",
    "recover_expired_leases",
    "run_email_effect",
    "run_google_effect",
    "run_notify_effect",
    "select_due",
    "staleness_policy",
    "trigger_staleness",
    "void_pending_steps",
    "workflow_key_prefix",
    "workflow_step_dedupe_key",
]
