"""All ORM models. Importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from aethercal.server.db.models.booking import Booking, GuestToken
from aethercal.server.db.models.integrations import (
    BusyCache,
    ExternalCalendarLink,
    ExternalConnection,
)
from aethercal.server.db.models.notifications import SentNotification
from aethercal.server.db.models.outbox import Outbox, OutboxStatus
from aethercal.server.db.models.scheduling import DateOverride, EventType, Schedule
from aethercal.server.db.models.tenancy import ApiKey, MemberRole, Membership, Tenant, User
from aethercal.server.db.models.webhooks import Webhook, WebhookDelivery
from aethercal.server.db.models.workflows import (
    Workflow,
    WorkflowStep,
    WorkflowTemplate,
    WorkflowTrigger,
)

__all__ = [
    "ApiKey",
    "Booking",
    "BusyCache",
    "DateOverride",
    "EventType",
    "ExternalCalendarLink",
    "ExternalConnection",
    "GuestToken",
    "MemberRole",
    "Membership",
    "Outbox",
    "OutboxStatus",
    "Schedule",
    "SentNotification",
    "Tenant",
    "User",
    "Webhook",
    "WebhookDelivery",
    "Workflow",
    "WorkflowStep",
    "WorkflowTemplate",
    "WorkflowTrigger",
]
