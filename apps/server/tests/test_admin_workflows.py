"""Offline tests for the admin's workflow + template CRUD (RF-24, Wave 2).

The admin is a live surface, and the rule CRUD is the one screen where "the panel says it saved" and
"the guest's reminder actually moved" can come apart. ``services/workflow_rules`` already reconciles
the queue of every booking a rule governs — the job of these tests is to prove the ADMIN goes
through that path and does not quietly grow its own.

So the assertions are on the EFFECTIVE state — the outbox row that will actually fire, and the
``workflows`` row as the database holds it — never on the value the action handed back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.schemas.schedules import ScheduleCreate, TimeRangeSchema
from aethercal.schemas.workflows import (
    WorkflowCreate,
    WorkflowStepIn,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
)
from aethercal.server.admin.service import (
    AdminActionError,
    EventTypeForm,
    create_event_type_action,
    create_schedule_action,
    create_template_action,
    create_workflow_action,
    delete_template_action,
    list_templates_view,
    list_workflows_view,
    set_workflow_active_action,
    update_template_action,
    update_workflow_action,
)
from aethercal.server.db import Base
from aethercal.server.db.models import Outbox, Tenant, User, Workflow, WorkflowTemplate
from aethercal.server.services.bookings import BookingParams, create_booking

Sessionmaker = async_sessionmaker[AsyncSession]

_WEEKLY_9_TO_5 = {day: [TimeRangeSchema(start="09:00", end="17:00")] for day in range(5)}
_NOW = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)  # Monday 00:00 UTC
_START = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)  # the FOLLOWING Monday, 09:00 — a week out
_MAX_ADVANCE = 60 * 60 * 24 * 30

#: ``before_start`` with -1440 → 24 h before the start. Both send times sit in the FUTURE
#: relative to ``_NOW``, so nothing here is retired for being overdue — the test is about the step
#: MOVING, and an accidentally-in-the-past send time would mask that behind a skip.
_SEND_AT_24H_BEFORE = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
_SEND_AT_1H_BEFORE = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
    """An in-memory aiosqlite sessionmaker with the full schema (offline admin TDD)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(maker: Sessionmaker, *, slug: str = "acme") -> tuple[uuid.UUID, uuid.UUID]:
    """Tenant + host + schedule + event type; returns ``(tenant_id, event_type_id)``."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email=f"{slug}@example.com", name="Host", timezone="UTC")
        session.add(host)
        await session.flush()
        tenant_id, host_id = tenant.id, host.id

    schedule = await create_schedule_action(
        maker,
        tenant_slug=slug,
        data=ScheduleCreate(name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5),
    )
    event_type = await create_event_type_action(
        maker,
        tenant_slug=slug,
        form=EventTypeForm(
            host_id=host_id,  # RF-30: the host is an explicit choice, never the tenant's first user
            slug="intro",
            title="Intro",
            schedule_id=schedule.id,
            duration_seconds=1800,
            max_advance_seconds=_MAX_ADVANCE,
        ),
    )
    return tenant_id, event_type.id


async def _book(
    maker: Sessionmaker, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID
) -> uuid.UUID:
    async with maker() as session, session.begin():
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=_START,
                guest_name="Guest",
                guest_email="guest@example.com",
                guest_timezone="UTC",
            ),
            now=_NOW,
        )
        await session.flush()
        return booking.id


async def _notify_rows(maker: Sessionmaker, booking_id: uuid.UUID) -> list[Outbox]:
    """The booking's queued NOTIFY intents — the rows that will ACTUALLY fire."""
    async with maker() as session:
        return list(
            (
                await session.scalars(
                    select(Outbox).where(Outbox.booking_id == booking_id, Outbox.effect == "notify")
                )
            ).all()
        )


def _as_utc(moment: datetime) -> datetime:
    """SQLite hands timestamps back naive; the admin's contract is that its times are UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _reminder_email() -> WorkflowCreate:
    """A 24-h email reminder. ``reminder`` is a BUILT-IN email kind, so it needs no template."""
    return WorkflowCreate(
        name="24h reminder",
        trigger="before_start",
        offset_minutes=-1440,
        steps=[WorkflowStepIn(channel="email", kind="reminder")],
    )


def _whatsapp_template() -> WorkflowTemplateCreate:
    return WorkflowTemplateCreate(
        channel="whatsapp",
        kind="reminder",
        locale="es",
        body="Hola {{guest_name}}, te esperamos el {{start_local}}.",
    )


def _whatsapp_rule() -> WorkflowCreate:
    return WorkflowCreate(
        name="whatsapp reminder",
        trigger="before_start",
        offset_minutes=-60,
        steps=[WorkflowStepIn(channel="whatsapp", kind="reminder")],
    )


# --------------------------------------------------------------------------------------
# The edit has to be TRUE for the bookings already on the books.
# --------------------------------------------------------------------------------------


async def test_a_rule_authored_in_the_admin_arms_the_bookings_that_already_exist(
    sessionmaker: Sessionmaker,
) -> None:
    """A rule written today means the meeting already in next week's diary — not only the next one.

    Asserted on the OUTBOX row, never on the returned model: a rule that saves and queues nothing is
    exactly the failure this panel exists to avoid, and from the return value it looks identical to
    one that worked.
    """
    tenant_id, event_type_id = await _seed(sessionmaker)
    booking_id = await _book(sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id)

    await create_workflow_action(sessionmaker, tenant_slug="acme", data=_reminder_email(), now=_NOW)

    rows = await _notify_rows(sessionmaker, booking_id)
    assert len(rows) == 1
    assert _as_utc(rows[0].next_retry_at) == _SEND_AT_24H_BEFORE


async def test_editing_the_offset_in_the_admin_moves_the_reminder_already_queued(
    sessionmaker: Sessionmaker,
) -> None:
    """==The defect this whole module exists to prevent.==

    A tenant changes "remind 24 h before" to "1 h before", the panel shows the new rule, and every
    guest already on the books is still reminded 24 h before — no error, no warning, nothing in a
    log. The rule row is not the send time; the OUTBOX row is. So the assertion is on the outbox.

    And it must be the SAME row (same id): a step's id is half of its dedupe key, so a re-created
    step has no memory of the reminder it already sent, and the guest is reminded twice.
    """
    tenant_id, event_type_id = await _seed(sessionmaker)
    booking_id = await _book(sessionmaker, tenant_id=tenant_id, event_type_id=event_type_id)
    rule = await create_workflow_action(
        sessionmaker, tenant_slug="acme", data=_reminder_email(), now=_NOW
    )
    before = await _notify_rows(sessionmaker, booking_id)
    assert _as_utc(before[0].next_retry_at) == _SEND_AT_24H_BEFORE

    await update_workflow_action(
        sessionmaker,
        tenant_slug="acme",
        workflow_id=rule.id,
        data=WorkflowUpdate(offset_minutes=-60),
        now=_NOW,
    )

    after = await _notify_rows(sessionmaker, booking_id)
    assert len(after) == 1
    assert _as_utc(after[0].next_retry_at) == _SEND_AT_1H_BEFORE
    assert after[0].id == before[0].id, "the step was re-created: its dedupe key is now a new one"


async def test_switching_a_rule_off_from_the_admin_is_visible_in_the_database(
    sessionmaker: Sessionmaker,
) -> None:
    """The toggle writes the ROW, not just the screen."""
    await _seed(sessionmaker)
    rule = await create_workflow_action(
        sessionmaker, tenant_slug="acme", data=_reminder_email(), now=_NOW
    )

    await set_workflow_active_action(
        sessionmaker, tenant_slug="acme", workflow_id=rule.id, active=False, now=_NOW
    )

    async with sessionmaker() as session:
        stored = await session.get(Workflow, rule.id)
        assert stored is not None
        assert stored.active is False


# --------------------------------------------------------------------------------------
# A rule that cannot send has to fail LOUD, at the panel — not silently, at the drain.
# --------------------------------------------------------------------------------------


async def test_a_step_with_no_template_is_refused_and_no_rule_is_written(
    sessionmaker: Sessionmaker,
) -> None:
    """A WhatsApp step with no template reaches the drain, is skipped, and messages nobody — while
    the rule reads ``active: true`` for ever. The panel refuses it, and refuses it WITHOUT leaving a
    half-built rule behind (the effective state, not merely the exception)."""
    await _seed(sessionmaker)

    with pytest.raises(AdminActionError) as refusal:
        await create_workflow_action(
            sessionmaker, tenant_slug="acme", data=_whatsapp_rule(), now=_NOW
        )
    assert "template" in refusal.value.message

    assert await list_workflows_view(sessionmaker, tenant_slug="acme") == []


async def test_a_duplicate_rule_name_is_refused(sessionmaker: Sessionmaker) -> None:
    await _seed(sessionmaker)
    await create_workflow_action(sessionmaker, tenant_slug="acme", data=_reminder_email(), now=_NOW)

    with pytest.raises(AdminActionError):
        await create_workflow_action(
            sessionmaker, tenant_slug="acme", data=_reminder_email(), now=_NOW
        )


async def test_editing_an_unknown_rule_is_an_error_not_a_silent_success(
    sessionmaker: Sessionmaker,
) -> None:
    """The service returns ``None`` for an absent row rather than raising, so a handler that forgets
    to check it reports a save that never happened."""
    await _seed(sessionmaker)

    with pytest.raises(AdminActionError):
        await update_workflow_action(
            sessionmaker,
            tenant_slug="acme",
            workflow_id=uuid.uuid4(),
            data=WorkflowUpdate(active=False),
            now=_NOW,
        )


# --------------------------------------------------------------------------------------
# Templates — the bodies the steps render.
# --------------------------------------------------------------------------------------


async def test_a_template_round_trips_through_the_admin(sessionmaker: Sessionmaker) -> None:
    await _seed(sessionmaker)

    created = await create_template_action(
        sessionmaker, tenant_slug="acme", data=_whatsapp_template()
    )
    listed = await list_templates_view(sessionmaker, tenant_slug="acme")
    assert [row.id for row in listed] == [created.id]

    await update_template_action(
        sessionmaker,
        tenant_slug="acme",
        template_id=created.id,
        data=WorkflowTemplateUpdate(body="Hola {{guest_name}}."),
    )
    async with sessionmaker() as session:
        stored = await session.get(WorkflowTemplate, created.id)
        assert stored is not None
        assert stored.body == "Hola {{guest_name}}."


async def test_a_template_body_may_not_smuggle_an_unknown_variable() -> None:
    """The allow-list is the schema's, and the admin has no way past it: the body is DATA."""
    with pytest.raises(ValueError, match="unknown template variable"):
        WorkflowTemplateCreate(
            channel="whatsapp", kind="reminder", locale="es", body="{{host_password}}"
        )


async def test_deleting_the_last_body_a_live_step_renders_is_refused(
    sessionmaker: Sessionmaker,
) -> None:
    """Delete it and the step keeps its ``active: true`` and silently stops sending. The panel
    refuses, and the template is STILL THERE afterwards (the effective state)."""
    await _seed(sessionmaker)
    template = await create_template_action(
        sessionmaker, tenant_slug="acme", data=_whatsapp_template()
    )
    await create_workflow_action(sessionmaker, tenant_slug="acme", data=_whatsapp_rule(), now=_NOW)

    with pytest.raises(AdminActionError):
        await delete_template_action(sessionmaker, tenant_slug="acme", template_id=template.id)

    async with sessionmaker() as session:
        assert await session.get(WorkflowTemplate, template.id) is not None


async def test_deleting_an_unknown_template_is_an_error_not_a_silent_success(
    sessionmaker: Sessionmaker,
) -> None:
    """``delete_template`` returns ``False`` for an absent row rather than raising. Reported as a
    success, that is the panel telling the operator it deleted something that was never there."""
    await _seed(sessionmaker)

    with pytest.raises(AdminActionError):
        await delete_template_action(sessionmaker, tenant_slug="acme", template_id=uuid.uuid4())


# --------------------------------------------------------------------------------------
# Tenant scoping.
# --------------------------------------------------------------------------------------


async def test_a_tenant_never_sees_another_tenants_rules(sessionmaker: Sessionmaker) -> None:
    await _seed(sessionmaker, slug="alpha")
    await _seed(sessionmaker, slug="beta")
    await create_workflow_action(sessionmaker, tenant_slug="beta", data=_reminder_email(), now=_NOW)

    assert await list_workflows_view(sessionmaker, tenant_slug="alpha") == []
    assert len(await list_workflows_view(sessionmaker, tenant_slug="beta")) == 1
