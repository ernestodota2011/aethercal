"""EventType request/response schemas (RF-14): the bookable-meeting API contract.

Durations cross the wire as **integer seconds**, mirroring the ``event_types`` DB columns exactly,
so the contract carries no unit ambiguity and no float rounding. The server-side service bridges
these seconds to the pure ``aethercal.core`` value objects (``timedelta`` / ``Buffer``) when the
slots engine needs them — this package stays a dependency-free contract.

Bounds are enforced here (Pydantic v2) so a malformed payload is rejected at the edge with a 422
before any handler or query runs:

* ``duration_seconds`` / ``max_advance_seconds`` — strictly positive.
* ``buffer_before_seconds`` / ``buffer_after_seconds`` / ``min_notice_seconds`` — non-negative.
* ``increment_seconds`` — optional; strictly positive when present.
* ``max_per_day`` — optional; at least 1 when present.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# Reusable constrained aliases keep the create/update models in lockstep on their bounds.
PositiveSeconds = Annotated[int, Field(gt=0)]
NonNegativeSeconds = Annotated[int, Field(ge=0)]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]
Slug = Annotated[str, Field(min_length=1, max_length=63)]


class EventTypeCreate(BaseModel):
    """Payload to create an EventType. Optional-with-default fields fall back to safe values."""

    host_id: uuid.UUID
    schedule_id: uuid.UUID
    slug: Slug
    title: ShortText
    description: str | None = None
    location: Annotated[str | None, Field(max_length=255)] = None
    duration_seconds: PositiveSeconds
    buffer_before_seconds: NonNegativeSeconds = 0
    buffer_after_seconds: NonNegativeSeconds = 0
    min_notice_seconds: NonNegativeSeconds = 0
    max_advance_seconds: PositiveSeconds
    increment_seconds: Annotated[int, Field(gt=0)] | None = None
    max_per_day: Annotated[int, Field(ge=1)] | None = None
    questions: list[Any] = Field(default_factory=list)
    active: bool = True


class EventTypeUpdate(BaseModel):
    """Partial update of an EventType — every field optional; only provided fields are applied.

    Use ``model_dump(exclude_unset=True)`` to get exactly the fields the caller sent. Bounds still
    apply to any field that IS provided.
    """

    host_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    slug: Slug | None = None
    title: ShortText | None = None
    description: str | None = None
    location: Annotated[str | None, Field(max_length=255)] = None
    duration_seconds: Annotated[int, Field(gt=0)] | None = None
    buffer_before_seconds: Annotated[int, Field(ge=0)] | None = None
    buffer_after_seconds: Annotated[int, Field(ge=0)] | None = None
    min_notice_seconds: Annotated[int, Field(ge=0)] | None = None
    max_advance_seconds: Annotated[int, Field(gt=0)] | None = None
    increment_seconds: Annotated[int, Field(gt=0)] | None = None
    max_per_day: Annotated[int, Field(ge=1)] | None = None
    questions: list[Any] | None = None
    active: bool | None = None


class EventTypeRead(BaseModel):
    """The EventType as returned by the API — built directly from the ORM row (from_attributes)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    host_id: uuid.UUID
    schedule_id: uuid.UUID
    slug: str
    title: str
    description: str | None
    location: str | None
    duration_seconds: int
    buffer_before_seconds: int
    buffer_after_seconds: int
    min_notice_seconds: int
    max_advance_seconds: int
    increment_seconds: int | None
    max_per_day: int | None
    questions: list[Any]
    active: bool


__all__ = ["EventTypeCreate", "EventTypeRead", "EventTypeUpdate"]
