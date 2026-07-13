"""The rule CRUD (RF-24): what a tenant may author, and what their edit actually DOES.

Until now a tenant had exactly one workflow — the seeded 24 h reminder — and no way to change it.
This is the layer that lets them author their own, and it is written against one rule: **every
outcome is asserted on the EFFECTIVE state**, never the apparent one. A rule row that exists but
queues nothing, an ``active = false`` that stops nothing, an edited offset that leaves the reminder
firing at the old hour — each of those "passes" a test that only reads back the row it just wrote.

So the tests below check the OUTBOX, which is where a rule either becomes a message or does not:

* a rule with no steps, a phone step with no template, an offset on a trigger that ignores offsets —
  all **refused, loudly**, because each of them is a rule that silently sends nothing (or sends the
  wrong thing) and reports no error;
* creating, editing, activating and deactivating a rule **reconcile the queued steps of the bookings
  it governs** — the reminder moves when the rule moves, or the edit was a lie.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.schemas.workflows import (
    CHANNEL_NAMES,
    TEMPLATE_VARIABLES,
    WORKFLOW_TRIGGER_NAMES,
    WorkflowCreate,
    WorkflowStepIn,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
)
from aethercal.server.channels import Channel
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Schedule,
    Tenant,
    User,
    WorkflowStep,
)
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxSkipped,
    OutboxWork,
    run_notify_effect,
    workflow_step_dedupe_key,
)
from aethercal.server.services.workflow_rules import (
    DuplicateNameError,
    DuplicateTemplateError,
    InvalidReferenceError,
    InvalidRuleError,
    TemplateInUseError,
    create_template,
    create_workflow,
    delete_template,
    get_workflow,
    list_templates,
    list_workflows,
    set_workflow_active,
    update_template,
    update_workflow,
)
from aethercal.server.services.workflows import BookingTransition, apply_booking_transition

_NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
_FAR = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)  # two weeks out: outside every window used here
_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


# --------------------------------------------------------------------------------------
# Fixtures — a tenant with an event type, and a helper that books it.
# --------------------------------------------------------------------------------------


@pytest.fixture
def reminder() -> WorkflowCreate:
    """The shape of the rule every tenant already has: e-mail the guest 24 h before the start."""
    return WorkflowCreate(
        name="24h reminder",
        trigger="before_start",
        offset_minutes=-1440,
        steps=[WorkflowStepIn(channel="email", kind="reminder")],
    )


async def _event_type(session: AsyncSession, tenant: Tenant, *, slug: str = "intro") -> EventType:
    schedule = Schedule(
        tenant_id=tenant.id, name=f"sched-{slug}", timezone="UTC", rules=_ALWAYS_OPEN
    )
    session.add(schedule)
    await session.flush()
    host_id = (await session.scalars(select(User.id).where(User.tenant_id == tenant.id))).first()
    assert host_id is not None
    return await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host_id,
            schedule_id=schedule.id,
            slug=slug,
            title="Intro Call",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 60,
        ),
    )


async def _book(
    session: AsyncSession,
    tenant: Tenant,
    event_type: EventType,
    *,
    start: datetime = _FAR,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
    """A booking row, written straight to the table: the slots engine is not what is under test."""
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=status,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
        answers={},
    )
    session.add(booking)
    await session.flush()
    return booking


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the round-trip, so a stored instant reads back naive. Compare
    INSTANTS, not tzinfo — the column is ``DateTime(timezone=True)`` and PostgreSQL keeps it."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _steps_of(session: AsyncSession, booking: Booking) -> Sequence[Outbox]:
    """Every NOTIFY row queued for ``booking`` — the effective state of its queue."""
    return (
        await session.scalars(
            select(Outbox)
            .where(
                Outbox.booking_id == booking.id,
                Outbox.effect == OutboxEffect.NOTIFY.value,
            )
            .order_by(Outbox.created_at, Outbox.id)
        )
    ).all()


async def _live_steps_of(session: AsyncSession, booking: Booking) -> list[Outbox]:
    """The rows that will actually still SEND: everything not voided/delivered/skipped/dead."""
    return [row for row in await _steps_of(session, booking) if row.status in {"pending", "failed"}]


# --------------------------------------------------------------------------------------
# What a tenant may author. Each of these is refused because ACCEPTING it produces a rule that
# silently does nothing (or the wrong thing) and reports no error.
# --------------------------------------------------------------------------------------


def test_a_rule_with_no_steps_is_refused_at_the_edge() -> None:
    """A rule with zero steps fires nothing, for ever, and raises nothing. It is not a rule."""
    with pytest.raises(ValueError, match="at least 1 item"):
        WorkflowCreate(name="empty", trigger="before_start", offset_minutes=-60, steps=[])


def test_an_offset_on_an_event_shaped_trigger_is_refused() -> None:
    """``on_booking``/``on_cancel``/``on_no_show`` fire the moment their event happens — the send
    time IGNORES ``offset_minutes`` entirely (``services/workflows.py`` ``_send_time`` returns
    ``None`` for them). Storing "2 h after the cancellation" would therefore be a message the tenant
    believes they scheduled and that in fact goes out immediately. Refuse it instead of lying."""
    with pytest.raises(ValueError, match=r"(?i)ignores"):
        WorkflowCreate(
            name="late cancel notice",
            trigger="on_cancel",
            offset_minutes=120,
            steps=[WorkflowStepIn(channel="email", kind="cancellation")],
        )


def test_before_start_refuses_a_positive_offset_and_after_end_a_negative_one() -> None:
    """The sign IS the direction. A ``before_start`` with ``+1440`` reminds the guest a day AFTER
    the meeting started, which is not what the tenant asked for and not what the name says."""
    with pytest.raises(ValueError, match="before_start"):
        WorkflowCreate(
            name="bad",
            trigger="before_start",
            offset_minutes=60,
            steps=[WorkflowStepIn(channel="email", kind="reminder")],
        )
    with pytest.raises(ValueError, match="after_end"):
        WorkflowCreate(
            name="bad",
            trigger="after_end",
            offset_minutes=-60,
            steps=[WorkflowStepIn(channel="email", kind="reminder")],
        )


def test_two_steps_on_the_same_channel_are_refused() -> None:
    """Two email steps in one rule are two identical emails: each step has its own id, so each gets
    its own dedupe key, and the outbox's exactly-once guarantee holds for BOTH of them."""
    with pytest.raises(ValueError, match="one step per channel"):
        WorkflowCreate(
            name="double",
            trigger="before_start",
            offset_minutes=-60,
            steps=[
                WorkflowStepIn(channel="email", kind="reminder", position=0),
                WorkflowStepIn(channel="email", kind="reminder", position=1),
            ],
        )


def test_two_steps_at_the_same_position_are_refused() -> None:
    with pytest.raises(ValueError, match="position"):
        WorkflowCreate(
            name="collision",
            trigger="before_start",
            offset_minutes=-60,
            steps=[
                WorkflowStepIn(channel="email", kind="reminder", position=0),
                WorkflowStepIn(channel="whatsapp", kind="reminder", position=0),
            ],
        )


async def test_a_phone_step_without_a_template_is_refused(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """A WhatsApp step has no built-in composer: its body comes from a ``workflow_templates`` row.
    With no template the step reaches the drain, is SKIPPED with a reason, and the guest never hears
    from it — a rule that looks armed and is not. Refuse it at authoring time."""
    with pytest.raises(InvalidRuleError, match="no template"):
        await create_workflow(
            sqlite_session,
            tenant_id=tenant.id,
            now=_NOW,
            data=WorkflowCreate(
                name="wa reminder",
                trigger="before_start",
                offset_minutes=-60,
                steps=[WorkflowStepIn(channel="whatsapp", kind="reminder")],
            ),
        )


async def test_a_phone_step_WITH_a_template_is_accepted(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )
    row = await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=WorkflowCreate(
            name="wa reminder",
            trigger="before_start",
            offset_minutes=-60,
            steps=[WorkflowStepIn(channel="whatsapp", kind="reminder")],
        ),
    )
    assert row.workflow.trigger == WorkflowTrigger.BEFORE_START.value


async def test_an_email_step_with_a_custom_kind_needs_a_template_too(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """``reminder``/``confirmation``/``cancellation``/``reschedule`` have built-in composers (they
    carry the ``.ics``). Anything else — a follow-up, say — has a body only if the tenant wrote
    one."""
    with pytest.raises(InvalidRuleError, match="no template"):
        await create_workflow(
            sqlite_session,
            tenant_id=tenant.id,
            now=_NOW,
            data=WorkflowCreate(
                name="follow up",
                trigger="after_end",
                offset_minutes=60,
                steps=[WorkflowStepIn(channel="email", kind="follow_up")],
            ),
        )


async def test_a_rule_cannot_be_scoped_to_another_tenants_event_type(
    sqlite_session: AsyncSession,
    tenant: Tenant,
    tenant_factory: object,
    reminder: WorkflowCreate,
) -> None:
    """Otherwise tenant A authors a rule that fires on tenant B's bookings."""
    other = await tenant_factory(sqlite_session, slug="other")  # type: ignore[operator]
    theirs = await _event_type(sqlite_session, other, slug="theirs")
    with pytest.raises(InvalidReferenceError):
        await create_workflow(
            sqlite_session,
            tenant_id=tenant.id,
            now=_NOW,
            data=reminder.model_copy(update={"event_type_id": theirs.id}),
        )


async def test_the_name_is_unique_per_tenant(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    with pytest.raises(DuplicateNameError):
        await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)


# --------------------------------------------------------------------------------------
# The CRUD itself.
# --------------------------------------------------------------------------------------


async def test_create_then_read_back_the_rule_and_its_steps(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)

    fetched = await get_workflow(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id
    )
    assert fetched is not None
    assert fetched.workflow.name == "24h reminder"
    assert fetched.workflow.offset_minutes == -1440
    assert [(step.channel, step.kind) for step in fetched.steps] == [("email", "reminder")]

    listed = await list_workflows(sqlite_session, tenant_id=tenant.id)
    assert [rule.workflow.id for rule in listed] == [created.workflow.id]


async def test_another_tenants_rule_is_invisible(
    sqlite_session: AsyncSession,
    tenant: Tenant,
    tenant_factory: object,
    reminder: WorkflowCreate,
) -> None:
    other = await tenant_factory(sqlite_session, slug="other")  # type: ignore[operator]
    theirs = await create_workflow(sqlite_session, tenant_id=other.id, now=_NOW, data=reminder)

    assert (
        await get_workflow(sqlite_session, tenant_id=tenant.id, workflow_id=theirs.workflow.id)
        is None
    )
    assert await list_workflows(sqlite_session, tenant_id=tenant.id) == []
    assert (
        await update_workflow(
            sqlite_session,
            tenant_id=tenant.id,
            workflow_id=theirs.workflow.id,
            now=_NOW,
            data=WorkflowUpdate(active=False),
        )
        is None
    )


async def test_editing_the_steps_replaces_them_wholesale(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )

    updated = await update_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        workflow_id=created.workflow.id,
        now=_NOW,
        data=WorkflowUpdate(
            steps=[
                WorkflowStepIn(channel="email", kind="reminder", position=0),
                WorkflowStepIn(channel="whatsapp", kind="reminder", position=1),
            ]
        ),
    )
    assert updated is not None
    assert {(step.channel, step.kind) for step in updated.steps} == {
        ("email", "reminder"),
        ("whatsapp", "reminder"),
    }
    # The rows really are replaced in the table, not merely in the relationship.
    rows = (
        await sqlite_session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == created.workflow.id)
        )
    ).all()
    assert len(rows) == 2


async def test_an_edit_that_changes_only_the_trigger_is_validated_against_the_MERGED_state(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """The payload alone looks innocent — ``{"trigger": "on_cancel"}`` carries no offset at all. It
    is the RESULT that is incoherent: the stored ``-1440`` would be silently ignored for ever. So
    the check runs on the merged rule, not on the fields the caller happened to send."""
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)

    with pytest.raises(InvalidRuleError, match=r"(?i)ignores"):
        await update_workflow(
            sqlite_session,
            tenant_id=tenant.id,
            workflow_id=created.workflow.id,
            now=_NOW,
            data=WorkflowUpdate(trigger="on_cancel"),
        )


# --------------------------------------------------------------------------------------
# The effective state. A rule is not what its row says — it is what the queue does.
# --------------------------------------------------------------------------------------


async def test_creating_a_rule_queues_it_for_the_bookings_it_already_governs(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """A tenant who authors a "remind 24 h before" rule today means it for the meeting they already
    have next week. Materialising only for FUTURE bookings would leave that one silently unreminded
    — the rule row exists, and nothing happens."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)

    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)

    (row,) = await _live_steps_of(sqlite_session, booking)
    step = created.steps[0]
    assert row.dedupe_key == workflow_step_dedupe_key(created.workflow.id, step.id, Channel.EMAIL)
    assert _utc(row.next_retry_at) == _FAR - timedelta(minutes=1440)


async def test_a_rule_scoped_to_one_event_type_does_not_touch_the_others(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    mine = await _event_type(sqlite_session, tenant, slug="mine")
    theirs = await _event_type(sqlite_session, tenant, slug="theirs")
    booked_mine = await _book(sqlite_session, tenant, mine)
    booked_theirs = await _book(sqlite_session, tenant, theirs)

    await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=reminder.model_copy(update={"event_type_id": mine.id}),
    )

    assert len(await _live_steps_of(sqlite_session, booked_mine)) == 1
    assert await _live_steps_of(sqlite_session, booked_theirs) == []


async def test_moving_the_offset_MOVES_the_already_queued_reminder(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """THE test of this cut. A tenant changes "24 h before" to "2 h before"; every booking already
    on the books still has a row queued at ``start - 24h``. Leave it and the edit changed nothing a
    guest can perceive — the QUEUE, not the rule row, is what sends."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    (before,) = await _live_steps_of(sqlite_session, booking)
    assert _utc(before.next_retry_at) == _FAR - timedelta(minutes=1440)

    await update_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        workflow_id=created.workflow.id,
        now=_NOW,
        data=WorkflowUpdate(offset_minutes=-120),
    )

    (after,) = await _live_steps_of(sqlite_session, booking)
    assert after.id == before.id  # the SAME row, re-timed — not a second reminder
    assert _utc(after.next_retry_at) == _FAR - timedelta(minutes=120)


async def test_adding_a_channel_queues_it_for_the_bookings_already_on_the_books(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )

    updated = await update_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        workflow_id=created.workflow.id,
        now=_NOW,
        data=WorkflowUpdate(
            steps=[
                WorkflowStepIn(channel="email", kind="reminder", position=0),
                WorkflowStepIn(channel="whatsapp", kind="reminder", position=1),
            ]
        ),
    )
    assert updated is not None

    live = await _live_steps_of(sqlite_session, booking)
    assert {row.payload["channel"] for row in live} == {"email", "whatsapp"}


async def test_removing_a_step_retires_the_row_it_had_already_queued(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """Drop the WhatsApp step and the WhatsApp message must not still arrive next Tuesday."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)
    await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )
    created = await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=WorkflowCreate(
            name="reminders",
            trigger="before_start",
            offset_minutes=-1440,
            steps=[
                WorkflowStepIn(channel="email", kind="reminder", position=0),
                WorkflowStepIn(channel="whatsapp", kind="reminder", position=1),
            ],
        ),
    )
    assert len(await _live_steps_of(sqlite_session, booking)) == 2

    await update_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        workflow_id=created.workflow.id,
        now=_NOW,
        data=WorkflowUpdate(steps=[WorkflowStepIn(channel="email", kind="reminder", position=0)]),
    )

    live = await _live_steps_of(sqlite_session, booking)
    assert [row.payload["channel"] for row in live] == ["email"]
    voided = [row for row in await _steps_of(sqlite_session, booking) if row.status == "voided"]
    assert [row.payload["channel"] for row in voided] == ["whatsapp"]


async def test_an_edit_that_pushes_the_send_time_into_the_past_voids_it_rather_than_sending_late(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """The same rule the materialiser already obeys: a reminder whose moment has gone is noise, and
    a ``next_retry_at`` in the past drains IMMEDIATELY. Re-timing it there would fire it at once."""
    event_type = await _event_type(sqlite_session, tenant)
    soon = _NOW + timedelta(hours=3)
    booking = await _book(sqlite_session, tenant, event_type, start=soon)
    created = await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=reminder.model_copy(update={"offset_minutes": -60}),
    )
    assert len(await _live_steps_of(sqlite_session, booking)) == 1

    # 24 h before a meeting that is 3 h away is yesterday.
    await update_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        workflow_id=created.workflow.id,
        now=_NOW,
        data=WorkflowUpdate(offset_minutes=-1440),
    )

    assert await _live_steps_of(sqlite_session, booking) == []
    assert [row.status for row in await _steps_of(sqlite_session, booking)] == ["voided"]


async def test_a_cancelled_booking_is_never_re_armed_by_an_edit(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """The reconcile walks the tenant's LIVE bookings. Widen it to every row and editing a rule
    would queue a reminder for a meeting that was called off."""
    event_type = await _event_type(sqlite_session, tenant)
    cancelled = await _book(sqlite_session, tenant, event_type, status=BookingStatus.CANCELLED)

    await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)

    assert await _steps_of(sqlite_session, cancelled) == []


async def test_deactivating_a_rule_stops_it_from_arming_new_bookings(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    event_type = await _event_type(sqlite_session, tenant)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)

    off = await set_workflow_active(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id, active=False, now=_NOW
    )
    assert off is not None and off.workflow.active is False

    later = await _book(sqlite_session, tenant, event_type)
    await apply_booking_transition(
        sqlite_session, booking=later, transition=BookingTransition.CONFIRM, now=_NOW
    )
    assert await _steps_of(sqlite_session, later) == []


async def test_reactivating_a_rule_re_arms_the_bookings_made_while_it_was_off(
    sqlite_session: AsyncSession, tenant: Tenant, reminder: WorkflowCreate
) -> None:
    """A booking taken while the rule was off has NO queued step at all. Switch the rule back on and
    "active" has to mean armed — otherwise the tenant sees ``active: true`` and that week's guests
    are never reminded, in silence."""
    event_type = await _event_type(sqlite_session, tenant)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    await set_workflow_active(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id, active=False, now=_NOW
    )
    booking = await _book(sqlite_session, tenant, event_type)
    assert await _steps_of(sqlite_session, booking) == []

    await set_workflow_active(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id, active=True, now=_NOW
    )

    (row,) = await _live_steps_of(sqlite_session, booking)
    assert _utc(row.next_retry_at) == _FAR - timedelta(minutes=1440)


async def test_an_event_shaped_step_is_never_back_filled_onto_an_existing_booking(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """``on_booking`` reports something that has ALREADY happened. Authoring the rule today must not
    send "your booking is confirmed" to somebody who booked last week."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)

    await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=WorkflowCreate(
            name="confirm",
            trigger="on_booking",
            offset_minutes=0,
            steps=[WorkflowStepIn(channel="email", kind="confirmation")],
        ),
    )

    assert await _steps_of(sqlite_session, booking) == []


# --------------------------------------------------------------------------------------
# Templates.
# --------------------------------------------------------------------------------------


def test_a_template_may_only_use_the_allowlisted_variables() -> None:
    """The body is DATA, never instructions. An unknown ``{{...}}`` renders as garbage in a real
    guest's message — and an expression tag would be an invitation to evaluate it."""
    assert "guest_name" in TEMPLATE_VARIABLES
    with pytest.raises(ValueError, match="unknown template variable"):
        WorkflowTemplateCreate(
            channel="sms", kind="reminder", locale="es", body="Hi {{guest_password}}"
        )
    with pytest.raises(ValueError, match="expression"):
        WorkflowTemplateCreate(
            channel="sms", kind="reminder", locale="es", body="{% for x in y %}{% endfor %}"
        )


def test_a_phone_template_may_not_carry_a_subject_and_an_email_one_must() -> None:
    """WhatsApp and SMS have no subject. Accepting one would store a field nobody ever reads."""
    with pytest.raises(ValueError, match="subject"):
        WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", subject="Hi", body="Hola"
        )
    with pytest.raises(ValueError, match="subject"):
        WorkflowTemplateCreate(channel="email", kind="follow_up", locale="es", body="Hola")


async def test_template_crud_round_trip(sqlite_session: AsyncSession, tenant: Tenant) -> None:
    created = await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="sms", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )
    assert [row.id for row in await list_templates(sqlite_session, tenant_id=tenant.id)] == [
        created.id
    ]

    updated = await update_template(
        sqlite_session,
        tenant_id=tenant.id,
        template_id=created.id,
        data=WorkflowTemplateUpdate(body="Hola {{guest_name}}, te esperamos a las {{start_local}}"),
    )
    assert updated is not None and "start_local" in updated.body

    assert (
        await delete_template(sqlite_session, tenant_id=tenant.id, template_id=created.id) is True
    )
    assert await list_templates(sqlite_session, tenant_id=tenant.id) == []


async def test_a_template_identity_is_unique_per_tenant(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    data = WorkflowTemplateCreate(channel="sms", kind="reminder", locale="es", body="Hola")
    await create_template(sqlite_session, tenant_id=tenant.id, data=data)
    with pytest.raises(DuplicateTemplateError):
        await create_template(sqlite_session, tenant_id=tenant.id, data=data)


async def test_deleting_the_last_template_a_step_depends_on_is_refused(
    sqlite_session: AsyncSession, tenant: Tenant
) -> None:
    """Delete it and the step it fed stops rendering — silently skipped at the drain, for ever. The
    rule still says ``active: true``. Refuse the delete, and name the rule that holds it."""
    template = await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="Hola {{guest_name}}"
        ),
    )
    await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        now=_NOW,
        data=WorkflowCreate(
            name="wa reminder",
            trigger="before_start",
            offset_minutes=-60,
            steps=[WorkflowStepIn(channel="whatsapp", kind="reminder")],
        ),
    )

    with pytest.raises(TemplateInUseError, match="wa reminder"):
        await delete_template(sqlite_session, tenant_id=tenant.id, template_id=template.id)

    # A SECOND locale for the same (channel, kind) makes the first one droppable again.
    await create_template(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="en", body="Hi {{guest_name}}"
        ),
    )
    assert (
        await delete_template(sqlite_session, tenant_id=tenant.id, template_id=template.id) is True
    )


async def test_another_tenants_template_cannot_be_read_or_deleted(
    sqlite_session: AsyncSession,
    tenant: Tenant,
    tenant_factory: object,
) -> None:
    other = await tenant_factory(sqlite_session, slug="other")  # type: ignore[operator]
    theirs = await create_template(
        sqlite_session,
        tenant_id=other.id,
        data=WorkflowTemplateCreate(channel="sms", kind="reminder", locale="es", body="Hola"),
    )

    assert await list_templates(sqlite_session, tenant_id=tenant.id) == []
    assert (
        await delete_template(sqlite_session, tenant_id=tenant.id, template_id=theirs.id) is False
    )
    assert (
        await update_template(
            sqlite_session,
            tenant_id=tenant.id,
            template_id=theirs.id,
            data=WorkflowTemplateUpdate(body="hacked"),
        )
        is None
    )


# --------------------------------------------------------------------------------------
# "Off" has to mean off AT THE SEND — the only moment anybody outside can observe.
# --------------------------------------------------------------------------------------


class _RecordingSender:
    """Stands in for the SMTP sender: records what would have reached a real guest."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def _work_for(row: Outbox) -> OutboxWork:
    return OutboxWork(
        id=row.id,
        tenant_id=row.tenant_id,
        booking_id=row.booking_id,
        effect=OutboxEffect(row.effect),
        dedupe_key=row.dedupe_key,
        payload=dict(row.payload),
        attempts=0,
        claimed_by="test-worker",
    )


async def test_a_step_queued_by_a_rule_that_is_now_OFF_is_not_delivered(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    reminder: WorkflowCreate,
) -> None:
    """The step was queued days ago, when the rule was on. Switching the rule off this afternoon has
    to stop TOMORROW's message — and the only place that can be enforced is the send itself, because
    the row is already sitting in the outbox with its send time intact.

    Deliberately NOT done by voiding the row: that is terminal, its dedupe key would stay occupied
    for ever, and switching the rule back on could never restore the reminder. So the row survives,
    inert, and the drain asks the rule."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    (row,) = await _live_steps_of(sqlite_session, booking)
    work = await _work_for(row)

    await set_workflow_active(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id, active=False, now=_NOW
    )
    await sqlite_session.commit()

    sender = _RecordingSender()
    with pytest.raises(OutboxSkipped, match="workflow-inactive"):
        await run_notify_effect(sqlite_maker, work, _NOW, sender=sender, channels={})
    assert sender.sent == []  # nothing reached the guest

    # Switch it back on and the very same row sends: the toggle is symmetric.
    await set_workflow_active(
        sqlite_session, tenant_id=tenant.id, workflow_id=created.workflow.id, active=True, now=_NOW
    )
    await sqlite_session.commit()

    await run_notify_effect(sqlite_maker, work, _NOW, sender=sender, channels={})
    assert len(sender.sent) == 1
    assert "ada@example.com" in str(sender.sent[0]["To"])


async def test_a_step_whose_rule_has_VANISHED_is_not_delivered(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    reminder: WorkflowCreate,
) -> None:
    """An event type deleted with ``ON DELETE CASCADE`` takes its workflows with it. The steps it
    had already queued would otherwise still be delivered — messages from a rule that exists
    nowhere."""
    event_type = await _event_type(sqlite_session, tenant)
    booking = await _book(sqlite_session, tenant, event_type)
    created = await create_workflow(sqlite_session, tenant_id=tenant.id, now=_NOW, data=reminder)
    (row,) = await _live_steps_of(sqlite_session, booking)
    work = await _work_for(row)

    await sqlite_session.delete(created.workflow)
    await sqlite_session.commit()

    sender = _RecordingSender()
    with pytest.raises(OutboxSkipped, match="workflow-gone"):
        await run_notify_effect(sqlite_maker, work, _NOW, sender=sender, channels={})
    assert sender.sent == []


# --------------------------------------------------------------------------------------
# The anti-drift lock: the API's vocabulary and the engine's enums are the same set.
# --------------------------------------------------------------------------------------


def test_the_api_vocabulary_matches_the_engine_enums() -> None:
    """``aethercal.schemas`` cannot import ``aethercal.server`` (the layering forbids it), so the
    trigger/channel names are re-declared as ``Literal``s there. Re-declared is not duplicated only
    while something asserts they are the SAME set — add a trigger to the engine without adding it
    here and the API would 422 a value the engine happily fires."""
    assert set(WORKFLOW_TRIGGER_NAMES) == {trigger.value for trigger in WorkflowTrigger}
    assert set(CHANNEL_NAMES) == {channel.value for channel in Channel}


def test_the_dedupe_key_is_built_from_uuids() -> None:
    """Guard against a stray string id sneaking into the key a message's uniqueness depends on."""
    key = workflow_step_dedupe_key(uuid.uuid4(), uuid.uuid4(), Channel.EMAIL)
    assert key.startswith("wf:") and key.endswith(":email")
