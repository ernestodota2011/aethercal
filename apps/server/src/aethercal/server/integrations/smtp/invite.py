"""Booking calendar invite (.ics) builder for transactional emails (RF-08).

The core :mod:`aethercal.core.ical.serde` round-trips an ``Event`` (a recurrence master) but has no
notion of a *summary*, *organizer*, or *attendee* — the three properties a booking **invite** must
carry — and its ``Event`` model cannot represent them, so this builds the invite directly with the
same ``icalendar`` library rather than bending ``serde`` out of shape. The library is only partially
typed, so (exactly as ``serde`` does) every call into it is contained behind an ``Any`` boundary.

The output is deterministic: the same booking yields byte-identical ``.ics`` (``DTSTAMP`` is pinned
to the event start), mirroring the reproducible-output philosophy of ``serde``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from icalendar import Calendar, vCalAddress, vText
from icalendar import Event as VEvent

from aethercal.core.ical import PRODID

_VERSION = "2.0"
_METHOD = "REQUEST"


def booking_invite_ics(  # noqa: PLR0913 - the invite's iCalendar properties ARE the interface
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: datetime,
    organizer_name: str,
    organizer_email: str,
    attendee_name: str,
    attendee_email: str,
    description: str | None = None,
) -> str:
    """Serialize a single-VEVENT ``REQUEST`` invite for a booking to an RFC 5545 string.

    ``start`` / ``end`` should be timezone-aware; a UTC instant is emitted in the ``Z`` form. The
    organizer is the host and the single attendee is the guest (RF-08).
    """
    vevent: Any = VEvent()
    vevent.add("uid", uid)
    vevent.add("dtstamp", start)
    vevent.add("dtstart", start)
    vevent.add("dtend", end)
    vevent.add("summary", summary)
    if description:
        vevent.add("description", description)
    vevent.add("organizer", _address(organizer_name, organizer_email, role=None))
    vevent.add("attendee", _address(attendee_name, attendee_email, role="REQ-PARTICIPANT"))

    calendar: Any = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", _VERSION)
    calendar.add("method", _METHOD)
    calendar.add_component(vevent)
    ics: str = calendar.to_ical().decode("utf-8")
    return ics


def _address(name: str, email: str, *, role: str | None) -> Any:
    """Build a ``CAL-ADDRESS`` (``mailto:``) with a common-name (and optional role) parameter."""
    address: Any = vCalAddress(f"mailto:{email}")
    address.params["cn"] = vText(name)
    if role is not None:
        address.params["role"] = vText(role)
    return address


__all__ = ["booking_invite_ics"]
