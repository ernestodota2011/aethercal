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
from aethercal.schemas.workflows import WorkflowRead, WorkflowTemplateRead

if TYPE_CHECKING:
    # Imported for the ANNOTATIONS only; the runtime values are plain objects, so this module keeps
    # its "pure, no reflex/db dependency" property. ``aethercal.ui`` would otherwise trigger the
    # component's ``rx.asset`` side effect, and ``admin.service`` would drag SQLAlchemy and the ORM
    # models into what is meant to be a leaf of pure functions.
    from aethercal.server.admin.service import ConnectionRead, HostRead
    from aethercal.ui import CalendarEvent

_MIN_WEEKDAY = 0
_MAX_WEEKDAY = 6

SHARED_SCHEDULE = "(business)"
"""What a schedule's owner cell reads when ``user_id`` is NULL: the pattern is the BUSINESS's, and
every host may use it (RF-30). It is also the sentinel the owner ``select`` submits, so the handler
can tell "shared" (a value) from "leave the owner alone" (an absence)."""

DEFAULT_CALENDAR = "(the account's default)"
"""Where a connection's bookings go when no calendar has been designated. Spelled out, because "no
target" is not "no calendar" — it means the connected account's OWN default calendar, which for a
personal account is exactly the one the bookings should not be landing in."""

ALL_EVENT_TYPES = "(all)"
"""What a rule's ``scope`` cell reads when ``event_type_id`` is NULL — it governs EVERY event type.

Spelled out rather than left blank: an empty cell reads as "unset / broken", and this is the rule's
widest and most consequential setting. It is also the sentinel the scope ``select`` submits, so the
handler can tell "every event type" (a value) from "leave the scope alone" (an absence)."""

_PREVIEW_CHARS = 60

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
    """Flatten a schedule into string cells, listing its open weekdays as a sorted CSV.

    ``owner`` is the host whose pattern this is, or :data:`SHARED_SCHEDULE` when it belongs to the
    BUSINESS and every host may use it (RF-30). Rendered as a word rather than a blank, because
    "shared with everyone" is a decision and an empty cell reads as one nobody made — and it is the
    difference between a pattern two hosts share on purpose and two hosts sharing one by accident.
    """
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "timezone": schedule.timezone,
        "weekdays": ",".join(str(day) for day in sorted(schedule.rules)),
        "owner": str(schedule.user_id) if schedule.user_id else SHARED_SCHEDULE,
    }


def host_row(host: HostRead) -> dict[str, str]:
    """Flatten a host into the string cells its table renders — and the host selector's options."""
    return {
        "id": str(host.id),
        "name": host.name,
        "email": host.email,
        "timezone": host.timezone,
    }


def connection_row(connection: ConnectionRead) -> dict[str, str]:
    """Flatten one of a host's connected calendar accounts.

    ``calendar`` says where this connection's bookings are WRITTEN. :data:`DEFAULT_CALENDAR` is not
    a blank: it is what happens when nothing has been designated — the account's own default
    calendar — and the operator needs to see that it is the current answer, because it is the one
    thing they are meant to change (bookings belong in a dedicated calendar, not a personal one).
    """
    return {
        "id": str(connection.id),
        "account": connection.account_email,
        "calendar": connection.booking_calendar_id or DEFAULT_CALENDAR,
    }


def workflow_row(workflow: WorkflowRead) -> dict[str, str]:
    """Flatten a rule into the string cells its table renders.

    ``steps`` is rendered as ``channel:kind`` pairs because a rule's substance IS its steps — listed
    by name and trigger alone, a rule that messages the guest looks identical to one that does
    nothing at all. ``scope`` names the event type it governs; :data:`ALL_EVENT_TYPES` means every
    one of them, which is a real value (``event_type_id`` is nullable) rather than an absence.
    """
    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "trigger": workflow.trigger,
        "offset_min": str(workflow.offset_minutes),
        "scope": str(workflow.event_type_id) if workflow.event_type_id else ALL_EVENT_TYPES,
        "steps": ", ".join(f"{step.channel}:{step.kind}" for step in workflow.steps),
        "active": "yes" if workflow.active else "no",
    }


def template_row(template: WorkflowTemplateRead) -> dict[str, str]:
    """Flatten a template into the string cells its table renders.

    The body is PREVIEWED for the table only: it is operator-authored, guest-facing text of up to
    4000 characters, and pasting all of it into a cell makes the table unreadable. The edit form
    round-trips the full body.
    """
    return {
        "id": str(template.id),
        "channel": template.channel,
        "kind": template.kind,
        "locale": template.locale,
        "subject": template.subject or "",
        "body": _preview(template.body),
    }


def _preview(body: str) -> str:
    """The first line of a body, capped: enough to recognise it, never enough to flood a table."""
    lines = body.strip().splitlines()
    first = lines[0] if lines else ""
    if len(first) <= _PREVIEW_CHARS:
        return first
    return first[: _PREVIEW_CHARS - 1].rstrip() + "…"


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
    "ALL_EVENT_TYPES",
    "DEFAULT_CALENDAR",
    "SHARED_SCHEDULE",
    "booking_event",
    "booking_row",
    "connection_row",
    "event_type_row",
    "host_row",
    "parse_weekdays",
    "schedule_row",
    "template_row",
    "weekly_rules",
    "workflow_row",
]
