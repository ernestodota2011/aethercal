"""Workflow rule + template request/response schemas (RF-24): the notification-engine API contract.

A **workflow** is a rule ("24 h before the start, remind the guest"); a **step** is one message on
one channel within it; a **template** is the body a step renders for a ``(channel, kind, locale)``.

.. rubric:: Why the vocabularies are re-declared here

``aethercal.schemas`` may not import ``aethercal.server`` — the layering contract forbids it
(``pyproject.toml``, import-linter) — so the trigger and channel names cannot BE the server's enums.
They are ``Literal``s instead, which is a duplication and would be a drift waiting to happen;
``test_the_api_vocabulary_matches_the_engine_enums`` asserts the two sets are identical, so adding a
trigger to the engine without adding it here fails a test rather than 422-ing a value the engine
happily fires.

.. rubric:: Every rejection here is a message that would otherwise go silently wrong

The bounds are not decoration — each one is a rule that, if accepted, yields a workflow that raises
nothing and does the wrong thing:

* **the offset must be coherent with the trigger.** ``on_booking``/``on_cancel``/``on_no_show`` fire
  at the instant their event happens: the engine's send time IGNORES ``offset_minutes`` for them
  (``services/workflows.py`` ``_send_time`` returns ``None``). Accept "2 h after the cancellation"
  and the tenant has scheduled a message that in fact goes out immediately, with no error anywhere.
  And the SIGN is the direction: a ``before_start`` with ``+60`` would "remind" the guest an hour
  after the meeting started.
* **at least one step.** A rule with no steps fires nothing, for ever, silently.
* **one step per channel.** Two email steps are two ids, so two dedupe keys, so two identical emails
  — the outbox's exactly-once guarantee holds separately for each of them.
* **only allowlisted ``{{variables}}``, and no expression tags.** The body is DATA, never
  instructions: no Jinja, no ``eval``. An unknown variable renders as literal garbage into a real
  guest's message; an expression tag is an invitation to evaluate a tenant-authored string.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkflowTriggerName = Literal["on_booking", "before_start", "after_end", "on_cancel", "on_no_show"]
"""What fires a rule. Mirrors ``aethercal.server.db.models.workflows.WorkflowTrigger``."""

WORKFLOW_TRIGGER_NAMES: tuple[WorkflowTriggerName, ...] = get_args(WorkflowTriggerName)

ChannelName = Literal["email", "whatsapp", "sms"]
"""A step's delivery channel. Mirrors ``aethercal.server.channels.Channel``."""

CHANNEL_NAMES: tuple[ChannelName, ...] = get_args(ChannelName)

EVENT_SHAPED_TRIGGERS: frozenset[str] = frozenset({"on_booking", "on_cancel", "on_no_show"})
"""The triggers that fire the moment their event happens — and therefore carry NO offset."""

SUBJECTLESS_CHANNELS: frozenset[str] = frozenset({"whatsapp", "sms"})
"""Channels with no concept of a subject line. A subject stored for one is a field nobody reads."""

MAX_OFFSET_MINUTES = 366 * 24 * 60
"""A year, either way. Beyond it the tenant has mis-typed (a step queued four decades out is not a
feature), and the outbox would carry the row until then."""

TEMPLATE_VARIABLES: frozenset[str] = frozenset(
    {
        "guest_name",
        "guest_email",
        "event_title",
        "start_local",
        "end_local",
        "timezone",
        "host_name",
        "cancel_url",
        "reschedule_url",
        "meeting_url",
    }
)
"""The ONLY substitutions a template body/subject may contain (strict allow-list).

It governs WHICH variables exist, never what they contain: the values come from the guest and are
rendered into mail to the host, into WhatsApp/SMS and into the admin panel, so the renderer escapes
per channel, caps each length, and neutralises URLs/markup in free text. That is the renderer's job;
this list is the vocabulary it may draw on."""

SUPPORTED_LOCALES: frozenset[str] = frozenset({"es", "en"})
"""The locales the platform has chrome for (kept in step with ``schemas.event_types``)."""

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_EXPRESSION_TAG = re.compile(r"\{%|%\}|\{#|#\}")

ShortText = Annotated[str, Field(min_length=1, max_length=255)]
Kind = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")]
"""A step's content kind (``reminder``, ``follow_up``, …) — a ledger key, so it stays a slug."""
Body = Annotated[str, Field(min_length=1, max_length=4000)]
Locale = Annotated[str, Field(min_length=2, max_length=16)]


def check_offset(trigger: str, offset_minutes: int) -> None:
    """Raise ``ValueError`` unless ``offset_minutes`` means anything at all for ``trigger``.

    The single home of the coherence rule: :class:`WorkflowCreate` enforces it at the edge, and the
    service re-runs it on the MERGED rule after a partial update (a PATCH that changes only the
    trigger leaves an offset behind that the engine would then ignore for ever)."""
    if abs(offset_minutes) > MAX_OFFSET_MINUTES:
        raise ValueError(
            f"offset_minutes must be within +/-{MAX_OFFSET_MINUTES} minutes (one year); "
            f"got {offset_minutes}"
        )
    if trigger in EVENT_SHAPED_TRIGGERS:
        if offset_minutes != 0:
            raise ValueError(
                f"trigger '{trigger}' fires the moment its event happens and IGNORES "
                "offset_minutes, so a non-zero offset would be a message you believe you scheduled "
                "and that in fact goes out immediately; use before_start/after_end to delay one"
            )
        return
    if trigger == "before_start" and offset_minutes > 0:
        raise ValueError(
            "trigger 'before_start' takes a negative offset_minutes (-1440 = 24 h before the "
            f"start); {offset_minutes} would fire AFTER the meeting began"
        )
    if trigger == "after_end" and offset_minutes < 0:
        raise ValueError(
            "trigger 'after_end' takes a non-negative offset_minutes (60 = an hour after the end); "
            f"{offset_minutes} would fire BEFORE the meeting was over"
        )


def check_subject(channel: str, subject: str | None) -> None:
    """Raise ``ValueError`` unless the subject matches what ``channel`` can actually carry."""
    if channel in SUBJECTLESS_CHANNELS and subject is not None:
        raise ValueError(
            f"the {channel} channel has no subject line; a subject stored for it is a field "
            "nobody ever reads"
        )
    if channel not in SUBJECTLESS_CHANNELS and subject is None:
        raise ValueError(f"the {channel} channel needs a subject (an email without one is blank)")


def check_template_text(text: str, *, field: str) -> None:
    """Raise ``ValueError`` unless ``text`` uses only allowlisted ``{{variables}}`` and no tags."""
    if _EXPRESSION_TAG.search(text):
        raise ValueError(
            f"{field} may not contain an expression tag: a template is DATA, never instructions — "
            "substitution is strict and nothing is ever evaluated"
        )
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name not in TEMPLATE_VARIABLES:
            raise ValueError(
                f"unknown template variable '{name}' in {field}; the allow-list is "
                f"{sorted(TEMPLATE_VARIABLES)}"
            )


def _check_steps(steps: list[WorkflowStepIn]) -> None:
    """One step per channel, one step per position — both, or the rule sends twice (or fails)."""
    channels = [step.channel for step in steps]
    if len(set(channels)) != len(channels):
        raise ValueError(
            "one step per channel: two steps on the same channel carry two separate dedupe keys, "
            "so the guest receives the same message twice"
        )
    positions = [step.position for step in steps]
    if len(set(positions)) != len(positions):
        raise ValueError("each step needs its own position (they are unique within a workflow)")


class WorkflowStepIn(BaseModel):
    """One message of a rule: a ``kind`` of content, on one ``channel``, at one ``position``."""

    channel: ChannelName
    kind: Kind
    position: Annotated[int, Field(ge=0)] = 0


class WorkflowStepRead(BaseModel):
    """A stored step. Its ``id`` is half of the outbox dedupe key, so it is a stable identity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: ChannelName
    kind: str
    position: int


class WorkflowCreate(BaseModel):
    """Payload to author a rule. ``event_type_id`` ``None`` = every event type of the tenant."""

    name: ShortText
    trigger: WorkflowTriggerName
    offset_minutes: int = 0
    event_type_id: uuid.UUID | None = None
    active: bool = True
    steps: Annotated[list[WorkflowStepIn], Field(min_length=1)]

    @model_validator(mode="after")
    def _check(self) -> WorkflowCreate:
        check_offset(self.trigger, self.offset_minutes)
        _check_steps(self.steps)
        return self


class WorkflowUpdate(BaseModel):
    """Partial update. An omitted field is left alone; ``steps`` REPLACES the step list wholesale.

    ``event_type_id`` is nullable *and* optional: send ``null`` to widen the rule to every event
    type, omit it to leave the scope untouched (the service reads ``exclude_unset``, so the two are
    never confused).

    Offset coherence is checked by the SERVICE, on the merged rule — a PATCH carrying only
    ``{"trigger": "on_cancel"}`` is self-consistent and would still leave a stored ``-1440`` behind
    that the engine ignores for ever."""

    name: ShortText | None = None
    trigger: WorkflowTriggerName | None = None
    offset_minutes: int | None = None
    event_type_id: uuid.UUID | None = None
    active: bool | None = None
    steps: Annotated[list[WorkflowStepIn], Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def _check(self) -> WorkflowUpdate:
        if self.steps is not None:
            _check_steps(self.steps)
        return self


class WorkflowRead(BaseModel):
    """A rule as every read path returns it, steps included (they are the rule's substance)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    trigger: WorkflowTriggerName
    offset_minutes: int
    event_type_id: uuid.UUID | None
    active: bool
    steps: list[WorkflowStepRead]
    created_at: datetime
    updated_at: datetime


class WorkflowTemplateCreate(BaseModel):
    """The body a step renders for one ``(channel, kind, locale)``.

    ``subject`` is required for email and forbidden on the phone channels: WhatsApp and SMS have no
    such concept, so storing one would be a field nobody ever reads — and an email whose subject was
    quietly dropped arrives blank."""

    channel: ChannelName
    kind: Kind
    locale: Locale
    subject: ShortText | None = None
    body: Body

    @model_validator(mode="after")
    def _check(self) -> WorkflowTemplateCreate:
        check_subject(self.channel, self.subject)
        check_template_text(self.body, field="body")
        if self.subject is not None:
            check_template_text(self.subject, field="subject")
        return self


class WorkflowTemplateUpdate(BaseModel):
    """Partial update of a template's TEXT.

    The ``(channel, kind, locale)`` identity is immutable: changing it would silently re-point every
    step that resolves through this body. Delete it and write a new one instead."""

    subject: ShortText | None = None
    body: Body | None = None

    @model_validator(mode="after")
    def _check(self) -> WorkflowTemplateUpdate:
        if self.body is not None:
            check_template_text(self.body, field="body")
        if self.subject is not None:
            check_template_text(self.subject, field="subject")
        return self


class WorkflowTemplateRead(BaseModel):
    """A stored template."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: ChannelName
    kind: str
    locale: str
    subject: str | None
    body: str
    created_at: datetime
    updated_at: datetime


__all__ = [
    "CHANNEL_NAMES",
    "EVENT_SHAPED_TRIGGERS",
    "MAX_OFFSET_MINUTES",
    "SUBJECTLESS_CHANNELS",
    "SUPPORTED_LOCALES",
    "TEMPLATE_VARIABLES",
    "WORKFLOW_TRIGGER_NAMES",
    "ChannelName",
    "WorkflowCreate",
    "WorkflowRead",
    "WorkflowStepIn",
    "WorkflowStepRead",
    "WorkflowTemplateCreate",
    "WorkflowTemplateRead",
    "WorkflowTemplateUpdate",
    "WorkflowTriggerName",
    "WorkflowUpdate",
    "check_offset",
    "check_subject",
    "check_template_text",
]
