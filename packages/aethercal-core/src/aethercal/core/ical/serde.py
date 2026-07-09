"""iCalendar (.ics) serialization for :class:`Event` with a guaranteed round-trip.

The ``icalendar`` library is only partially typed (several of its public methods —
``Component.add``, ``Component.walk`` and the ``VEVENT`` property accessors — carry
incomplete annotations). To keep this module strict-clean without weakening the
type checks on our own domain logic, every call into ``icalendar`` is contained
behind an explicit ``Any`` boundary and the extracted values are immediately
re-annotated with their concrete types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, tzinfo
from typing import Any, cast
from zoneinfo import ZoneInfo

from icalendar import Calendar, vDDDLists, vRecur
from icalendar import Event as VEvent

from aethercal.core.model import Event

PRODID = "-//AetherLogik//AetherCal//EN"
_VERSION = "2.0"
_UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "aethercal.aetherlogik.com")


def _iana_name(info: tzinfo | None) -> str:
    """Recover the IANA key from a tzinfo produced by icalendar (always a ZoneInfo)."""
    if isinstance(info, ZoneInfo):
        return info.key
    raise TypeError(f"expected a ZoneInfo tzinfo, got {info!r}")


def _rrule_to_str(value: Any) -> str | None:
    """Render a parsed RRULE property back to its RFC 5545 body string."""
    if isinstance(value, list):
        value = cast(Any, value[0]) if value else None
    if value is None:
        return None
    if isinstance(value, vRecur):
        return value.to_ical().decode("utf-8")
    raise TypeError(f"unexpected RRULE value: {value!r}")


def _collect_dates(value: Any) -> frozenset[datetime]:
    """Extract naive wall-time datetimes from an EXDATE/RDATE property.

    icalendar returns a single ``vDDDLists`` for one property line, or a ``list``
    of them when the same property appears more than once; handle both.
    """
    if value is None:
        return frozenset()
    date_lists: list[Any] = cast(list[Any], value) if isinstance(value, list) else [value]
    out: set[datetime] = set()
    for date_list in date_lists:
        if not isinstance(date_list, vDDDLists):
            raise TypeError(f"unexpected date-list value: {date_list!r}")
        for item in date_list.dts:
            moment = item.dt
            if not isinstance(moment, datetime):
                raise TypeError(f"expected a datetime in an EXDATE/RDATE, got {moment!r}")
            out.add(moment.replace(tzinfo=None))
    return frozenset(out)


def _stable_uid(event: Event) -> str:
    """A deterministic UID: equal Events yield the same UID (reproducible output)."""
    canonical = "\n".join(
        [
            event.dtstart.isoformat(),
            str(event.duration),
            event.timezone,
            event.rrule or "",
            ",".join(sorted(d.isoformat() for d in event.exdates)),
            ",".join(sorted(d.isoformat() for d in event.rdates)),
        ]
    )
    return f"{uuid.uuid5(_UID_NAMESPACE, canonical)}@aethercal"


def event_to_ics(event: Event) -> str:
    """Serialize an :class:`Event` to a VCALENDAR string containing one VEVENT."""
    zone = ZoneInfo(event.timezone)
    vevent: Any = VEvent()
    vevent.add("uid", _stable_uid(event))
    vevent.add("dtstart", event.dtstart.replace(tzinfo=zone))
    vevent.add("duration", event.duration)
    if event.rrule is not None:
        vevent.add("rrule", vRecur.from_ical(event.rrule))
    if event.exdates:
        vevent.add("exdate", [d.replace(tzinfo=zone) for d in sorted(event.exdates)])
    if event.rdates:
        vevent.add("rdate", [d.replace(tzinfo=zone) for d in sorted(event.rdates)])

    calendar: Any = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", _VERSION)
    calendar.add_component(vevent)
    ics: str = calendar.to_ical().decode("utf-8")
    return ics


def event_from_ics(data: str) -> Event:
    """Parse a VCALENDAR/VEVENT string back into an :class:`Event`."""
    calendar: Any = Calendar.from_ical(data)
    vevents: list[Any] = calendar.walk("VEVENT")
    if not vevents:
        raise ValueError("no VEVENT found in the iCalendar data")
    vevent: Any = vevents[0]

    start: datetime = vevent.DTSTART
    duration: timedelta = vevent.DURATION

    return Event(
        dtstart=start.replace(tzinfo=None),
        duration=duration,
        timezone=_iana_name(start.tzinfo),
        rrule=_rrule_to_str(vevent.get("rrule")),
        exdates=_collect_dates(vevent.get("exdate")),
        rdates=_collect_dates(vevent.get("rdate")),
    )
