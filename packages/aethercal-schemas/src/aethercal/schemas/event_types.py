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

``title``/``description`` are the canonical fallback (the tenant's base-locale text).
``title_translations``/``description_translations`` hold only sparse per-locale overrides, e.g.
``{"en": "Discovery call"}`` — a locale with no entry falls back to the canonical text. Both maps
are validated against :data:`SUPPORTED_TRANSLATION_LOCALES` so a bad locale key is rejected at the
edge (422) rather than silently ignored downstream. ``resolve_title``/``resolve_description`` below
are the single place that walks override → canonical fallback; consumers (booking, admin) should
use them instead of reaching into the maps directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Any, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Reusable constrained aliases keep the create/update models in lockstep on their bounds.
PositiveSeconds = Annotated[int, Field(gt=0)]
NonNegativeSeconds = Annotated[int, Field(ge=0)]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]
Slug = Annotated[str, Field(min_length=1, max_length=63)]

# The locales the platform currently has translated chrome for. Extend this set to add a locale —
# every ``*_translations`` map (Create/Update) is validated against it, so an unsupported key is
# rejected at the edge (422) instead of silently stored and never surfaced anywhere.
SUPPORTED_TRANSLATION_LOCALES: frozenset[str] = frozenset({"es", "en"})


def _check_translation_locales(value: dict[str, str] | None) -> dict[str, str] | None:
    """Reject any key outside :data:`SUPPORTED_TRANSLATION_LOCALES`; ``None``/empty pass through."""
    if not value:
        return value
    invalid = sorted(set(value) - SUPPORTED_TRANSLATION_LOCALES)
    if invalid:
        raise ValueError(
            f"unsupported translation locale(s) {invalid}; "
            f"supported locales are {sorted(SUPPORTED_TRANSLATION_LOCALES)}"
        )
    return value


class EventTypeCreate(BaseModel):
    """Payload to create an EventType. Optional-with-default fields fall back to safe values."""

    host_id: uuid.UUID
    schedule_id: uuid.UUID
    slug: Slug
    title: ShortText
    description: str | None = None
    title_translations: dict[str, str] = Field(default_factory=dict)
    description_translations: dict[str, str] = Field(default_factory=dict)
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

    @field_validator("title_translations", "description_translations")
    @classmethod
    def _validate_translation_locales(cls, value: dict[str, str]) -> dict[str, str]:
        result = _check_translation_locales(value)
        assert result is not None  # Create's maps are never None, only possibly empty.
        return result


class EventTypeUpdate(BaseModel):
    """Partial update of an EventType — every field optional; only provided fields are applied.

    Use ``model_dump(exclude_unset=True)`` to get exactly the fields the caller sent. Bounds still
    apply to any field that IS provided. ``title_translations``/``description_translations`` follow
    the same optional-and-unset pattern as every other field: omitting them from the payload leaves
    the stored map untouched (the service only assigns keys ``exclude_unset=True`` surfaces).
    """

    host_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    slug: Slug | None = None
    title: ShortText | None = None
    description: str | None = None
    title_translations: dict[str, str] | None = None
    description_translations: dict[str, str] | None = None
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

    @field_validator("title_translations", "description_translations")
    @classmethod
    def _validate_translation_locales(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _check_translation_locales(value)


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
    title_translations: dict[str, str] = Field(default_factory=dict)
    description_translations: dict[str, str] = Field(default_factory=dict)
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
    #: Whether an ACTIVE WhatsApp/SMS rule governs this event type — i.e. whether a phone number
    #: given here would ever actually be messaged (RF-24). This is NOT a column: the server derives
    #: it per request from the tenant's workflow rules
    #: (``services/workflow_rules.phone_channel_scope``), because the answer changes the moment a
    #: rule is switched on or off.
    #:
    #: The booking page asks for a phone + consent ONLY when this is true. The default is ``False``
    #: — deliberately the safe direction: a server that failed to compute it collects NO personal
    #: data, rather than harvesting phone numbers nothing will ever send to (RNF-8: minimal data).
    collects_phone: bool = False


@overload
def resolve_translation(canonical: str, translations: Mapping[str, str], locale: str) -> str: ...


@overload
def resolve_translation(
    canonical: str | None, translations: Mapping[str, str], locale: str
) -> str | None: ...


def resolve_translation(
    canonical: str | None, translations: Mapping[str, str], locale: str
) -> str | None:
    """==THE rule==: a non-blank per-locale override wins; otherwise the canonical text.

    This exists as a primitive over *values* — rather than only as a resolver over
    :class:`EventTypeRead` — because the same question ("which title do we show this person?") is
    asked from two places holding two different shapes: the booking page holds an
    ``EventTypeRead``, and the email composer holds the SQLAlchemy ORM row.

    When the only resolver took an ``EventTypeRead``, the composer wrote its own second answer —
    a bare ``event_type.title`` — and that answer ignored the translations for an entire release:
    every English mail carried a Spanish title, and the ``.ics`` SUMMARY carried it into the guest's
    calendar permanently. One rule, reachable from both shapes, is the fix for that class of bug.

    (A ``Protocol`` would be the tidier way to say "any event-type-ish thing", but a protocol
    member is matched against the DECLARED class attribute, and SQLAlchemy declares ``Mapped[str]``
    — so the ORM row, the very shape this needs to admit, would not satisfy it. Values it is.)

    A present-but-empty override is treated as *no* override: a blank string is never a meaningful
    thing to show a booker, and it is exactly what an admin form submits for "I left this alone".
    """
    return translations.get(locale) or canonical


def resolve_title(event: EventTypeRead, locale: str) -> str:
    """Return the title for ``locale``: the per-locale override, or the canonical fallback."""
    return resolve_translation(event.title, event.title_translations, locale)


def resolve_description(event: EventTypeRead, locale: str) -> str | None:
    """Return the description for ``locale``: the per-locale override, or the canonical fallback.

    Unlike ``resolve_title``, the canonical fallback (``event.description``) may itself be ``None``
    — an EventType with no description in any locale legitimately has none to show.
    """
    return resolve_translation(event.description, event.description_translations, locale)


__all__ = [
    "SUPPORTED_TRANSLATION_LOCALES",
    "EventTypeCreate",
    "EventTypeRead",
    "EventTypeUpdate",
    "resolve_description",
    "resolve_title",
    "resolve_translation",
]
