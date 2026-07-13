"""The workflow step lifecycle (RF-24): what happens to a booking's steps when its life changes.

The engine that renders and enqueues steps lands in a later cut. What lives here is the part that
has to be decided ONCE, centrally, and that every later handler must obey: **the transition table**.

.. rubric:: Why a table, and not an upsert

``reschedule_booking`` does not mutate a booking. It opens a **new row** — the successor inherits
the ``ical_uid``, and the predecessor is marked ``CANCELLED`` (``services/bookings.py``). Outbox
uniqueness is keyed on ``booking_id``, so a successor's steps can never collide with the
predecessor's. Any design that leans on ``ON CONFLICT ... DO UPDATE`` to "move" a step therefore
matches **zero rows**, raises nothing, and passes every test — a silent no-op, which is the exact
disease this batch exists to cure. So the lifecycle is stated explicitly:

* **confirm** — materialise every forward-looking trigger. Nothing to void.
* **reschedule (predecessor)** — materialise nothing; void **every pending step, the exempt ones
  included**.
* **reschedule (successor)** — materialise every forward-looking trigger (a plain INSERT: the
  booking id is new, so nothing can conflict). Nothing to void.
* **cancel** — materialise ``on_cancel``; void every other pending step.
* **no_show** — materialise ``on_no_show``; void the pending ``after_end`` step.

Two of those cells are load-bearing, and both are the kind of bug that reaches a real guest:

* **Reschedule voids the predecessor's EXEMPT steps too.** An ``after_end`` follow-up is exempt from
  the staleness guard — the appointment is over, so "the chain moved on" is not a reason to suppress
  it. Leave the predecessor's copy ``pending`` and it *will* be delivered, at the hour of a meeting
  that never happened.
* **No-show voids the pending ``after_end``.** Otherwise the guest who did NOT show up receives
  "thanks for meeting with us".

.. rubric:: ``on_cancel`` fires from the OPERATION, never from the state change

A reschedule marks the predecessor ``CANCELLED`` — as an *implementation detail* of the swap. Hook
the ``on_cancel`` trigger to the STATUS instead of to the operation and every reschedule sends a
cancellation notice; and because ``on_cancel`` is exempt from the staleness guard, it really is
delivered, to a real guest. That is why :class:`BookingTransition` keeps ``CANCEL`` and
``RESCHEDULE_PREDECESSOR`` apart even though both leave the row cancelled."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import assert_never

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, Workflow, WorkflowStep
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.services.outbox import (
    OutboxEffect,
    as_utc,
    enqueue_effect,
    void_pending_steps,
    workflow_step_dedupe_key,
)

_logger = logging.getLogger(__name__)

# ``WorkflowTrigger`` is re-exported so the shared contract's import path holds. It is DEFINED in
# the leaf model module: the outbox classifies staleness by trigger, and the engine will import the
# outbox to enqueue — declaring the vocabulary here would close that into an import cycle.
__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_REMINDER_KIND",
    "DEFAULT_REMINDER_NAME",
    "DEFAULT_REMINDER_OFFSET_MINUTES",
    "BookingTransition",
    "StepAction",
    "WorkflowTrigger",
    "apply_booking_transition",
    "notify_payload",
    "seed_default_workflows",
    "step_action",
    "step_send_time",
    "triggers_to_materialise",
    "triggers_to_void",
]


class BookingTransition(StrEnum):
    """The events in a booking's life that change what its queued steps ought to be.

    ``RESCHEDULE_PREDECESSOR`` and ``CANCEL`` both end with a cancelled row, and they are
    deliberately NOT the same transition: only a real cancellation notifies the guest. See the
    module docstring."""

    CONFIRM = "confirm"
    RESCHEDULE_PREDECESSOR = "reschedule_predecessor"
    RESCHEDULE_SUCCESSOR = "reschedule_successor"
    CANCEL = "cancel"
    NO_SHOW = "no_show"


class StepAction(StrEnum):
    """What a transition does to one trigger's step."""

    MATERIALISE = "materialise"
    """Enqueue it (a plain INSERT — the booking id is new, so nothing can conflict)."""
    VOID = "void"
    """Retire it if it is still pending: the world it was queued for no longer exists."""
    LEAVE = "leave"
    """Not this transition's business (it has already gone out, or it is not queued yet)."""


# The forward-looking triggers: the ones a booking coming into existence should queue. ``on_cancel``
# and ``on_no_show`` are NOT among them — each is materialised by the transition that names it, so a
# booking never sits on a queued "your booking was cancelled" message it might accidentally send.
_FORWARD_LOOKING = (
    WorkflowTrigger.ON_BOOKING,
    WorkflowTrigger.BEFORE_START,
    WorkflowTrigger.AFTER_END,
)


def step_action(transition: BookingTransition, trigger: WorkflowTrigger) -> StepAction:
    """What ``transition`` does to the step for ``trigger``.

    Exhaustive over **both** enums: ``assert_never`` turns a new transition, or a new trigger, into
    a pyright error rather than a silent default. A silent default here is not a style question — it
    is the follow-up email that goes out for a meeting that never happened."""
    match transition:
        case BookingTransition.CONFIRM | BookingTransition.RESCHEDULE_SUCCESSOR:
            # A booking is starting its life (a fresh one, or a reschedule's successor). Queue what
            # is anchored in its future. Nothing to void: the row is new, so it has no steps yet.
            return StepAction.MATERIALISE if trigger in _FORWARD_LOOKING else StepAction.LEAVE

        case BookingTransition.RESCHEDULE_PREDECESSOR:
            # This booking has been REPLACED. Every step still queued for it is now about a meeting
            # that will not happen — including the EXEMPT ones, which is the whole trap:
            # ``after_end`` is exempt from the staleness guard, so leaving it pending means it
            # really is delivered.
            return StepAction.VOID

        case BookingTransition.CANCEL:
            # Tell the guest it is cancelled, and retire everything else that is still waiting.
            return (
                StepAction.MATERIALISE if trigger is WorkflowTrigger.ON_CANCEL else StepAction.VOID
            )

        case BookingTransition.NO_SHOW:
            # The appointment happened and the guest did not come. Send the no-show step, and kill
            # the follow-up: "thanks for meeting with us" to somebody who never showed up is worse
            # than saying nothing at all. ``on_booking`` / ``before_start`` have already gone out
            # (their moment has passed) and ``on_cancel`` is not queued — neither is this one's
            # business.
            match trigger:
                case WorkflowTrigger.ON_NO_SHOW:
                    return StepAction.MATERIALISE
                case WorkflowTrigger.AFTER_END:
                    return StepAction.VOID
                case (
                    WorkflowTrigger.ON_BOOKING
                    | WorkflowTrigger.BEFORE_START
                    | WorkflowTrigger.ON_CANCEL
                ):
                    return StepAction.LEAVE
                case _ as unreachable_trigger:
                    assert_never(unreachable_trigger)

        case _ as unreachable:
            assert_never(unreachable)


def triggers_to_materialise(transition: BookingTransition) -> tuple[WorkflowTrigger, ...]:
    """The triggers whose steps ``transition`` enqueues."""
    return tuple(
        trigger
        for trigger in WorkflowTrigger
        if step_action(transition, trigger) is StepAction.MATERIALISE
    )


def triggers_to_void(transition: BookingTransition) -> tuple[WorkflowTrigger, ...]:
    """The triggers whose still-pending steps ``transition`` retires."""
    return tuple(
        trigger
        for trigger in WorkflowTrigger
        if step_action(transition, trigger) is StepAction.VOID
    )


# --------------------------------------------------------------------------------------
# The step PRODUCER. The engine that renders bodies and drives the channels lands in a later cut;
# what has to exist NOW is the thing that puts a booking's steps into the outbox, inside the
# booking's OWN transaction. Without it, retiring the APScheduler reminder would leave every NEW
# booking with no reminder at all — a real regression.
# --------------------------------------------------------------------------------------

# The default rule every tenant gets: remind the guest 24 h before the start. It is DATA, not code
# (a row the tenant can edit or switch off), and it is seeded in exactly TWO places — migration 0005
# for the tenants that already existed, and :func:`seed_default_workflows` for every tenant created
# afterwards. Miss the second and a brand-new tenant silently has no reminders at all.
DEFAULT_REMINDER_NAME = "24h reminder"
DEFAULT_REMINDER_OFFSET_MINUTES = -1440
DEFAULT_REMINDER_KIND = "reminder"

DEFAULT_LOCALE = "es"
"""Spanish is the primary locale (RNF-1). Used when nothing else knows what the guest booked in."""


def notify_payload(  # noqa: PLR0913 - the payload IS the contract; each field is load-bearing
    *,
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    trigger: WorkflowTrigger,
    channel: Channel,
    kind: str,
    locale: str,
) -> dict[str, object]:
    """The NOTIFY intent's payload — built in ONE place, read by the drain.

    Every field is load-bearing downstream: ``trigger`` decides the step's staleness policy,
    ``workflow_id`` is what lets the drain check the rule is still switched on, ``step_id`` +
    ``channel`` are the ledger identity that makes the message exactly-once, and ``kind`` +
    ``locale`` choose the body. Two hand-rolled copies of this dict is how a field quietly goes
    missing from one producer's rows and the drain starts skipping them."""
    return {
        "trigger": trigger.value,
        "workflow_id": str(workflow_id),
        "step_id": str(step_id),
        "channel": channel.value,
        "kind": kind,
        "locale": locale,
    }


async def seed_default_workflows(session: AsyncSession, *, tenant_id: uuid.UUID) -> Workflow | None:
    """Give a new tenant the default 24 h reminder rule. ``None`` if it already has one.

    Idempotent on the workflow's ``(tenant_id, name)`` unique constraint, so calling it twice is a
    no-op rather than a second rule — which would remind the guest twice."""
    existing = (
        await session.scalars(
            select(Workflow).where(
                Workflow.tenant_id == tenant_id, Workflow.name == DEFAULT_REMINDER_NAME
            )
        )
    ).first()
    if existing is not None:
        return None

    workflow = Workflow(
        tenant_id=tenant_id,
        event_type_id=None,  # applies to every event type of the tenant
        name=DEFAULT_REMINDER_NAME,
        trigger=WorkflowTrigger.BEFORE_START.value,
        offset_minutes=DEFAULT_REMINDER_OFFSET_MINUTES,
        active=True,
    )
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowStep(
            tenant_id=tenant_id,
            workflow_id=workflow.id,
            channel=Channel.EMAIL.value,
            kind=DEFAULT_REMINDER_KIND,
            position=0,
        )
    )
    await session.flush()
    return workflow


def step_send_time(
    trigger: WorkflowTrigger, *, booking: Booking, offset: timedelta
) -> datetime | None:
    """When a step for ``trigger`` is due. ``None`` = as soon as the next drain runs.

    The anchor is the trigger's own moment, and ``offset_minutes`` is SIGNED — ``before_start``
    carries ``-1440``, so the addition just works. The event-shaped triggers (``on_booking``,
    ``on_cancel``, ``on_no_show``) fire now: they report something that has just happened, so the
    offset is IGNORED for them — which is exactly why the rule CRUD refuses to store one (a tenant
    would otherwise schedule "2 h after the cancellation" and watch it go out immediately).

    Public because it is the ONE definition of a step's send time: the materialiser
    (:func:`apply_booking_transition`) and the rule CRUD's reconcile both read it. Two copies of
    this arithmetic is how the queue and the rule come to disagree about when a reminder goes
    out.
    """
    match trigger:
        case WorkflowTrigger.BEFORE_START:
            return as_utc(booking.start_at) + offset
        case WorkflowTrigger.AFTER_END:
            return as_utc(booking.end_at) + offset
        case WorkflowTrigger.ON_BOOKING | WorkflowTrigger.ON_CANCEL | WorkflowTrigger.ON_NO_SHOW:
            return None
        case _ as unreachable:
            assert_never(unreachable)


async def _active_workflows(
    session: AsyncSession, *, booking: Booking, triggers: Collection[WorkflowTrigger]
) -> Sequence[Workflow]:
    """The tenant's active rules for ``triggers`` that apply to this booking's event type."""
    if not triggers:
        return []
    return (
        await session.scalars(
            select(Workflow).where(
                Workflow.tenant_id == booking.tenant_id,
                Workflow.active.is_(True),
                Workflow.trigger.in_([trigger.value for trigger in triggers]),
                # NULL = the rule applies to every event type of the tenant.
                or_(
                    Workflow.event_type_id.is_(None),
                    Workflow.event_type_id == booking.event_type_id,
                ),
            )
        )
    ).all()


async def apply_booking_transition(
    session: AsyncSession,
    *,
    booking: Booking,
    transition: BookingTransition,
    now: datetime,
    locale: str = DEFAULT_LOCALE,
) -> list[uuid.UUID]:
    """Bring a booking's queued steps in line with ``transition``. Returns the ids materialised.

    Runs INSIDE the booking's own transaction — exactly like the webhook and the email intent — so a
    step can never be queued for a booking whose transaction later rolls back, and a booking can
    never commit without its steps.

    Voids first, then materialises, both read straight off the transition table. Never an ad-hoc
    ``if``: the table is the only place that decides what a transition does to a step."""
    voided = await void_pending_steps(
        session,
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        triggers=triggers_to_void(transition),
    )
    if voided:
        _logger.info(
            "booking %s (%s): voided %d pending workflow step(s)",
            booking.id,
            transition.value,
            len(voided),
        )

    materialised: list[uuid.UUID] = []
    wanted = triggers_to_materialise(transition)
    for workflow in await _active_workflows(session, booking=booking, triggers=wanted):
        trigger = WorkflowTrigger(workflow.trigger)
        send_at = step_send_time(
            trigger, booking=booking, offset=timedelta(minutes=workflow.offset_minutes)
        )
        if send_at is not None and send_at <= now:
            # Its moment has already passed — booked inside the reminder window, or rescheduled into
            # it. A "reminder" that lands after the fact is noise, and a `next_retry_at` in the past
            # drains IMMEDIATELY, so it has to be skipped rather than queued. Recorded, because a
            # message that is silently absent is exactly the kind of thing nobody ever notices.
            _logger.info(
                "booking %s: skipping workflow %s (%s) — its send time %s is already past",
                booking.id,
                workflow.id,
                trigger.value,
                send_at.isoformat(),
            )
            continue

        steps = (
            await session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow.id)
                .order_by(WorkflowStep.position)
            )
        ).all()
        for step in steps:
            channel = Channel(step.channel)
            row = await enqueue_effect(
                session,
                tenant_id=booking.tenant_id,
                booking_id=booking.id,
                effect=OutboxEffect.NOTIFY,
                dedupe_key=workflow_step_dedupe_key(workflow.id, step.id, channel),
                payload=notify_payload(
                    workflow_id=workflow.id,
                    step_id=step.id,
                    trigger=trigger,
                    channel=channel,
                    kind=step.kind,
                    locale=locale,
                ),
                next_retry_at=send_at,
            )
            if row is not None:
                materialised.append(row.id)
    return materialised
