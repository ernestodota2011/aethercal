"""Booking notification service (RF-08 / RF-10 / RF-24).

The ledger (:class:`~aethercal.server.db.models.SentNotification`) holds one row per message
identity — ``(tenant, booking, kind, channel, step_id)`` — so a message is never sent twice for the
same identity. Cancel/reschedule links are *passed in*: the booking service mints the signed guest
tokens and builds the URLs; this module never mints a token. Like every service here it flushes but
does not commit — the caller owns the transaction.

Two shapes, because an in-transaction caller and the outbox drain need different things:

* :func:`send_booking_notification` — the **single-transaction, reserve-first** path. It INSERTs the
  ledger row BEFORE sending, so the unique index (not a racy read) arbitrates exactly-one send even
  under concurrency; if the send then raises, the caller's SAVEPOINT rolls the reservation back and
  a retry re-sends. Correct whenever the send happens inside the transaction.

* :func:`notification_already_sent` + :func:`compose_booking_notification` +
  :func:`record_booking_notification` — the same work split into **read / compose / record**, so the
  outbox drain can put the network send BETWEEN them with no transaction open at all (R8). There the
  exclusion comes from the outbox row's own claim + lease — a strictly better place for it, since
  the outbox row *is* the unit of work — and the ledger insert lands atomically with that row's
  ``delivered`` bookkeeping. The residual is unchanged, and still errs toward a duplicate rather
  than a loss: a crash after the provider accepted but before the settle commits replays the send.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, EventType, SentNotification, User
from aethercal.server.integrations.smtp.compose import (
    BookingEmailContext,
    NotificationKind,
    build_notification_email,
)
from aethercal.server.integrations.smtp.sender import EmailSender


async def notification_already_sent(
    session: AsyncSession,
    *,
    booking: Booking,
    kind: NotificationKind,
    channel: Channel = Channel.EMAIL,
    step_id: uuid.UUID | None = None,
) -> bool:
    """True when this message identity is already in the ledger (so it must not be re-sent)."""
    existing = await session.scalars(
        select(SentNotification.id).where(
            SentNotification.tenant_id == booking.tenant_id,
            SentNotification.booking_id == booking.id,
            SentNotification.kind == kind.value,
            SentNotification.channel == channel.value,
            SentNotification.step_id == step_id,
        )
    )
    return existing.first() is not None


async def record_booking_notification(  # noqa: PLR0913 - the ledger identity IS the keyword contract
    session: AsyncSession,
    *,
    booking: Booking,
    kind: NotificationKind,
    now: datetime,
    channel: Channel = Channel.EMAIL,
    step_id: uuid.UUID | None = None,
) -> bool:
    """Write the ledger row for a message identity; ``False`` when it was already recorded.

    The INSERT runs inside a ``SAVEPOINT`` so a unique-constraint conflict (another writer got there
    first) returns ``False`` instead of poisoning the caller's transaction — the guarded pattern
    ``services/event_types.py`` uses for a duplicate slug."""
    row = SentNotification(
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        kind=kind.value,
        channel=channel.value,
        step_id=step_id,
        sent_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return False
    return True


async def compose_booking_notification(  # noqa: PLR0913 - the composer needs the full booking context
    session: AsyncSession,
    *,
    kind: NotificationKind,
    booking: Booking,
    cancel_url: str | None,
    reschedule_url: str | None,
    locale: str = "es",
    sequence: int | None = None,
) -> EmailMessage:
    """Build the ``kind`` email for ``booking`` (a pure read + composition — it sends nothing).

    ``sequence`` overrides the ``.ics`` SEQUENCE with the value snapshotted at the triggering
    transition (F1-08); ``None`` falls back to the booking's current ``sequence``.
    """
    context = await _build_context(
        session, booking, cancel_url=cancel_url, reschedule_url=reschedule_url, sequence=sequence
    )
    return build_notification_email(context, kind=kind, locale=locale)


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
    """Reserve, compose and send the ``kind`` email for ``booking``; return whether it sent.

    The **reserve-first** path (RF-08/RF-10): the ledger row is INSERTed before the send, so the
    unique index — not a racy ``SELECT`` — arbitrates exactly-one send, and two concurrent callers
    can never both mail the guest. A rejected reservation means somebody else already owns this
    message identity: this returns ``False`` and sends nothing. If the send then raises, the
    caller's SAVEPOINT rolls the reservation back, so a retry re-sends rather than silently
    swallowing it.

    Use this when the send is inside your transaction. The outbox drain does NOT (it must not hold a
    transaction across network I/O) — it drives the read/compose/record trio above and takes its
    exclusion from the outbox row's claim + lease."""
    if not await record_booking_notification(session, booking=booking, kind=kind, now=now):
        return False
    message = await compose_booking_notification(
        session,
        kind=kind,
        booking=booking,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
        locale=locale,
        sequence=sequence,
    )
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
    so a notification can never hard-fail a booking flow on an unexpectedly missing row."""
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


__all__ = [
    "compose_booking_notification",
    "notification_already_sent",
    "record_booking_notification",
    "send_booking_notification",
]
