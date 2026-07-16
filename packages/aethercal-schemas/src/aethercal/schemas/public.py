"""The PUBLIC contract — what an ANONYMOUS caller may send, and the very little they are told back.

Every model here exists because reusing the authenticated one would have leaked something. That is
the whole design rule of this module, and it is worth stating before the code:

* :class:`PublicEventTypeRead` is not ``EventTypeRead``. That model carries ``tenant_id``,
  ``host_id`` and ``schedule_id`` — the internal identifiers of a business, handed to strangers, for
  no reason at all. A guest choosing a time needs a slug, a title, a description, a duration and the
  questions they are about to be asked. Nothing else is theirs to see.
* :class:`PublicBookingRead` is not ``BookingRead``. ==That model is a PII dump==: ``guest_name``,
  ``guest_email``, ``guest_notes``, ``answers``. Echoed from an endpoint with no authentication in
  front of it, it turns a booking id into an oracle for somebody else's personal data. The public
  answer is ``{id, start, end, status}``, and the ``id`` is there only so the page can show a
  confirmation — and so a later cut can resume a checkout against it.
* :class:`PublicBookingCreate` is not ``BookingCreate``. It carries **no** ``event_type_id``: the
  business and the event type are in the ROUTE (``/public/{tenant_slug}/{event_slug}/bookings``),
  and a body field naming the appointment beside a route that already names it is two sources of
  truth for one fact — which is precisely how a booking ends up in the wrong diary. It carries
  instead the one thing the authenticated body never needs: a **captcha token**.

The guest's own fields, and the four rules they are held to, are NOT re-declared here: they are
inherited from :class:`~aethercal.schemas.bookings.GuestBookingBase`. Two copies of the E.164
normalizer would eventually have disagreed, and the copy that gave way would have been the one on
the endpoint anybody can call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import GuestBookingBase
from aethercal.schemas.slots import Availability, SlotRead


class PublicEventTypeRead(BaseModel):
    """A bookable event type, as an anonymous guest sees it.

    Built from the ORM row (``from_attributes``) but never from ``EventTypeRead``'s field set — see
    the module docstring. ``active`` is absent on purpose: the public listing only ever contains
    active event types, so a flag that is always ``true`` would be noise with a footgun attached.

    ``collects_phone`` is not a column: the server derives it per request from the tenant's ACTIVE
    WhatsApp/SMS rules. It is what tells the page whether to ask for a phone number at all — a
    question no guest should be asked when nothing on this instance would ever message them.
    """

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    description: str | None
    title_translations: dict[str, str] = Field(default_factory=dict)
    description_translations: dict[str, str] = Field(default_factory=dict)
    location: str | None
    duration_seconds: int
    questions: list[Any]
    collects_phone: bool = False


class PublicSlotsResponse(BaseModel):
    """The bookable slots for one public event type over a window (RF-03/RF-13).

    Keyed by ``event_slug``, not by an id: the id is how the *authenticated* API names an event
    type, and an anonymous caller has no business holding one. ``timezone`` echoes the requested
    IANA display zone; the slot bounds themselves are always absolute UTC instants.
    """

    event_slug: str
    timezone: str
    availability: Availability
    slots: list[SlotRead]


class PublicBookingCreate(GuestBookingBase):
    """The body of an UNAUTHENTICATED booking. The appointment is named by the ROUTE, not by this.

    ``turnstile_token`` is the widget's response, forwarded for server-side verification against
    Cloudflare. It is ``str | None`` rather than required, so a MISSING token is answered by the
    endpoint's own guard — one refusal, ``403 captcha_required``, the same shape a token that
    FAILED verification gets — instead of a 422 naming the field a bot should start guessing at.
    """

    turnstile_token: Annotated[str | None, Field(max_length=2048)] = None


class PublicBookingRead(BaseModel):
    """What the public POST answers with. ==Five fields, and the fifth carries no PII.==

    Not ``BookingRead``. That model would echo the guest's name, e-mail, notes and answers back out
    of an endpoint that asked for no credentials — and the day somebody points it at a booking id
    they did not create, it answers them with a stranger's personal data.

    ``meeting_url`` is deliberately absent too, and it is the one omission with a cost: the
    confirmation page can no longer show the meeting link inline. It reaches the guest in the
    confirmation e-mail instead — the channel that proves they own the address they typed. A
    public endpoint that hands out a meeting link keyed only by a booking id is a public endpoint
    that hands out meeting links.

    ``checkout_url`` (B-05b) is the fifth, and it is the ONE field a hold adds: where to send the
    guest to pay. It is ``None`` for a free booking (confirmed on the spot, nothing to pay) and it
    carries no personal data — it is a payment-redirect URL, not a booking dump. A PAID booking
    comes back ``status = pending`` with this set; the arbiter confirms it once the payment lands.
    """

    # ``populate_by_name`` so this ONE model works both directions without a hand-rolled remap:
    # the SERVER builds it from the ORM row, whose attributes are ``start_at`` / ``end_at`` (the
    # aliases); the SDK re-parses the WIRE body, keyed by the field names ``start`` / ``end``.
    # Without it the SDK would need its own key-remapper (the ``BookingRead`` path has one) — a
    # second thing to keep in step with these names, and the day they drifted the public booking
    # client would silently fail to parse a perfectly good response.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    start: datetime = Field(validation_alias="start_at")
    end: datetime = Field(validation_alias="end_at")
    status: BookingStatus
    #: Where to send the guest to pay (a hold), or ``None`` for a free booking. Not PII.
    checkout_url: str | None = None


__all__ = [
    "PublicBookingCreate",
    "PublicBookingRead",
    "PublicEventTypeRead",
    "PublicSlotsResponse",
]
