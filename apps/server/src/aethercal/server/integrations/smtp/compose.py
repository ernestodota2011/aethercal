"""Pure composition of the transactional booking emails (RF-08) and the 24 h reminder (RF-10).

:func:`build_notification_email` is a pure function of a :class:`BookingEmailContext` plus a kind
and locale: it returns a fully-formed :class:`email.message.EmailMessage` with a localized subject
and plain-text body (cancel/reschedule/meeting links included) and the booking's ``.ics`` invite
attached as ``text/calendar``. Spanish is the primary locale and English the alternative (RNF-1); an
unrecognized locale falls back to Spanish. Nothing here touches the network, a clock, or a database
— the notification service supplies every value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from enum import StrEnum
from zoneinfo import ZoneInfo

from aethercal.server.integrations.smtp.invite import booking_invite_ics


class NotificationKind(StrEnum):
    """The four booking notifications (their value is the ``SentNotification.kind`` ledger key)."""

    CONFIRMATION = "confirmation"
    CANCELLATION = "cancellation"
    RESCHEDULE = "reschedule"
    REMINDER = "reminder"


# The host is copied on the lifecycle events they care about (someone booked / changed / cancelled);
# a 24 h reminder is for the guest alone.
_HOST_IS_COPIED = frozenset(
    {NotificationKind.CONFIRMATION, NotificationKind.CANCELLATION, NotificationKind.RESCHEDULE}
)


# The iTIP method + VEVENT status + SEQUENCE per kind, so the ``.ics`` matches the kind (RF-08).
# All kinds share the same stable UID (derived per booking), so cancel/reschedule reference the
# very event the confirmation created: confirmation ADDs it; reschedule UPDATEs it (same UID,
# bumped sequence); cancellation CANCELs it (bumped again). The reminder re-states the still-
# confirmed event, so it mirrors the confirmation. Successive reschedules share sequence 1 — a
# fully monotonic per-booking counter would need to be persisted on the booking (out of scope;
# documented residual).
_ICS_LIFECYCLE: Mapping[NotificationKind, tuple[str, str, int]] = {
    NotificationKind.CONFIRMATION: ("REQUEST", "CONFIRMED", 0),
    NotificationKind.RESCHEDULE: ("REQUEST", "CONFIRMED", 1),
    NotificationKind.CANCELLATION: ("CANCEL", "CANCELLED", 2),
    NotificationKind.REMINDER: ("REQUEST", "CONFIRMED", 0),
}


@dataclass(frozen=True)
class BookingEmailContext:
    """Everything the composer needs about one booking, resolved from the ORM by the service.

    ``start_at`` / ``end_at`` are timezone-aware UTC instants; ``guest_timezone`` is the IANA zone
    the guest booked in (used to render the local time). The three URLs are optional — the reminder
    job, for instance, has no signed guest links to pass.
    """

    uid: str
    event_title: str
    guest_name: str
    guest_email: str
    host_name: str
    host_email: str
    start_at: datetime
    end_at: datetime
    guest_timezone: str
    meeting_url: str | None = None
    cancel_url: str | None = None
    reschedule_url: str | None = None


@dataclass(frozen=True)
class _Copy:
    """The localized strings for one language."""

    subjects: Mapping[NotificationKind, str]
    intros: Mapping[NotificationKind, str]
    when_label: str
    meeting_label: str
    manage_heading: str
    cancel_label: str
    reschedule_label: str
    ics_note: str
    signoff: str


_ES = _Copy(
    subjects={
        NotificationKind.CONFIRMATION: "Reserva confirmada: {title}",
        NotificationKind.CANCELLATION: "Reserva cancelada: {title}",
        NotificationKind.RESCHEDULE: "Reserva reprogramada: {title}",
        NotificationKind.REMINDER: "Recordatorio: {title} en 24 horas",
    },
    intros={
        NotificationKind.CONFIRMATION: 'Hola {guest}, tu reserva "{title}" quedó confirmada.',
        NotificationKind.CANCELLATION: 'Hola {guest}, tu reserva "{title}" fue cancelada.',
        NotificationKind.RESCHEDULE: 'Hola {guest}, tu reserva "{title}" fue reprogramada.',
        NotificationKind.REMINDER: (
            'Hola {guest}, te recordamos tu reserva "{title}" en las próximas 24 horas.'
        ),
    },
    when_label="Cuándo",
    meeting_label="Enlace de la reunión",
    manage_heading="¿Necesitas hacer cambios?",
    cancel_label="Cancelar",
    reschedule_label="Reprogramar",
    ics_note="Adjuntamos la invitación de calendario (.ics).",
    signoff="— {host}",
)

_EN = _Copy(
    subjects={
        NotificationKind.CONFIRMATION: "Booking confirmed: {title}",
        NotificationKind.CANCELLATION: "Booking cancelled: {title}",
        NotificationKind.RESCHEDULE: "Booking rescheduled: {title}",
        NotificationKind.REMINDER: "Reminder: {title} in 24 hours",
    },
    intros={
        NotificationKind.CONFIRMATION: 'Hi {guest}, your booking "{title}" is confirmed.',
        NotificationKind.CANCELLATION: 'Hi {guest}, your booking "{title}" has been cancelled.',
        NotificationKind.RESCHEDULE: 'Hi {guest}, your booking "{title}" has been rescheduled.',
        NotificationKind.REMINDER: (
            'Hi {guest}, a reminder about your booking "{title}" in the next 24 hours.'
        ),
    },
    when_label="When",
    meeting_label="Meeting link",
    manage_heading="Need to make a change?",
    cancel_label="Cancel",
    reschedule_label="Reschedule",
    ics_note="A calendar invite (.ics) is attached.",
    signoff="— {host}",
)

_COPY: Mapping[str, _Copy] = {"es": _ES, "en": _EN}


def _normalize_locale(locale: str) -> str:
    """Reduce a locale tag to ``"en"`` or (the primary) ``"es"`` (RNF-1)."""
    primary = locale.split("-", 1)[0].strip().lower()
    return "en" if primary == "en" else "es"


def _format_when(start: datetime, timezone: str) -> str:
    """Render ``start`` in the guest's IANA zone (no locale-dependent names → deterministic)."""
    aware = start if start.tzinfo is not None else start.replace(tzinfo=UTC)
    local = aware.astimezone(ZoneInfo(timezone))
    return f"{local:%Y-%m-%d %H:%M} ({timezone})"


def _build_body(copy: _Copy, context: BookingEmailContext, kind: NotificationKind) -> str:
    """Assemble the plain-text body: intro, when, meeting link, manage links, ics note, signoff."""
    lines: list[str] = [
        copy.intros[kind].format(guest=context.guest_name, title=context.event_title),
        "",
        f"{copy.when_label}: {_format_when(context.start_at, context.guest_timezone)}",
    ]
    if context.meeting_url:
        lines.append(f"{copy.meeting_label}: {context.meeting_url}")

    manage: list[str] = []
    if context.cancel_url:
        manage.append(f"{copy.cancel_label}: {context.cancel_url}")
    if context.reschedule_url:
        manage.append(f"{copy.reschedule_label}: {context.reschedule_url}")
    if manage:
        lines.extend(["", copy.manage_heading, *manage])

    lines.extend(["", copy.ics_note, "", copy.signoff.format(host=context.host_name)])
    return "\n".join(lines)


def build_notification_email(
    context: BookingEmailContext, *, kind: NotificationKind, locale: str = "es"
) -> EmailMessage:
    """Compose the :class:`EmailMessage` for ``kind`` in ``locale`` (default Spanish, RNF-1).

    The guest is the recipient; the host is copied on confirmation/cancellation/reschedule. The
    ``.ics`` invite is attached as ``text/calendar`` with the iTIP method/status/sequence matching
    ``kind`` (see :data:`_ICS_LIFECYCLE`), so a cancellation cancels the event instead of re-adding
    it. ``From`` is intentionally left unset — the
    :class:`~aethercal.server.integrations.smtp.sender.SmtpEmailSender` stamps it from its config so
    the composer stays free of any environment/secret.
    """
    copy = _COPY[_normalize_locale(locale)]

    message = EmailMessage()
    message["Subject"] = copy.subjects[kind].format(title=context.event_title)
    message["To"] = formataddr((context.guest_name, context.guest_email))
    if kind in _HOST_IS_COPIED:
        message["Cc"] = formataddr((context.host_name, context.host_email))
    message.set_content(_build_body(copy, context, kind))

    method, status, sequence = _ICS_LIFECYCLE[kind]
    ics = booking_invite_ics(
        uid=context.uid,
        summary=context.event_title,
        start=context.start_at,
        end=context.end_at,
        organizer_name=context.host_name,
        organizer_email=context.host_email,
        attendee_name=context.guest_name,
        attendee_email=context.guest_email,
        method=method,
        status=status,
        sequence=sequence,
    )
    # A str routes to the text content manager (maintype is implicitly "text"); ``subtype`` gives
    # the text/calendar part. ``add_attachment`` marks it Content-Disposition: attachment.
    message.add_attachment(
        ics,
        subtype="calendar",
        filename="invite.ics",
    )
    return message


__all__ = ["BookingEmailContext", "NotificationKind", "build_notification_email"]
