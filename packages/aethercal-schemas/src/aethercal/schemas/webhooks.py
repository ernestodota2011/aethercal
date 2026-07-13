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
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

WebhookEventName = Literal[
    "booking.created",
    "booking.cancelled",
    "booking.rescheduled",
    "booking.no_show",
]
"""The events an AetherCal webhook can fan out. Kept in lockstep with the booking lifecycle.

This ``Literal`` is the SINGLE source: :data:`WEBHOOK_EVENTS`, the subscription validator
(:data:`_EventList`), :class:`WebhookRead`, :class:`WebhookEnvelope` and the OpenAPI schema all
derive from it, so an event is added here and nowhere else.

``booking.no_show`` (RF-25) closes a real observability hole: a subscriber's CRM learned about a
cancellation and about a reschedule, but a guest who simply never turned up was invisible to it.
Widening the vocabulary needs NO data migration — ``Webhook.events`` is a JSON column, and nobody
starts receiving the new event by accident, because an existing subscriber cannot have subscribed to
an event that did not exist."""

WEBHOOK_EVENTS: tuple[WebhookEventName, ...] = get_args(WebhookEventName)
"""The allowed event names as a tuple, for iteration/validation by callers."""

WEBHOOK_API_VERSION = "1"
"""The envelope schema version. Bumped only on a breaking change to the outbound contract."""

_EventList = Annotated[list[WebhookEventName], Field(min_length=1)]
_Url = Annotated[str, Field(min_length=1, max_length=2048)]

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def _require_http_scheme(url: str) -> str:
    """Reject any non ``http``/``https`` webhook URL at registration (RF-17 / RNF-5).

    Scheme-only fast fail — the authoritative egress/IP check runs at send time in the delivery
    worker (:func:`aethercal.server.webhooks.ssrf.assert_target_allowed`), which is also where the
    operator's private-target allowlist is applied. Deliberately NOT here: a URL that is legal on
    one instance (an operator who declared their LAN) is illegal on another, so a registration-time
    address check would either lie to the self-hoster or hard-code one deployment's policy into the
    shared schema package.
    """
    if urlsplit(url).scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError("webhook url scheme must be http or https")
    return url


class WebhookCreate(BaseModel):
    """Request body to subscribe a new webhook.

    ``secret`` may be supplied by the caller; when omitted the server mints one and returns it once
    in :class:`WebhookCreated`.
    """

    url: _Url
    events: _EventList
    secret: str | None = None

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        """Enforce the ``http``/``https`` scheme on the subscription URL (RF-17 / RNF-5)."""
        return _require_http_scheme(value)


class WebhookUpdate(BaseModel):
    """Partial update of a subscription: toggle ``active``, or change ``url`` / ``events``.

    Every field is optional; an omitted field leaves that attribute unchanged.
    """

    url: _Url | None = None
    events: _EventList | None = None
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str | None) -> str | None:
        """Enforce the ``http``/``https`` scheme when a new URL is supplied (RF-17 / RNF-5)."""
        return value if value is None else _require_http_scheme(value)


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
