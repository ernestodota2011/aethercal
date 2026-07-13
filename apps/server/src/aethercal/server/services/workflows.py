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

from enum import StrEnum
from typing import assert_never

from aethercal.server.db.models.workflows import WorkflowTrigger

# ``WorkflowTrigger`` is re-exported so the shared contract's import path holds. It is DEFINED in
# the leaf model module: the outbox classifies staleness by trigger, and the engine will import the
# outbox to enqueue — declaring the vocabulary here would close that into an import cycle.
__all__ = [
    "BookingTransition",
    "StepAction",
    "WorkflowTrigger",
    "step_action",
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
