"""Booking request/response schemas (F1-05, RF-07/RF-16): the booking API contract.

Pure transport DTOs — they check *shape* (types, bounds, a real IANA guest timezone), never
calendar semantics; the service and the pure ``aethercal.core`` engines own slot validity and the
anti-double-booking rules. A booking is requested with only its ``start``: the server derives
``end`` from the event type's duration, so the two can never disagree.

``BookingRead`` is built straight from the ORM row (``from_attributes``); the wire names ``start`` /
``end`` map from the columns ``start_at`` / ``end_at`` via ``validation_alias``, matching the slots
contract. The internal Google ``external_event_id`` is intentionally NOT exposed (RF-16); the
guest-facing ``meeting_url`` is.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aethercal.core.model import BookingStatus

# The IANA-zone rule, CONSUMED and re-exported (see ``__all__``) — this layer does not own it.
#
# "Is this a real timezone" is domain logic about time, so it lives in ``aethercal.core.tz``: a zone
# is a MEMBER of the set IANA publishes, not a filename that happens to open. It could not live
# here even if we wanted it to — ``core.model.Event`` and ``core.model.Schedule`` need the same
# rule, and ``core`` may not import ``schemas`` (the "core is pure" import contract). A rule owned
# by this layer is therefore a rule the domain must re-implement, which is exactly how four copies
# of it came to exist: three carrying the same broken ``except``, and two OS-dependent answers
# between them.
#
# The name stays exported here because it is what this layer's consumers already import — the guest
# booking contract below, ``server/services/users.py`` for the host's timezone, ``api/slots.py`` for
# the ``tz`` query param. They get the one function, not a copy of it.
from aethercal.core.tz import require_iana_zone

GuestName = Annotated[str, Field(min_length=1, max_length=255)]
GuestEmail = Annotated[str, Field(min_length=3, max_length=320)]
GuestTimezone = Annotated[str, Field(min_length=1, max_length=64)]
# The bound on the RAW submitted string — generous enough for the punctuation a human types
# (``+1 (305) 413-1728`` is 17 characters), tight enough that a hostile payload cannot hand the
# normalizer a megabyte to chew on. The REAL shape is enforced by ``_E164`` after normalization:
# a ``+``, a non-zero country digit, then up to 14 more — 15 digits max, so 16 characters, which
# ``bookings.guest_phone`` (``String(20)``) can never overflow.
GuestPhone = Annotated[str, Field(min_length=2, max_length=32)]

#: The canonical E.164 form — the ONLY shape that reaches the database.
#:
#: ``[0-9]``, never ``\d``: in Python ``\d`` matches every Unicode decimal digit, so it would wave
#: through a number whose body is written in Arabic-Indic (U+0660-U+0669), Devanagari (U+0966-
#: U+096F) or fullwidth (U+FF10-U+FF19) numerals as a "valid E.164 number". Those are not phone
#: numbers. They would be stored, and then handed to the WhatsApp/SMS provider, which rejects them
#: — or, worse, interprets them as some other number. E.164 is ASCII digits, and this pattern now
#: says so. (The cases live in ``test_normalize_phone_rejects_anything_that_is_not_e164``.)
#:
#: Matched with ``fullmatch``, never ``match``: Python's ``$`` also matches BEFORE a trailing
#: newline, so ``re.match(r"...$", "+13054131728\n")`` SUCCEEDS. Today the punctuation strip below
#: happens to remove that newline first — which means the anchor is already unsound and only a
#: refactor away from being exploitable. ``fullmatch`` anchors both ends with no such exception.
_E164 = re.compile(r"\+[1-9][0-9]{1,14}")
#: Punctuation a human sprinkles through a phone number. It is stripped before matching; anything
#: left over that is not an ASCII digit (a letter, a Unicode numeral, an "ext", a second ``+``)
#: makes the number INVALID rather than being quietly discarded — deleting characters until a
#: string parses is how you end up messaging a stranger's phone.
_PHONE_PUNCTUATION = re.compile(r"[\s\-().]")


def normalize_phone(value: str) -> str | None:
    """Canonicalize a human-typed phone to E.164, or ``None`` if it simply is not one.

    Formatting is forgiven (``+1 (305) 413-1728`` → ``+13054131728``); a missing country code is
    NOT. A bare ``3054131728`` could belong to a dozen countries, and guessing one means a
    WhatsApp message sent to a stranger. The caller decides what ``None`` means: the booking form
    turns it into a friendly field error, the API contract into a 422.

    The result is guaranteed ASCII (see ``_E164``): a string that merely *looks* like digits to a
    human, but is not the digits a phone network understands, never reaches the database.
    """
    candidate = _PHONE_PUNCTUATION.sub("", value.strip())
    return candidate if _E164.fullmatch(candidate) else None


def require_emailish(value: str) -> str:
    """A light structural check (a single ``@`` with non-empty local/domain, no spaces), trimmed.

    Deliberately not full RFC 5322 validation (no extra dependency): the transactional email either
    reaches its recipient or does not — a stricter gate belongs to the sending layer, not this
    contract.

    Public for the same reason as :func:`require_iana_zone`: the host's address — the one every
    confirmation is copied to — is now held to the rule the guest's has been held to all along. The
    ``ValueError`` names the guest's field because that is this module's own contract; the host
    service catches it and words its own refusal.
    """
    candidate = value.strip()
    local, _, domain = candidate.partition("@")
    if not local or not domain or " " in candidate or candidate.count("@") != 1:
        raise ValueError("guest_email is not a valid email address")
    return candidate


class GuestBookingBase(BaseModel):
    """Everything a GUEST supplies to book a slot — and every rule those values are held to.

    ==Extracted so there is exactly ONE of it.== Two request bodies now open a booking: this
    contract's :class:`BookingCreate` (authenticated — the tenant's own API key, naming the event
    type by id) and the PUBLIC router's ``PublicBookingCreate`` (unauthenticated — the event type
    comes from the ROUTE, as ``(tenant_slug, event_slug)``, and a captcha token comes with it).

    They differ in *how the appointment is identified*, and in nothing else. Copying the guest
    fields
    into a second model would have copied the four validators with them — the E.164 normalizer, the
    IANA-zone rule, the e-mail check, and the refusal of a consent that names no number — and the
    copies would then have drifted, on the endpoint with no authentication in front of it. The rules
    live here, once, and both bodies inherit them.

    ``guest_phone`` / ``guest_phone_consent`` carry RF-24's consent box across the wire. The consent
    is a **boolean whoever fills the form actively set**, not a timestamp the client invents: the
    SERVER stamps ``bookings.guest_phone_consent_at`` from its own clock, so a client can neither
    back-date a tick nor forge one for a booking it did not just make.

    ⚠️ ``guest_phone_consent`` means "the box on the form was ticked" — **not** "the owner of this
    number agreed". The number is typed into a PUBLIC form by whoever is booking, and nothing here
    verifies they possess it. Verifying possession is a declared gap (``docs/phone-channels.md``).
    """

    start: datetime
    guest_name: GuestName
    guest_email: GuestEmail
    guest_timezone: GuestTimezone
    guest_notes: Annotated[str | None, Field(max_length=2000)] = None
    answers: dict[str, Any] | None = None
    locale: Annotated[str | None, Field(max_length=16)] = None
    #: The phone typed into the booking form, in E.164 — ``None`` when none was given. Booking
    #: without one always works. Whoever is booking typed it in; nobody verified they own it.
    guest_phone: GuestPhone | None = None
    #: Whether the consent box was EXPLICITLY ticked on the form. Defaults to ``False``: it is
    #: opted INTO, never assumed, never pre-ticked. It records a TICK — not verified permission
    #: from the number's owner (see the class docstring, and ``docs/phone-channels.md``).
    guest_phone_consent: bool = False

    @field_validator("guest_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return require_iana_zone(value)

    @field_validator("guest_email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return require_emailish(value)

    @field_validator("guest_phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_phone(value)
        if normalized is None:
            raise ValueError(
                "guest_phone must be an E.164 number including the country code (e.g. +13054131728)"
            )
        return normalized

    @model_validator(mode="after")
    def _consent_needs_a_number(self) -> Self:
        """Refuse consent that references no number — it consents to nothing, and cannot be proven.

        Accepting it would write ``guest_phone_consent_at`` onto a row with no phone: a stamp
        asserting the guest agreed to be messaged somewhere we cannot name. The inverse (a number
        with no consent) is legitimate and stays allowed — the outbox gate is built to refuse it.
        """
        if self.guest_phone_consent and self.guest_phone is None:
            raise ValueError("guest_phone_consent requires a guest_phone to consent about")
        return self


class BookingCreate(GuestBookingBase):
    """Request body to book a slot with the tenant's API key (RF-07).

    Only ``start`` is sent; ``end`` is server-derived from the event type's duration, so the two can
    never disagree. The appointment is named by ``event_type_id`` — which is the authenticated
    contract's way of saying it, and is exactly what the PUBLIC body does NOT have: there, the
    business and the event type come from the route, because a guest holds no ids and because a body
    field naming the event type beside a route that already names it would be two sources of truth
    for one fact.
    """

    event_type_id: uuid.UUID


class BookingReschedule(BaseModel):
    """Request body to reschedule a booking to a new start (RF-07)."""

    new_start: datetime


class BookingRead(BaseModel):
    """A booking as returned by every read/write path — built from the ORM row (from_attributes).

    ``start`` / ``end`` map from the ``start_at`` / ``end_at`` columns; the internal
    ``external_event_id`` is never exposed (RF-16).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type_id: uuid.UUID
    start: datetime = Field(validation_alias="start_at")
    end: datetime = Field(validation_alias="end_at")
    status: BookingStatus
    guest_name: str
    guest_email: str
    guest_timezone: str
    guest_notes: str | None
    answers: dict[str, Any]
    meeting_url: str | None
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    created_at: datetime


__all__ = [
    "BookingCreate",
    "BookingRead",
    "BookingReschedule",
    "GuestBookingBase",
    "normalize_phone",
    "require_emailish",
    "require_iana_zone",
]
