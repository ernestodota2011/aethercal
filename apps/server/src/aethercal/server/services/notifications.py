"""Booking notification service (RF-08 / RF-10).

``send_booking_notification`` composes the email for a kind, sends it through the injected
:class:`~aethercal.server.integrations.smtp.sender.EmailSender`, and records a
:class:`~aethercal.server.db.models.SentNotification`. It is **idempotent** per
``(tenant_id, booking_id, kind)``: a repeat call finds the ledger row and skips, so a retried job or
a double-fired event never mails the guest twice. Cancel/reschedule links are *passed in* — F1-05
mints the signed guest tokens (via the F1-06 service) and builds the URLs; this module never mints a
token. Like every service here it flushes but does not commit — the caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import Booking, EventType, SentNotification, User
from aethercal.server.integrations.smtp.compose import (
    BookingEmailContext,
    NotificationKind,
    build_notification_email,
)
from aethercal.server.integrations.smtp.sender import EmailSender


async def send_booking_notification(  # noqa: PLR0913 - spec-mandated keyword contract (F1-05 caller)
    session: AsyncSession,
    *,
    kind: NotificationKind,
    booking: Booking,
    cancel_url: str | None,
    reschedule_url: str | None,
    sender: EmailSender,
    now: datetime,
    locale: str = "es",
) -> bool:
    """Compose + send the ``kind`` email for ``booking`` and record it; return whether it sent.

    Returns ``False`` (a no-op skip) when a ``(tenant_id, booking_id, kind)`` ledger row already
    exists; otherwise sends via ``sender``, inserts the ledger row stamped ``sent_at=now``, and
    returns ``True``. The unique constraint on the ledger is the backstop against a concurrent race.
    Flushes; the caller owns the commit.
    """
    already_sent = await session.scalar(
        select(SentNotification.id).where(
            SentNotification.tenant_id == booking.tenant_id,
            SentNotification.booking_id == booking.id,
            SentNotification.kind == kind.value,
        )
    )
    if already_sent is not None:
        return False

    context = await _build_context(
        session, booking, cancel_url=cancel_url, reschedule_url=reschedule_url
    )
    message = build_notification_email(context, kind=kind, locale=locale)
    await sender.send(message)

    session.add(
        SentNotification(
            tenant_id=booking.tenant_id, booking_id=booking.id, kind=kind.value, sent_at=now
        )
    )
    await session.flush()
    return True


async def _build_context(
    session: AsyncSession,
    booking: Booking,
    *,
    cancel_url: str | None,
    reschedule_url: str | None,
) -> BookingEmailContext:
    """Resolve the event title + host (organizer) from the booking's FKs into a composer context.

    The foreign keys guarantee the event type and its host exist; the fallbacks are purely defensive
    so a notification can never hard-fail a booking flow on an unexpectedly missing row.
    """
    event_type = await session.get(EventType, booking.event_type_id)
    host = await session.get(User, event_type.host_id) if event_type is not None else None
    return BookingEmailContext(
        uid=f"{booking.id}@aethercal",
        event_title=event_type.title if event_type is not None else "",
        guest_name=booking.guest_name,
        guest_email=booking.guest_email,
        host_name=host.name if host is not None else "",
        host_email=host.email if host is not None else "",
        start_at=booking.start_at,
        end_at=booking.end_at,
        guest_timezone=booking.guest_timezone,
        meeting_url=booking.meeting_url,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
    )


__all__ = ["send_booking_notification"]
