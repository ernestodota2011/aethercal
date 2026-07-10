"""All ORM models. Importing this package registers every MVP table on ``Base.metadata``."""

from __future__ import annotations

from aethercal.server.db.models.booking import Booking, GuestToken
from aethercal.server.db.models.integrations import (
    BusyCache,
    ExternalCalendarLink,
    ExternalConnection,
)
from aethercal.server.db.models.notifications import SentNotification
from aethercal.server.db.models.outbox import Outbox
from aethercal.server.db.models.scheduling import DateOverride, EventType, Schedule
from aethercal.server.db.models.tenancy import ApiKey, Tenant, User
from aethercal.server.db.models.webhooks import Webhook, WebhookDelivery

__all__ = [
    "ApiKey",
    "Booking",
    "BusyCache",
    "DateOverride",
    "EventType",
    "ExternalCalendarLink",
    "ExternalConnection",
    "GuestToken",
    "Outbox",
    "Schedule",
    "SentNotification",
    "Tenant",
    "User",
    "Webhook",
    "WebhookDelivery",
]
