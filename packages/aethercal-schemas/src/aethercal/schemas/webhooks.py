"""Outbound-webhook request/response schemas and the v1 delivery envelope (RF-17).

This is AetherCal's OWN webhook contract, deliberately independent of cal.com's incoming shape (a
separate compatibility adapter translates outbound events for legacy consumers in F1-14). The
envelope every delivery carries is::

    {"event": "<name>", "api_version": "1", "timestamp": "<iso8601>", "data": {...}}

The per-subscriber ``secret`` is write-only: it is accepted on create (or generated) and returned
exactly once, but :class:`WebhookRead` never carries it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

WebhookEventName = Literal["booking.created", "booking.cancelled", "booking.rescheduled"]
"""The events an AetherCal webhook can fan out. Kept in lockstep with the booking lifecycle."""

WEBHOOK_EVENTS: tuple[WebhookEventName, ...] = get_args(WebhookEventName)
"""The allowed event names as a tuple, for iteration/validation by callers."""

WEBHOOK_API_VERSION = "1"
"""The envelope schema version. Bumped only on a breaking change to the outbound contract."""

_EventList = Annotated[list[WebhookEventName], Field(min_length=1)]
_Url = Annotated[str, Field(min_length=1, max_length=2048)]


class WebhookCreate(BaseModel):
    """Request body to subscribe a new webhook.

    ``secret`` may be supplied by the caller; when omitted the server mints one and returns it once
    in :class:`WebhookCreated`.
    """

    url: _Url
    events: _EventList
    secret: str | None = None


class WebhookUpdate(BaseModel):
    """Partial update of a subscription: toggle ``active``, or change ``url`` / ``events``.

    Every field is optional; an omitted field leaves that attribute unchanged.
    """

    url: _Url | None = None
    events: _EventList | None = None
    active: bool | None = None


class WebhookRead(BaseModel):
    """A subscription as returned by every read path. Never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    events: list[WebhookEventName]
    active: bool
    created_at: datetime
    updated_at: datetime


class WebhookCreated(WebhookRead):
    """The create response: a :class:`WebhookRead` plus the one-time plaintext ``secret``."""

    secret: str


class WebhookEnvelope(BaseModel):
    """The signed body of a single outbound delivery (AetherCal webhook contract v1)."""

    event: WebhookEventName
    api_version: str = WEBHOOK_API_VERSION
    timestamp: str
    data: dict[str, Any]


__all__ = [
    "WEBHOOK_API_VERSION",
    "WEBHOOK_EVENTS",
    "WebhookCreate",
    "WebhookCreated",
    "WebhookEnvelope",
    "WebhookEventName",
    "WebhookRead",
    "WebhookUpdate",
]
