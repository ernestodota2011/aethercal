"""Slots API schemas (RF-03/RF-16): the read-only availability contract.

The slots endpoint answers "when can a guest book this event type?" with a list of concrete,
bookable instants. Every instant crosses the wire as an absolute, timezone-aware UTC datetime (no
wall-time ambiguity); the requested display timezone is echoed back as ``timezone`` so a client can
localize for rendering without the server ever emitting a naive or non-UTC bound.

``availability`` reports how trustworthy the offered set is (RF-13 safe degradation):

* ``ok``          — the host's external (Google) busy set was known and complete for the window.
* ``degraded``    — a last-known (STALE) external busy copy was used; slots are still offered.
* ``unavailable`` — the external busy set could not be established; NO slots are offered rather than
  risk a double-booking (``slots`` is always empty in this case).

Pure transport DTOs: they check *shape*, not calendar semantics — the service and the pure
``aethercal.core`` engines own the date math.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# The trust level of an offered slot set (RF-13). Shared by the service result and this response so
# the machine vocabulary has a single source of truth.
Availability = Literal["ok", "degraded", "unavailable"]


class SlotRead(BaseModel):
    """A single bookable slot as a half-open ``[start, end)`` of absolute UTC instants (RF-03)."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime


class SlotsResponse(BaseModel):
    """Bookable slots for an event type over a window, plus the availability status (RF-03/RF-13).

    ``timezone`` echoes the caller's requested IANA display zone; the slot bounds are always UTC.
    """

    event_type_id: UUID
    timezone: str
    availability: Availability
    slots: list[SlotRead]


__all__ = ["Availability", "SlotRead", "SlotsResponse"]
