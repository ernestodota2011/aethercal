"""The workflow step lifecycle (RF-24): the transition table, and the voiding it drives.

Rescheduling does NOT mutate a booking — it opens a new row. So a successor's steps can never
collide
with the predecessor's, and any "move the step with an upsert" design matches ZERO rows, raises
nothing, and passes every test. The lifecycle is therefore an explicit table, and these tests pin
the two cells that are the difference between a correct product and a message sent to a real guest
about a meeting that never happened."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.db.models import Booking, Outbox, Schedule, Tenant, User
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxReport,
    _Outcome,
    _settle,
    claim_one,
    enqueue_effect,
    void_pending_steps,
)
from aethercal.server.services.workflows import (
    BookingTransition,
    StepAction,
    step_action,
    triggers_to_materialise,
    triggers_to_void,
)

_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# The table itself.
# --------------------------------------------------------------------------------------


def test_the_table_is_exhaustive_over_every_transition_and_trigger() -> None:
    """A missing cell must be impossible, not a silent default. (``assert_never`` also makes it a
    pyright error, so this is the runtime half of the same guarantee.)"""
    for transition in BookingTransition:
        for trigger in WorkflowTrigger:
            assert step_action(transition, trigger) in set(StepAction)


def test_confirming_queues_the_forward_looking_steps_and_no_others() -> None:
    """A booking must never sit on a queued "your booking was cancelled" message — that one is
    materialised by the cancellation itself, if and when it actually happens."""
    materialised = triggers_to_materialise(BookingTransition.CONFIRM)

    assert set(materialised) == {
        WorkflowTrigger.ON_BOOKING,
        WorkflowTrigger.BEFORE_START,
        WorkflowTrigger.AFTER_END,
    }
    assert WorkflowTrigger.ON_CANCEL not in materialised
    assert WorkflowTrigger.ON_NO_SHOW not in materialised
    assert triggers_to_void(BookingTransition.CONFIRM) == ()  # a new row has nothing to void


def test_a_reschedule_voids_the_predecessors_steps_including_the_exempt_ones() -> None:
    """The trap. ``after_end`` is EXEMPT from the staleness guard (the appointment is over, so "the
    chain moved on" is not a reason to suppress it). Leave the replaced booking's copy pending and
    it really is delivered — a follow-up for a meeting that never happened. Voiding therefore has to
    cover every trigger, the exempt ones included."""
    voided = triggers_to_void(BookingTransition.RESCHEDULE_PREDECESSOR)

    assert set(voided) == set(WorkflowTrigger)
    assert WorkflowTrigger.AFTER_END in voided
    assert WorkflowTrigger.ON_CANCEL in voided
    # And the successor starts clean — a plain INSERT against a brand-new booking id.
    assert set(triggers_to_materialise(BookingTransition.RESCHEDULE_SUCCESSOR)) == {
        WorkflowTrigger.ON_BOOKING,
        WorkflowTrigger.BEFORE_START,
        WorkflowTrigger.AFTER_END,
    }
    assert triggers_to_void(BookingTransition.RESCHEDULE_SUCCESSOR) == ()


def test_a_reschedule_does_not_notify_the_guest_of_a_cancellation() -> None:
    """The swap marks the predecessor CANCELLED as an implementation detail. Hooking ``on_cancel``
    to the STATUS instead of to the OPERATION would send a cancellation notice on every reschedule —
    and ``on_cancel`` is exempt from the staleness guard, so it would really be delivered."""
    assert (
        step_action(BookingTransition.RESCHEDULE_PREDECESSOR, WorkflowTrigger.ON_CANCEL)
        is StepAction.VOID
    )
    assert (
        step_action(BookingTransition.CANCEL, WorkflowTrigger.ON_CANCEL) is StepAction.MATERIALISE
    )


def test_cancelling_sends_the_cancellation_and_retires_everything_else() -> None:
    assert triggers_to_materialise(BookingTransition.CANCEL) == (WorkflowTrigger.ON_CANCEL,)
    assert set(triggers_to_void(BookingTransition.CANCEL)) == set(WorkflowTrigger) - {
        WorkflowTrigger.ON_CANCEL
    }


def test_a_no_show_kills_the_follow_up() -> None:
    """Otherwise the guest who did NOT show up receives "thanks for meeting with us"."""
    assert triggers_to_materialise(BookingTransition.NO_SHOW) == (WorkflowTrigger.ON_NO_SHOW,)
    assert triggers_to_void(BookingTransition.NO_SHOW) == (WorkflowTrigger.AFTER_END,)


# --------------------------------------------------------------------------------------
# The voiding primitive the table drives.
# --------------------------------------------------------------------------------------


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, Booking]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules={})
    session.add(schedule)
    await session.flush()
    event_type = await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        ),
    )
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_SLOT,
        end_at=_SLOT + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return tenant, booking


async def _queue_step(
    session: AsyncSession, tenant: Tenant, booking: Booking, trigger: WorkflowTrigger
) -> Outbox:
    row = await enqueue_effect(
        session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        effect=OutboxEffect.NOTIFY,
        dedupe_key=f"wf:{uuid.uuid4()}:{uuid.uuid4()}:email",
        payload={"trigger": trigger.value},
        next_retry_at=_SLOT,
    )
    assert row is not None
    return row


async def test_voiding_retires_the_replaced_bookings_pending_after_end_follow_up(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """End-to-end on the primitive: the predecessor's exempt ``after_end`` step really is retired,
    so it can never drain and thank somebody for a meeting that never happened."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    reminder = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.BEFORE_START)
    follow_up = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.AFTER_END)

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=triggers_to_void(BookingTransition.RESCHEDULE_PREDECESSOR),
    )

    assert set(voided) == {reminder.id, follow_up.id}
    assert reminder.status == "voided"
    assert follow_up.status == "voided"
    assert follow_up.next_retry_at is None  # it can never come due again


async def test_a_no_show_voids_the_follow_up_but_leaves_the_rest_alone(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    reminder = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.BEFORE_START)
    follow_up = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.AFTER_END)

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=triggers_to_void(BookingTransition.NO_SHOW),
    )

    assert voided == [follow_up.id]
    assert follow_up.status == "voided"
    assert reminder.status == "pending"  # its moment has already passed; not this one's business


async def test_voiding_never_touches_a_step_that_already_went_out(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A ``delivered`` row is history. Retiring it would rewrite what the guest was actually
    sent."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    delivered = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.ON_BOOKING)
    delivered.status = "delivered"
    await sqlite_session.flush()

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=tuple(WorkflowTrigger),
    )

    assert voided == []
    assert delivered.status == "delivered"


async def test_a_voided_step_is_never_drained(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The point of the whole exercise: a voided step is not due, ever again."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    step = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.AFTER_END)
    await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=(WorkflowTrigger.AFTER_END,),
    )

    due = (
        await sqlite_session.scalars(
            select(Outbox).where(Outbox.status.in_(("pending", "failed")), Outbox.id == step.id)
        )
    ).all()

    assert due == []


@pytest.mark.parametrize("transition", list(BookingTransition))
async def test_voiding_with_an_empty_trigger_set_is_a_no_op(
    sqlite_session: AsyncSession, tenant_factory: Any, transition: BookingTransition
) -> None:
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    step = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.BEFORE_START)

    voided = await void_pending_steps(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, triggers=()
    )

    assert voided == []
    assert step.status == "pending"
    # And the table itself never depends on the database: it is a pure declaration.
    assert set(triggers_to_void(transition)) <= set(WorkflowTrigger)


# --------------------------------------------------------------------------------------
# Voiding must retire every state that can still produce a send — not only `pending`.
# --------------------------------------------------------------------------------------


async def test_a_failed_step_is_voided_by_a_cancellation_and_never_sends(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The bug. A step whose provider was down sits in ``failed`` with a ``next_retry_at`` in the
    future — and it is COMPLETELY ALIVE. Void only the ``pending`` rows and that step retries an
    hour later, messaging a guest about an appointment that no longer exists."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    step = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.BEFORE_START)
    # The provider was down: the step failed and is parked for a retry in the future.
    step.status = "failed"
    step.attempts = 1
    step.next_retry_at = _SLOT + timedelta(hours=1)
    await sqlite_session.flush()

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=triggers_to_void(BookingTransition.CANCEL),
    )

    assert voided == [step.id], "the FAILED step survived the cancellation and will still retry"
    assert step.status == "voided"
    assert step.next_retry_at is None  # it can never come due again


async def test_a_failed_step_of_the_predecessor_is_voided_by_a_reschedule(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """Otherwise the replaced booking's failed steps retry and fire at the OLD time."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    reminder = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.BEFORE_START)
    follow_up = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.AFTER_END)
    for step in (reminder, follow_up):
        step.status = "failed"
        step.next_retry_at = _SLOT + timedelta(hours=1)
    await sqlite_session.flush()

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=triggers_to_void(BookingTransition.RESCHEDULE_PREDECESSOR),
    )

    assert set(voided) == {reminder.id, follow_up.id}
    assert reminder.status == follow_up.status == "voided"


async def test_a_claimed_step_is_voided_and_its_workers_result_is_discarded(
    sqlite_maker: async_sessionmaker[AsyncSession],
    sqlite_session: AsyncSession,
    tenant_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The DECLARED policy for a step that is in flight when the cancellation lands.

    There is no such thing as un-sending. So the policy — written down, not left undefined — is: we
    do NOT try to recall it, we guarantee it is never RETRIED, and we refuse to let its worker write
    the outcome. The row goes terminal (``voided``), and the worker's settle, gated on
    ``claimed_by = <me>``, no longer matches: its result is discarded and the fact is logged.
    """
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    step = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.AFTER_END)
    step_id = step.id
    tenant_id = tenant.id
    booking_id = booking.id
    await sqlite_session.commit()

    # A worker claims it and is now mid-send.
    async with sqlite_maker() as session, session.begin():
        work = await claim_one(
            session,
            intent_id=step_id,
            now=_SLOT,
            worker_id="mid-flight",
            lease=timedelta(minutes=5),
        )
    assert work is not None

    # The booking is cancelled WHILE that send is in the air.
    async with sqlite_maker() as session, session.begin():
        voided = await void_pending_steps(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            triggers=triggers_to_void(BookingTransition.CANCEL),
        )
    assert voided == [step_id]

    # The worker comes back and tries to settle "delivered". It must NOT be able to.
    report = OutboxReport()
    with caplog.at_level("WARNING"):
        await _settle(sqlite_maker, work, now=_SLOT, outcome=_Outcome.DELIVERED, report=report)

    assert report.delivered == [], "the in-flight worker wrote its result onto a voided step"
    assert report.lost == [step_id]

    async with sqlite_maker() as session:
        final = await session.get(Outbox, step_id)
    assert final is not None
    assert final.status == "voided"  # terminal: it will NEVER be retried
    assert final.next_retry_at is None

    # And the reason is stated: the send may already have reached the provider, and that is said out
    # loud rather than pretended away. It is NOT reported as a lease lost to another worker.
    messages = [record.getMessage() for record in caplog.records]
    assert any("VOIDED" in message and "cannot be recalled" in message for message in messages)
    assert not any("LEASE LOST" in message for message in messages)


async def test_a_delivered_step_is_never_voided(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """History is not rewritten: a step that already reached the guest stays delivered."""
    tenant, booking = await _seed(sqlite_session, tenant_factory)
    step = await _queue_step(sqlite_session, tenant, booking, WorkflowTrigger.ON_BOOKING)
    step.status = "delivered"
    await sqlite_session.flush()

    voided = await void_pending_steps(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        triggers=tuple(WorkflowTrigger),
    )

    assert voided == []
    assert step.status == "delivered"
