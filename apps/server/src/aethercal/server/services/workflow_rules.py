"""Authoring the rules (RF-24): create, list, edit, activate/deactivate workflows, steps, templates.

``services/workflows.py`` decides what a booking's TRANSITIONS do to its steps. This module decides
what a TENANT may author — and, crucially, what their edit actually does to the messages already
queued for the bookings they have on the books.

.. rubric:: An edit that changes nothing a guest can perceive is a lie

The send time of a step does not live in the rule. It lives in the outbox row's ``next_retry_at``,
written when the booking was confirmed. So a tenant who changes "remind 24 h before" to "2 h
before" rewrites a row in ``workflows``, sees the change reflected in the API, and every guest
already on the books is still reminded 24 h before — no error, no warning, nothing in a log. That is
this project's disease, one layer above the one the transition table cures.

Therefore **every mutation of a rule reconciles the queue of every booking it governs**
(:func:`~aethercal.server.services.outbox.reconcile_workflow_steps`): a step removed is voided, a
step re-timed is moved in place (the SAME row, so the message stays exactly-once), a step added is
queued for the bookings that already exist. Creating a rule does it too — "remind the guest 24 h
before", authored this morning, is meant for the meeting that is already in the diary next week.

.. rubric:: What is refused, and why each refusal is a message that would otherwise go wrong

* **a rule with no steps** — it fires nothing, for ever, and raises nothing.
* **an offset on an event-shaped trigger** — the engine ignores it (:func:`step_send_time` returns
  ``None``), so "2 h after the cancellation" would in fact go out immediately.
* **a step that cannot render a body** — a WhatsApp step with no template reaches the drain, is
  SKIPPED with a reason, and never messages anybody. The rule reads ``active: true`` for ever.
* **deleting the last template a step depends on** — the same silence, reached from the other side.

.. rubric:: A step's id is an identity, not a detail

It is half of the step's outbox dedupe key, which is what makes the message exactly-once. So an
edit MATCHES the incoming steps to the stored ones by channel and updates them in place; it never
deletes and re-creates them. A re-created step takes a new id, hence a new dedupe key, hence no
memory of the reminder it already sent — and the guest is reminded twice.

Transaction control (commit/rollback) belongs to the caller, as everywhere else in this layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.workflows import (
    WorkflowCreate,
    WorkflowStepIn,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
    check_offset,
    check_subject,
)
from aethercal.server.channels import Channel
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Workflow,
    WorkflowStep,
    WorkflowTemplate,
)
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.outbox import (
    OutboxEffect,
    StepSchedule,
    reconcile_workflow_steps,
    workflow_key_prefix,
    workflow_step_dedupe_key,
)
from aethercal.server.services.workflows import DEFAULT_LOCALE, notify_payload, step_send_time

_BUILTIN_EMAIL_KINDS: frozenset[str] = frozenset(kind.value for kind in NotificationKind)
"""The kinds the built-in composer renders on its own (it also carries the ``.ics``). Everything
else — every phone step, and any tenant-invented kind — needs a template, or it is a message that
never arrives."""

_LIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)
"""The bookings a rule still governs. A cancelled / no-show booking has settled its steps, and
re-arming them from a rule edit would message somebody about a meeting that was called off."""

_POSITION_PARKING = 1000
"""Positions are unique per workflow, so a straight swap (0↔1) collides with the row that has not
moved yet. Surviving steps are parked above every real position first, then written to their final
ones."""


class WorkflowRuleError(Exception):
    """Base class for the rule-authoring errors the API maps onto a clean HTTP status."""


class DuplicateNameError(WorkflowRuleError):
    """The tenant already has a workflow with that name (→ HTTP 409)."""


class DuplicateTemplateError(WorkflowRuleError):
    """The tenant already has a template for that (channel, kind, locale) (→ HTTP 409)."""


class InvalidReferenceError(WorkflowRuleError):
    """An ``event_type_id`` that is unknown, or is not this tenant's (→ HTTP 422)."""


class InvalidRuleError(WorkflowRuleError):
    """The rule is self-defeating: an incoherent offset, or a step that can never render (→ 422)."""


class InvalidTemplateError(WorkflowRuleError):
    """The template's text contradicts its own channel — an email with no subject line, say (→ 422).

    It can only be caught HERE: the schema sees the new text but not the channel it belongs to."""


class TemplateInUseError(WorkflowRuleError):
    """The last body a live step can render cannot be deleted (→ HTTP 409)."""


@dataclass(frozen=True, slots=True)
class Rule:
    """A workflow together with its steps — the unit every caller actually means.

    Explicit, rather than an ORM ``relationship``: this codebase has none (every FK is queried on
    purpose), and a lazy load on an :class:`AsyncSession` raises ``MissingGreenlet`` at the worst
    possible moment."""

    workflow: Workflow
    steps: list[WorkflowStep]


# --------------------------------------------------------------------------------------
# Validation — every refusal here is a message that would otherwise go silently wrong.
# --------------------------------------------------------------------------------------


async def _require_event_type(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_type_id: uuid.UUID | None
) -> None:
    """A rule may only be scoped to an event type of its OWN tenant (else it fires on theirs)."""
    if event_type_id is None:
        return
    found = (
        await session.scalars(
            select(EventType.id).where(
                EventType.id == event_type_id, EventType.tenant_id == tenant_id
            )
        )
    ).one_or_none()
    if found is None:
        raise InvalidReferenceError(f"event type {event_type_id} does not belong to this tenant")


async def _require_renderable(
    session: AsyncSession, *, tenant_id: uuid.UUID, steps: Sequence[WorkflowStepIn]
) -> None:
    """Refuse a step that has no way to produce a body. Fail LOUD here, or fail SILENT at the drain.

    ``email`` + a built-in kind is rendered by the composer that already exists. Everything else
    resolves through ``workflow_templates``. With no template row the step reaches the drain, raises
    ``OutboxSkipped``, and retires itself with a reason nobody is watching — the guest simply never
    hears from it, and the rule still reads ``active: true``."""
    stored = {
        (channel, kind)
        for channel, kind in await session.execute(
            select(WorkflowTemplate.channel, WorkflowTemplate.kind).where(
                WorkflowTemplate.tenant_id == tenant_id
            )
        )
    }
    for step in steps:
        if step.channel == Channel.EMAIL.value and step.kind in _BUILTIN_EMAIL_KINDS:
            continue
        if (step.channel, step.kind) in stored:
            continue
        raise InvalidRuleError(
            f"the {step.channel} step of kind '{step.kind}' has no template to render its body "
            f"(workflow_templates has no row for channel='{step.channel}', kind='{step.kind}'). "
            "Write the template first: without one the step is skipped at send time and the guest "
            "is never messaged, silently"
        )


def _check_offset(trigger: str, offset_minutes: int) -> None:
    """The schema's coherence rule, re-run on the MERGED rule (a PATCH sends only some fields)."""
    try:
        check_offset(trigger, offset_minutes)
    except ValueError as exc:
        raise InvalidRuleError(str(exc)) from exc


async def _reload(session: AsyncSession, row: Workflow | WorkflowTemplate) -> None:
    """Re-read a row the flush left half-loaded, so the caller gets one it can actually READ.

    ``updated_at`` carries a SQL-side ``onupdate=func.now()`` (``db/base.py``), so an UPDATE leaves
    the attribute EXPIRED — its new value exists only in the database. The next read of it emits a
    lazy SELECT, and on an :class:`AsyncSession` that is not a slow path but a ``MissingGreenlet``
    crash: sync IO in an async context, raised far from here — inside the API's serializer. So the
    row is reloaded HERE, in the async call that dirtied it, and this service hands back a row every
    field of which can simply be read.

    Why it never bit ``event_types``: ``EventTypeRead`` does not expose the timestamps at all, so
    nothing ever touched the expired attribute. ``WorkflowRead`` does."""
    await session.refresh(row)


# --------------------------------------------------------------------------------------
# Reads.
# --------------------------------------------------------------------------------------


async def _load_steps(session: AsyncSession, workflow_id: uuid.UUID) -> list[WorkflowStep]:
    return list(
        (
            await session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow_id)
                .order_by(WorkflowStep.position)
            )
        ).all()
    )


async def get_workflow(
    session: AsyncSession, *, tenant_id: uuid.UUID, workflow_id: uuid.UUID
) -> Rule | None:
    """The tenant's rule by id, steps included. ``None`` when it is not theirs (or absent)."""
    workflow = (
        await session.scalars(
            select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
        )
    ).one_or_none()
    if workflow is None:
        return None
    return Rule(workflow=workflow, steps=await _load_steps(session, workflow.id))


async def list_workflows(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Rule]:
    """Every rule of the tenant (active and inactive), oldest first, each with its steps."""
    workflows = (
        await session.scalars(
            select(Workflow)
            .where(Workflow.tenant_id == tenant_id)
            .order_by(Workflow.created_at, Workflow.id)
        )
    ).all()
    return [
        Rule(workflow=workflow, steps=await _load_steps(session, workflow.id))
        for workflow in workflows
    ]


#: The channels that message a PHONE — the only reason to ever ask a guest for one.
PHONE_CHANNELS: frozenset[Channel] = frozenset({Channel.WHATSAPP, Channel.SMS})


@dataclass(frozen=True, slots=True)
class PhoneChannelScope:
    """Which of a tenant's event types are governed by an ACTIVE WhatsApp/SMS rule.

    This answers the only question the public booking form is allowed to ask itself before it puts
    a phone field on screen: *will anything actually message this number?* If nothing will, the
    field must not exist — a phone collected "just in case" is PII taken from a stranger for a
    message that will never be sent (RNF-8: minimal data).
    """

    #: A rule with ``event_type_id = NULL`` governs EVERY event type of the tenant.
    tenant_wide: bool
    #: The event types named explicitly by an active phone rule.
    event_type_ids: frozenset[uuid.UUID]

    def covers(self, event_type_id: uuid.UUID) -> bool:
        """Whether a booking for ``event_type_id`` may be asked for a phone + consent."""
        return self.tenant_wide or event_type_id in self.event_type_ids


async def phone_channel_scope(session: AsyncSession, *, tenant_id: uuid.UUID) -> PhoneChannelScope:
    """Where an ACTIVE WhatsApp/SMS rule exists for this tenant — i.e. where a phone gets used.

    Three conditions, and every one of them is load-bearing. ``active`` is: a paused rule sends
    nothing, so it may collect nothing. The channel filter is: the seeded 24 h **e-mail** reminder
    governs every tenant on the instance, so reading "has a rule" as "wants a phone" would put a
    phone field in front of every guest of every business here. And ``tenant_id`` is: a neighbour's
    WhatsApp rule is not a licence to collect our guests' numbers.
    """
    rows = await session.scalars(
        select(Workflow.event_type_id)
        .join(
            WorkflowStep,
            (WorkflowStep.workflow_id == Workflow.id)
            & (WorkflowStep.tenant_id == Workflow.tenant_id),
        )
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.active.is_(True),
            WorkflowStep.channel.in_(sorted(channel.value for channel in PHONE_CHANNELS)),
        )
        .distinct()
    )
    scoped = list(rows.all())
    return PhoneChannelScope(
        tenant_wide=any(event_type_id is None for event_type_id in scoped),
        event_type_ids=frozenset(
            event_type_id for event_type_id in scoped if event_type_id is not None
        ),
    )


# --------------------------------------------------------------------------------------
# Writes.
# --------------------------------------------------------------------------------------


async def create_workflow(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: WorkflowCreate, now: datetime
) -> Rule:
    """Author a rule and ARM it — on the bookings that already exist, not only on the next one.

    The reconcile at the end is the point: a tenant who writes "remind the guest 24 h before" this
    morning means it for the meeting already in the diary next week. Materialising only for FUTURE
    bookings would leave that guest silently unreminded while the rule reads ``active: true``."""
    await _require_event_type(session, tenant_id=tenant_id, event_type_id=data.event_type_id)
    await _require_renderable(session, tenant_id=tenant_id, steps=data.steps)

    workflow = Workflow(
        tenant_id=tenant_id,
        event_type_id=data.event_type_id,
        name=data.name,
        trigger=data.trigger,
        offset_minutes=data.offset_minutes,
        active=data.active,
    )
    try:
        async with session.begin_nested():
            session.add(workflow)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateNameError(f"a workflow named '{data.name}' already exists") from exc

    for step in data.steps:
        session.add(
            WorkflowStep(
                tenant_id=tenant_id,
                workflow_id=workflow.id,
                channel=step.channel,
                kind=step.kind,
                position=step.position,
            )
        )
    await session.flush()

    steps = await _load_steps(session, workflow.id)
    # A brand-new rule has no queued rows to mistime, so the flag is moot — but it is stated, not
    # defaulted: a rule that has just been written IS its own timing.
    await _reconcile(session, workflow=workflow, steps=steps, now=now, timing_changed=True)
    return Rule(workflow=workflow, steps=steps)


async def update_workflow(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    now: datetime,
) -> Rule | None:
    """Edit the rule, then MAKE THE EDIT TRUE for every booking it already governs.

    Only the fields the caller actually sent are touched (``exclude_unset``, so an explicit
    ``event_type_id: null`` widens the rule to every event type while an omitted one leaves its
    scope alone). Offset coherence is re-checked on the MERGED rule: a PATCH carrying only
    ``{"trigger": "on_cancel"}`` is perfectly self-consistent, and would still leave behind a stored
    ``-1440`` that the engine ignores for ever."""
    rule = await get_workflow(session, tenant_id=tenant_id, workflow_id=workflow_id)
    if rule is None:
        return None
    workflow = rule.workflow

    fields = data.model_dump(exclude_unset=True)
    steps_in: list[WorkflowStepIn] | None = data.steps if "steps" in fields else None
    fields.pop("steps", None)

    # Did THIS edit move the rule's clock? Only then may a queued step whose send time now lies in
    # the past be retired: otherwise that step is not mistimed, it is OVERDUE (it was paused while
    # the rule was off), and retiring it would destroy the message on the very edit — the switch
    # back ON — that exists to deliver it.
    timing_changed = "trigger" in fields or "offset_minutes" in fields or steps_in is not None

    _check_offset(
        str(fields.get("trigger", workflow.trigger)),
        int(fields.get("offset_minutes", workflow.offset_minutes)),
    )
    if "event_type_id" in fields:
        await _require_event_type(
            session, tenant_id=tenant_id, event_type_id=fields["event_type_id"]
        )
    if steps_in is not None:
        await _require_renderable(session, tenant_id=tenant_id, steps=steps_in)

    target_name = str(fields.get("name", workflow.name))
    try:
        async with session.begin_nested():
            for key, value in fields.items():
                setattr(workflow, key, value)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateNameError(f"a workflow named '{target_name}' already exists") from exc
    await _reload(session, workflow)

    if steps_in is not None:
        await _sync_steps(session, workflow=workflow, wanted=steps_in)

    steps = await _load_steps(session, workflow.id)
    await _reconcile(
        session, workflow=workflow, steps=steps, now=now, timing_changed=timing_changed
    )
    return Rule(workflow=workflow, steps=steps)


async def set_workflow_active(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    active: bool,
    now: datetime,
) -> Rule | None:
    """Switch a rule on or off. Off PAUSES its messages; on delivers them.

    ==The toggle is symmetric, and nothing about it is terminal.== Switching OFF neither voids the
    queued rows nor lets the drain retire them: a step whose rule is inactive is PAUSED
    (``_gate_on_the_rule`` → ``OutboxDeferred``), so it stays ``pending``, consumes no attempt, and
    survives. Anything terminal here — voiding the row, or skipping it at the drain — would destroy
    a
    message over a condition the tenant can undo with one click, and its dedupe key would stay
    occupied for ever, so switching the rule back on could never restore it.

    Switching ON reconciles: a booking taken while the rule was off is armed, and a step that came
    due while it was off is made due NOW rather than mistaken for one the edit mistimed. What is no
    longer worth sending is stopped at the send, by ``message_deadline`` — not here, and not for
    ever.
    """
    return await update_workflow(
        session,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        data=WorkflowUpdate(active=active),
        now=now,
    )


async def _sync_steps(
    session: AsyncSession, *, workflow: Workflow, wanted: Sequence[WorkflowStepIn]
) -> None:
    """Bring the stored steps in line with ``wanted``, PRESERVING the id of every surviving step.

    Matched by CHANNEL, which is a step's identity within a rule (the schema allows only one step
    per channel). Delete-and-recreate would be simpler and is a real bug: the step id is half of the
    outbox dedupe key, so a re-created step has no memory of the reminder it already sent — and the
    guest is reminded a second time.
    """
    stored = {step.channel: step for step in await _load_steps(session, workflow.id)}
    incoming = {step.channel: step for step in wanted}

    for channel, step in stored.items():
        if channel not in incoming:
            await session.delete(step)

    # Positions are unique per (tenant, workflow), so writing them straight can collide with a row
    # that has not moved yet (a plain 0↔1 swap). Park the survivors above every real position first.
    for offset, step in enumerate(stored.values()):
        if step.channel in incoming:
            step.position = _POSITION_PARKING + offset
    await session.flush()

    for channel, incoming_step in incoming.items():
        step = stored.get(channel)
        if step is None:
            session.add(
                WorkflowStep(
                    tenant_id=workflow.tenant_id,
                    workflow_id=workflow.id,
                    channel=incoming_step.channel,
                    kind=incoming_step.kind,
                    position=incoming_step.position,
                )
            )
        else:
            step.kind = incoming_step.kind
            step.position = incoming_step.position
    await session.flush()


# --------------------------------------------------------------------------------------
# The reconcile: what the rule now MEANS for the bookings that already exist.
# --------------------------------------------------------------------------------------


async def _reconcile(
    session: AsyncSession,
    *,
    workflow: Workflow,
    steps: Sequence[WorkflowStep],
    now: datetime,
    timing_changed: bool,
) -> None:
    """Re-derive every governed booking's queued steps from the rule as it now stands.

    ``timing_changed`` says whether THIS operation moved the rule's clock (its trigger, its offset,
    or its steps). It is what tells an OVERDUE row apart from a MISTIMED one, and the two must not
    share a fate: an edit that drags a send time into the past retires the step (it would otherwise
    fire at once, after the fact), whereas a step that is merely overdue — paused while the tenant
    had the rule switched off — must be made DUE, not destroyed.

    An INACTIVE rule is skipped entirely — neither armed nor voided. Its queued rows are inert while
    it is off (the drain PAUSES them; it does not retire them), and voiding would burn their dedupe
    keys for ever, so switching the rule back on could never restore them. Everything is put right
    when it IS switched back on, which reconciles.

    The cost is bounded by the tenant's live bookings, and a rule edit is a rare, deliberate act."""
    if not workflow.active:
        return

    bookings = await _governed_bookings(session, workflow=workflow, now=now)
    if not bookings:
        return
    locales = await _locales_of(session, bookings=bookings)
    trigger = WorkflowTrigger(workflow.trigger)
    offset = timedelta(minutes=workflow.offset_minutes)

    for booking in bookings:
        locale = locales.get(booking.id, DEFAULT_LOCALE)
        wanted = [
            StepSchedule(
                dedupe_key=workflow_step_dedupe_key(workflow.id, step.id, Channel(step.channel)),
                payload=notify_payload(
                    workflow_id=workflow.id,
                    step_id=step.id,
                    trigger=trigger,
                    channel=Channel(step.channel),
                    kind=step.kind,
                    locale=locale,
                ),
                send_at=step_send_time(trigger, booking=booking, offset=offset),
            )
            for step in steps
        ]
        await reconcile_workflow_steps(
            session,
            tenant_id=workflow.tenant_id,
            booking_id=booking.id,
            workflow_id=workflow.id,
            wanted=wanted,
            now=now,
            timing_changed=timing_changed,
        )


async def _governed_bookings(
    session: AsyncSession, *, workflow: Workflow, now: datetime
) -> Sequence[Booking]:
    """The tenant's LIVE bookings this rule speaks for.

    Two shapes qualify. The obvious one is anything that has not ended yet. The other is a booking
    that IS over and still has a row queued for this workflow — an ``after_end`` follow-up due in an
    hour. Leave that out and editing the rule would strand exactly the step the edit was about."""
    scope = (
        [Booking.event_type_id == workflow.event_type_id]
        if workflow.event_type_id is not None
        else []
    )
    queued = select(Outbox.booking_id).where(
        Outbox.tenant_id == workflow.tenant_id,
        Outbox.effect == OutboxEffect.NOTIFY.value,
        Outbox.dedupe_key.startswith(workflow_key_prefix(workflow.id)),
    )
    return (
        await session.scalars(
            select(Booking)
            .where(
                Booking.tenant_id == workflow.tenant_id,
                Booking.status.in_(_LIVE_STATUSES),
                *scope,
                or_(Booking.end_at >= now, Booking.id.in_(queued)),
            )
            .order_by(Booking.start_at, Booking.id)
        )
    ).all()


async def _locales_of(
    session: AsyncSession, *, bookings: Sequence[Booking]
) -> Mapping[uuid.UUID, str]:
    """The locale each booking's steps were queued in — a guest is not asked their language twice.

    The locale is a property of the REQUEST that made the booking, not of the booking row, so a step
    queued later by a rule edit has no other way to know it. Falling back to the default would
    mail a Spanish reminder to somebody who booked in English."""
    rows = (
        await session.scalars(
            select(Outbox).where(
                Outbox.booking_id.in_([booking.id for booking in bookings]),
                Outbox.effect == OutboxEffect.NOTIFY.value,
            )
        )
    ).all()
    return {row.booking_id: str(row.payload["locale"]) for row in rows if "locale" in row.payload}


# --------------------------------------------------------------------------------------
# Templates — the bodies the steps render.
# --------------------------------------------------------------------------------------


async def create_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: WorkflowTemplateCreate
) -> WorkflowTemplate:
    """Store the body for one ``(channel, kind, locale)``. 409 on a duplicate identity."""
    row = WorkflowTemplate(
        tenant_id=tenant_id,
        channel=data.channel,
        kind=data.kind,
        locale=data.locale,
        subject=data.subject,
        body=data.body,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateTemplateError(
            f"a template already exists for channel='{data.channel}', kind='{data.kind}', "
            f"locale='{data.locale}'"
        ) from exc
    return row


async def get_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> WorkflowTemplate | None:
    """The tenant's template by id, or ``None`` when it is not theirs (or absent)."""
    return (
        await session.scalars(
            select(WorkflowTemplate).where(
                WorkflowTemplate.id == template_id, WorkflowTemplate.tenant_id == tenant_id
            )
        )
    ).one_or_none()


async def list_templates(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[WorkflowTemplate]:
    """Every template of the tenant, oldest first."""
    return list(
        (
            await session.scalars(
                select(WorkflowTemplate)
                .where(WorkflowTemplate.tenant_id == tenant_id)
                .order_by(WorkflowTemplate.created_at, WorkflowTemplate.id)
            )
        ).all()
    )


async def update_template(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    data: WorkflowTemplateUpdate,
) -> WorkflowTemplate | None:
    """Edit a template's TEXT. Its ``(channel, kind, locale)`` identity is immutable by design —
    re-pointing it would silently change what every step resolving through it sends."""
    row = await get_template(session, tenant_id=tenant_id, template_id=template_id)
    if row is None:
        return None

    fields = data.model_dump(exclude_unset=True)
    # The subject is judged against the template's CHANNEL, and only this layer knows the channel.
    # The schema can refuse a null body (the column is NOT NULL), but not this: `{"subject": null}`
    # is a perfectly valid payload — it is what a WhatsApp template's subject IS — and applied to an
    # EMAIL template the column would accept it, store it, and later deliver an email with a blank
    # subject line. The CREATE path already forbids exactly that shape; the edit path must not be
    # the way around it.
    try:
        check_subject(row.channel, fields.get("subject", row.subject))
    except ValueError as exc:
        raise InvalidTemplateError(str(exc)) from exc

    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await _reload(session, row)
    return row


async def delete_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> bool:
    """Delete a template; ``False`` when it is not the tenant's (or absent).

    **Refused while it is the last body a step can render.** Delete it and that step keeps its
    ``active: true`` and stops sending: at the drain it raises ``OutboxSkipped`` and retires itself
    with a reason nobody is reading, so the guest simply never hears from it. The refusal names the
    rules that hold it — unless another locale still covers the same ``(channel, kind)``, or the
    composer owns it (an email of a built-in kind needs no template).
    """
    row = await get_template(session, tenant_id=tenant_id, template_id=template_id)
    if row is None:
        return False

    composer_owned = row.channel == Channel.EMAIL.value and row.kind in _BUILTIN_EMAIL_KINDS
    if not composer_owned:
        siblings = (
            await session.scalars(
                select(WorkflowTemplate.id).where(
                    WorkflowTemplate.tenant_id == tenant_id,
                    WorkflowTemplate.channel == row.channel,
                    WorkflowTemplate.kind == row.kind,
                    WorkflowTemplate.id != row.id,
                )
            )
        ).all()
        if not siblings:
            holders = (
                await session.scalars(
                    select(Workflow.name)
                    .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
                    .where(
                        Workflow.tenant_id == tenant_id,
                        WorkflowStep.channel == row.channel,
                        WorkflowStep.kind == row.kind,
                    )
                    .order_by(Workflow.name)
                )
            ).all()
            if holders:
                names = ", ".join(f"'{name}'" for name in holders)
                raise TemplateInUseError(
                    f"the {row.channel} step of kind '{row.kind}' in {names} has no other body to "
                    "render: deleting this template would silently stop those messages. Remove the "
                    "step, or write another template for the same channel and kind, first"
                )

    await session.execute(
        delete(WorkflowTemplate).where(
            WorkflowTemplate.id == row.id, WorkflowTemplate.tenant_id == tenant_id
        )
    )
    await session.flush()
    return True


__all__ = [
    "DuplicateNameError",
    "DuplicateTemplateError",
    "InvalidReferenceError",
    "InvalidRuleError",
    "InvalidTemplateError",
    "Rule",
    "TemplateInUseError",
    "WorkflowRuleError",
    "create_template",
    "create_workflow",
    "delete_template",
    "get_template",
    "get_workflow",
    "list_templates",
    "list_workflows",
    "set_workflow_active",
    "update_template",
    "update_workflow",
]
