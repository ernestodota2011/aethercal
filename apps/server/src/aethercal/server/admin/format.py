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
from aethercal.server.db.models.outbox import OutboxStatus

if TYPE_CHECKING:
    # Imported for the ANNOTATIONS only; the runtime values are plain objects, so this module keeps
    # its "pure, no reflex/db dependency" property. ``aethercal.ui`` would otherwise trigger the
    # component's ``rx.asset`` side effect, and ``admin.service`` would drag SQLAlchemy and the ORM
    # models into what is meant to be a leaf of pure functions.
    from aethercal.server.admin.service import (
        AdminMetrics,
        BrandingRead,
        ConnectionRead,
        HostRead,
    )
    from aethercal.server.services.memberships import MemberRead
    from aethercal.ui import CalendarEvent, CalendarResource

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

# The outbox vocabulary, read from the MODEL rather than re-typed. The drain writes these states and
# the panel reports them; a literal copied by hand here would go on reading a reassuring 0 the day a
# status is renamed.
OUTBOX_PENDING = OutboxStatus.PENDING.value
OUTBOX_FAILED = OutboxStatus.FAILED.value
OUTBOX_DEAD = OutboxStatus.DEAD.value
OUTBOX_DELIVERED = OutboxStatus.DELIVERED.value

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


def booking_event(booking: BookingRead, *, resource_id: str | None = None) -> CalendarEvent:
    """Project a booking onto a calendar event for the admin agenda (F2-F).

    ``editable`` is ``True`` only for a CONFIRMED booking — the sole state the reschedule flow
    accepts — so the component offers drag/resize affordances only where a move can actually land; a
    non-confirmed booking renders as a muted, non-draggable chip.

    ``resource_id`` is the HOST's id, which is what puts the chip on a row of the RF-28 timeline. It
    is optional because the host lives on the EVENT TYPE, not on the booking: a booking whose event
    type has since been removed has no row, and the component surfaces it in an "unassigned" row
    rather than dropping it. The other four views have no resource dimension and ignore the key.
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
    if resource_id is not None:
        event["resourceId"] = resource_id
    return event


def host_resource(host: HostRead) -> CalendarResource:
    """Project a host onto one ROW of the RF-28 timeline. ==In this backend the resource IS the
    host== — the component itself is generic and would serve rooms or machines unchanged."""
    return {"id": str(host.id), "title": host.name}


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
        # The host is carried on the ROW because it is what the timeline needs to place a booking's
        # chip (RF-28): the booking knows its event type, and the event type knows its host.
        "host_id": str(event_type.host_id),
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


def _duration(seconds: float) -> str:
    """A waiting time a human reads at a glance: "3 h 12 min", not "11520.0"."""
    total = int(seconds)
    if total <= 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{total} s"


def metrics_rows(metrics: AdminMetrics) -> list[dict[str, str]]:
    """The health panel, as label/value rows (RF-25 / R9).

    ``outbox_due`` leads, and ``pending`` is reported beside it rather than as the headline: the
    outbox doubles as the durable scheduler, so a reminder queued for a booking three weeks out is
    ``pending`` and perfectly healthy. A panel that called that "backlog" would show a red number on
    a healthy business, and an alarm that cries wolf gets ignored — which is how the real one is
    missed.

    The oldest DUE age is the dead-man switch: flat while the drain lives, growing without bound
    from the moment it dies. Nothing else reports that death — the bookings keep confirming, and
    only the messages stop.
    """
    outbox = metrics.outbox_by_status
    return [
        {
            "label": "Mensajes vencidos sin enviar",
            "value": str(metrics.outbox_due),
            "hint": "Debería ser 0. Si crece, nada está despachando la cola.",
        },
        {
            "label": "El más antiguo lleva esperando",
            "value": _duration(metrics.outbox_oldest_due_age_seconds),
            "hint": "Si esto crece sin parar, el planificador está caído.",
        },
        {
            "label": "En cola (programados)",
            "value": str(outbox.get(OUTBOX_PENDING, 0)),
            "hint": "Normal: un recordatorio para dentro de tres semanas vive aquí.",
        },
        {
            "label": "Reintentando",
            "value": str(outbox.get(OUTBOX_FAILED, 0)),
            "hint": "Fallos transitorios; siguen vivos.",
        },
        {
            "label": "Descartados (agotaron reintentos)",
            "value": str(outbox.get(OUTBOX_DEAD, 0)),
            "hint": "Requieren una persona: aethercal-admin outbox replay.",
        },
        {
            "label": "Entregados",
            "value": str(outbox.get(OUTBOX_DELIVERED, 0)),
            "hint": "",
        },
        {
            "label": "Tasa de no-show",
            "value": f"{metrics.no_show_ratio:.0%}",
            # The denominator is SHOWN. A rate with no visible denominator is a number the operator
            # has to take on trust, and this is precisely the number they trusted: it used to count
            # every booking still in the diary, so it fell every time a new one was taken.
            "hint": (
                f"Sobre {metrics.appointments_expected} cita(s) que ya debían haber ocurrido. "
                "Las futuras y las canceladas no cuentan: a ninguna ha faltado nadie todavía."
            ),
        },
    ]


def host_row(host: HostRead) -> dict[str, str]:
    """Flatten a host into the string cells its table renders — and the host selector's options."""
    return {
        "id": str(host.id),
        "name": host.name,
        "email": host.email,
        "timezone": host.timezone,
    }


def member_row(member: MemberRead) -> dict[str, str]:
    """Flatten a member into the cells the members table renders (B-02).

    ``can_sign_in`` is not decoration. ==Every host that existed before this wave has no password==
    (``users.hashed_password`` was declared in migration 0001 and dead until now), so a members
    panel
    will legitimately list people who cannot get in yet. The owner has to SEE that — otherwise the
    first they hear of it is a colleague telling them the password does not work.
    """
    return {
        "id": str(member.id),
        "host_id": str(member.user_id),
        "name": member.name,
        "email": member.email,
        "role": member.role.value,
        "can_sign_in": "yes" if member.has_password else "no",
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


def branding_row(view: BrandingRead) -> dict[str, str]:
    """The branding form's five strings, flattened for a Reflex state var (B-07).

    ``registered_name`` rides along so the panel can SHOW the fallback ("currently shown as: Sol
    Holdings LLC") without pre-filling the ``public_name`` box with it — which would quietly turn a
    fallback into a value the operator then saved.
    """
    return {
        "public_name": view.public_name,
        "logo_url": view.logo_url,
        "accent_color": view.accent_color,
        "timezone": view.timezone,
        "registered_name": view.registered_name,
    }


__all__ = [
    "ALL_EVENT_TYPES",
    "DEFAULT_CALENDAR",
    "SHARED_SCHEDULE",
    "booking_event",
    "booking_row",
    "branding_row",
    "connection_row",
    "event_type_row",
    "host_resource",
    "host_row",
    "member_row",
    "metrics_rows",
    "parse_weekdays",
    "schedule_row",
    "template_row",
    "weekly_rules",
    "workflow_row",
]
