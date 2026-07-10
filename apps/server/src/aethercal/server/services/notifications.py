"""Booking notification service (RF-08 / RF-10).

``send_booking_notification`` composes the email for a kind, sends it through the injected
:class:`~aethercal.server.integrations.smtp.sender.EmailSender`, and records a
:class:`~aethercal.server.db.models.SentNotification`. It is **idempotent** per
``(tenant_id, booking_id, kind)`` and **concurrency-safe**: it *reserves* the ledger row (a guarded
INSERT) BEFORE sending, so the unique constraint — not a racy read — arbitrates exactly-one send,
and a retried job or a double-fired (even concurrent) event never mails the guest twice.
Cancel/reschedule links are *passed in* — F1-05
mints the signed guest tokens (via the F1-06 service) and builds the URLs; this module never mints a
token. Like every service here it flushes but does not commit — the caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
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
    sequence: int | None = None,
) -> bool:
    """Compose + send the ``kind`` email for ``booking`` and record it; return whether it sent.

    ``sequence`` overrides the ``.ics`` SEQUENCE with the value snapshotted at the triggering
    transition (F1-08 outbox intent); ``None`` falls back to the booking's current ``sequence`` (the
    reminder path, which re-states the live event).

    Reserve-first idempotency (RF-08/RF-10): before sending, it INSERTs the ``(tenant_id,
    booking_id, kind)`` :class:`SentNotification` row inside a ``begin_nested`` SAVEPOINT (mirroring
    the duplicate-slug guard in ``services/event_types.py``). If the unique constraint rejects it
    (:class:`IntegrityError`) another caller already reserved that notification, so this returns
    ``False`` and sends nothing — the database, not a racy ``SELECT``, arbitrates exactly-one send
    under concurrency. Only when the reservation succeeds does it compose and hand the message to
    ``sender`` and return ``True``. Flushes (inside the savepoint); the caller owns the commit.

    Reserve-first closes the concurrent-duplicate hole (two callers both passing a pre-check and
    both mailing the guest), which the prior SELECT-then-send-then-INSERT order left open. The
    fires-for-a-rolled-back-booking hole is closed a layer up: F1-05 no longer calls this inline
    pre-commit but enqueues a transactional-outbox intent that the post-commit drainer runs (so this
    only ever executes for a booking that actually committed).
    """
    reservation = SentNotification(
        tenant_id=booking.tenant_id, booking_id=booking.id, kind=kind.value, sent_at=now
    )
    try:
        async with session.begin_nested():
            session.add(reservation)
            await session.flush()
    except IntegrityError:
        return False

    context = await _build_context(
        session, booking, cancel_url=cancel_url, reschedule_url=reschedule_url, sequence=sequence
    )
    message = build_notification_email(context, kind=kind, locale=locale)
    await sender.send(message)
    return True


async def _build_context(
    session: AsyncSession,
    booking: Booking,
    *,
    cancel_url: str | None,
    reschedule_url: str | None,
    sequence: int | None = None,
) -> BookingEmailContext:
    """Resolve the event title + host (organizer) from the booking's FKs into a composer context.

    The foreign keys guarantee the event type and its host exist; the fallbacks are purely defensive
    so a notification can never hard-fail a booking flow on an unexpectedly missing row.
    """
    event_type = await session.get(EventType, booking.event_type_id)
    host = await session.get(User, event_type.host_id) if event_type is not None else None
    return BookingEmailContext(
        uid=booking.ical_uid,
        event_title=event_type.title if event_type is not None else "",
        guest_name=booking.guest_name,
        guest_email=booking.guest_email,
        host_name=host.name if host is not None else "",
        host_email=host.email if host is not None else "",
        start_at=booking.start_at,
        end_at=booking.end_at,
        guest_timezone=booking.guest_timezone,
        sequence=booking.sequence if sequence is None else sequence,
        meeting_url=booking.meeting_url,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
    )


__all__ = ["send_booking_notification"]
