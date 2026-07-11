"""Pure display/parse helpers for the admin UI (F1-11).

Reflex state vars are best kept to simple, JSON-serializable shapes, so read models are flattened to
``dict[str, str]`` rows here, and the weekly-schedule form inputs are parsed here too. Everything in
this module is a pure function with no Reflex or database dependency — the same reason the booking
page keeps its formatting in ``aethercal.booking.timefmt``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeRead
from aethercal.schemas.schedules import Rules, ScheduleRead, TimeRangeSchema

if TYPE_CHECKING:
    # Imported for the return type only; the runtime value is a plain dict, so this module keeps its
    # "pure, no reflex/db dependency" property (importing ``aethercal.ui`` at runtime triggers the
    # component's ``rx.asset`` side effect — kept out of this leaf helper module on purpose).
    from aethercal.ui import CalendarEvent

_MIN_WEEKDAY = 0
_MAX_WEEKDAY = 6

# A muted grey chip for a booking that is not an editable, confirmed slot (e.g. a pending one), so
# the calendar visibly distinguishes it from a live, draggable booking. Neutral by design — no
# lavender/cyan accent (the anti-AI-slop tell).
_NON_EDITABLE_COLOR = "#9ca3af"


def _wall_time_utc(moment: datetime) -> str:
    """Render an instant as a naive-UTC wall-time ISO string (``YYYY-MM-DDTHH:MM:SS``).

    The calendar core parses event times as LOCAL wall-time (never ``new Date(iso)`` UTC), and the
    admin's contract is that its times are UTC — so an aware instant is normalized to UTC and its
    tzinfo dropped, and a naive one (SQLite round-trips drop tzinfo) is taken as already-UTC. It is
    the inverse of the state handler stamping a naive ``datetime-local`` back as UTC.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.replace(microsecond=0).isoformat()


def booking_event(booking: BookingRead) -> CalendarEvent:
    """Project a booking onto a calendar event for the admin agenda (F2-F).

    ``editable`` is ``True`` only for a CONFIRMED booking — the sole state the reschedule flow
    accepts — so the component offers drag/resize affordances only where a move can actually land; a
    non-confirmed booking renders as a muted, non-draggable chip.
    """
    editable = booking.status is BookingStatus.CONFIRMED
    event: CalendarEvent = {
        "id": str(booking.id),
        "title": booking.guest_name,
        "start": _wall_time_utc(booking.start),
        "end": _wall_time_utc(booking.end),
        "editable": editable,
    }
    if not editable:
        event["color"] = _NON_EDITABLE_COLOR
    return event


def booking_row(booking: BookingRead) -> dict[str, str]:
    """Flatten a booking into the string cells the agenda table renders."""
    return {
        "id": str(booking.id),
        "start": booking.start.isoformat(),
        "guest": booking.guest_name,
        "email": booking.guest_email,
        "status": booking.status.value,
    }


def event_type_row(event_type: EventTypeRead) -> dict[str, str]:
    """Flatten an event type into the string cells its table renders (duration shown in minutes).

    ``title_en``/``description_en`` surface the current ``"en"`` override (A4), blank when none is
    stored — so re-listing after a save/edit is how the admin sees the EN translation "populated".
    """
    return {
        "id": str(event_type.id),
        "slug": event_type.slug,
        "title": event_type.title,
        "title_en": event_type.title_translations.get("en", ""),
        "description_en": event_type.description_translations.get("en", ""),
        "duration_min": str(event_type.duration_seconds // 60),
        "active": "yes" if event_type.active else "no",
    }


def schedule_row(schedule: ScheduleRead) -> dict[str, str]:
    """Flatten a schedule into string cells, listing its open weekdays as a sorted CSV."""
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "timezone": schedule.timezone,
        "weekdays": ",".join(str(day) for day in sorted(schedule.rules)),
    }


def parse_weekdays(raw: str) -> list[int]:
    """Parse a comma-separated weekday list (``"0,1,2"``, Monday=0..Sunday=6); ``""`` → ``[]``.

    Raises :class:`ValueError` (message mentions ``weekday``) on any non-integer or out-of-range
    token, so the caller can surface a clean validation message.
    """
    days: list[int] = []
    for token in raw.split(","):
        stripped = token.strip()
        if not stripped:
            continue
        try:
            day = int(stripped)
        except ValueError as exc:
            raise ValueError(f"weekday {stripped!r} is not a number 0-6") from exc
        if not _MIN_WEEKDAY <= day <= _MAX_WEEKDAY:
            raise ValueError(f"weekday {day} is out of range 0-6")
        days.append(day)
    return days


def weekly_rules(weekdays: list[int], start: str, end: str) -> Rules:
    """Build a schedule ``rules`` map applying a single ``[start, end)`` range to each weekday."""
    return {day: [TimeRangeSchema(start=start, end=end)] for day in weekdays}


__all__ = [
    "booking_event",
    "booking_row",
    "event_type_row",
    "parse_weekdays",
    "schedule_row",
    "weekly_rules",
]
