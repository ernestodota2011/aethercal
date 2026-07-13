"""Tenant-authored notification workflows (RF-24): the rule, its steps, and the message templates.

A **workflow** is a rule ("24 h before the start, remind the guest"); a **step** is one message on
one channel within that rule; a **template** is the body a step renders for a (channel, kind,
locale). The engine that materialises a booking's steps into outbox rows lands in Wave 1 — this
module only declares the tables so every worktree shares one schema.

Two deliberate choices:

* ``trigger``, ``kind`` and ``channel`` are plain ``VARCHAR`` columns, not ``sa.Enum``. Their Python
  vocabularies (``WorkflowTrigger`` in ``services/workflows.py``, ``NotificationKind`` in
  ``integrations/smtp/compose.py``, ``Channel`` in ``server/channels.py``) are the source of truth
  and are validated at the schema/service layer. A DB-level ``CHECK`` would force a migration every
  time a channel or a trigger is added, for a value already validated before it reaches the session
  — cost without safety.
* ``offset_minutes`` is SIGNED and relative to the trigger's anchor: ``-1440`` on ``before_start``
  is "24 h before the start". Storing the sign (rather than a magnitude plus a direction column)
  keeps the send time a single addition, which is exactly what the outbox's ``next_retry_at`` needs.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class WorkflowTrigger(StrEnum):
    """What fires a workflow (RF-24). The source of truth for the ``workflows.trigger`` value.

    It lives HERE, in the leaf model module, rather than in ``services/workflows.py`` (where the
    shared contract nominally places it) for one concrete reason: the outbox classifies a step's
    staleness BY TRIGGER (see ``services/outbox.py``), and the workflow *engine* will import the
    outbox in order to enqueue. Declaring the vocabulary inside the engine module would make that a
    real import cycle. A leaf enum has no such problem, and ``services/workflows.py`` re-exports it,
    so the contract's import path still holds for every consumer."""

    ON_BOOKING = "on_booking"
    BEFORE_START = "before_start"
    AFTER_END = "after_end"
    ON_CANCEL = "on_cancel"
    ON_NO_SHOW = "on_no_show"


class Workflow(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """One automation rule for a tenant (RF-24): when to fire, and for which event type."""

    __tablename__ = "workflows"

    # NULL = the rule applies to EVERY event type of the tenant. A concrete id scopes it to one.
    event_type_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("event_types.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    trigger: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # Signed minutes from the trigger's anchor: -1440 on ``before_start`` = 24 h before the start.
    offset_minutes: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), default=0, nullable=False
    )
    active: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("true"), default=True, nullable=False
    )

    __table_args__ = (sa.UniqueConstraint("tenant_id", "name"),)


class WorkflowStep(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """One message of a workflow: a ``kind`` of content on one ``channel`` (RF-24).

    ``position`` orders the steps within a workflow and is unique per workflow, so "the second step"
    is a stable identity that a template and an outbox dedupe key can both address."""

    __tablename__ = "workflow_steps"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    position: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), default=0, nullable=False
    )

    __table_args__ = (sa.UniqueConstraint("tenant_id", "workflow_id", "position"),)


class WorkflowTemplate(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """The body a step renders, per (channel, kind, locale) (RF-24).

    ``subject`` is NULL for the channels that have no subject (WhatsApp, SMS). The body is rendered
    by strict allow-list substitution of the documented ``{{variables}}`` — it is DATA, never
    instructions: no Jinja, no ``eval``, no arbitrary expression evaluation, so a tenant-authored
    template can never execute anything."""

    __tablename__ = "workflow_templates"

    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    subject: Mapped[str | None] = mapped_column(sa.String(255))
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "channel", "kind", "locale"),)


__all__ = ["Workflow", "WorkflowStep", "WorkflowTemplate", "WorkflowTrigger"]
