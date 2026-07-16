"""RNF-8 — guest erasure. ==A partial purge is worse than no purge at all.==

Worse, because it *reports success*. A purge that sweeps ``bookings`` and stops passes its own test,
answers the erasure request and closes the ticket — and leaves the guest's name and email sitting in
three other tables. Nobody finds out until somebody with a legal interest goes looking.

So the places the guest's data actually lives are ENUMERATED, and each is dealt with by name:

======================  =========================================================================
``bookings``            ``guest_name``, ``guest_email``, ``guest_phone``, ``guest_notes``,
                        ``answers``, ``guest_phone_consent_at``, ``guest_timezone``, and
                        ==``source_ip``== — the address the booking was made from, which is
                        personal data and is the ONE column here without a ``guest_`` prefix
``outbox``              the ``payload`` JSON carries ``guest_email`` (``services/bookings.py``)
``guest_tokens``        rows hanging off the booking
``sent_notifications``  the ledger of every message they were sent
``webhook_deliveries``  the ``payload`` JSON carries the WHOLE serialised booking — name, email,
                        notes, answers. ==And it has no ``booking_id`` column==, so a purge that
                        walks foreign keys never reaches it, and never notices that it did not
======================  =========================================================================

.. rubric:: Keep the fact, drop the person

The booking ROW survives, redacted. The appointment happened: it occupied that half-hour, it is in
the host's history and in their accounting, and the partial unique index records that the slot was
taken. Deleting it would rewrite the past and hand somebody else a slot that was genuinely used.
What is erased is the person, not the event. A webhook delivery survives the same way: "we told
this subscriber about this event" is a fact about our integration, and it need not name anybody.

DELETED outright is everything that exists ONLY in order to message this person: the queued outbox
intents (which would otherwise carry their address to a provider *after* the erasure), their guest
tokens, and the send ledger.

.. rubric:: The two structural locks

A purge is written once, against the schema of that day, and then rots in silence. Two module-level
declarations are asserted against the live metadata by the suite:

* :data:`BOOKING_PII_COLUMNS` + :data:`BOOKING_RETAINED_GUEST_COLUMNS` must together cover every
  ``guest_*`` column on ``bookings``. A new one has to be CLASSIFIED — erased, or explicitly kept.
* :data:`TABLES_PURGED_BY_BOOKING` must cover every table carrying a ``booking_id``. The channels
  cut is about to add rendered message bodies hanging off a booking; the day it lands, the suite
  fails until this module accounts for them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import (
    Booking,
    GuestToken,
    Outbox,
    SentNotification,
    WebhookDelivery,
)

_logger = logging.getLogger(__name__)

ERASED_NAME = "[erased]"

ERASED_EMAIL = "erased@invalid"
"""The tombstone address.

``.invalid`` is reserved by RFC 2606 and resolves nowhere, so even if some future code path did try
to mail an erased booking, the message cannot reach a real person — least of all the one who asked
to be forgotten. A plausible-looking placeholder would not have that property.

``guest_email`` is NOT NULL, so the column cannot simply be emptied; and a CONSTANT (rather than
something derived from the booking) means two erased guests are no longer distinguishable from one
another, which is rather the point of an erasure."""


BOOKING_PII_COLUMNS: dict[str, Any] = {
    "guest_name": ERASED_NAME,
    "guest_email": ERASED_EMAIL,
    "guest_phone": None,
    # The consent goes with the number it was given for. A standing "yes, you may message me on this
    # phone", attached to a person who no longer exists, is a permission nobody can withdraw.
    "guest_phone_consent_at": None,
    "guest_notes": None,
    "answers": {},
    # Not obviously "identifying", and not in the design's table — but it is a fact ABOUT the person
    # (roughly, where they live), it is theirs rather than the host's, and after the erasure nothing
    # needs it: the booking's own instants are UTC, and the only thing this ever fed was rendering a
    # local time into a message to a guest who is now gone. NOT NULL, so it is reset, not emptied.
    "guest_timezone": "UTC",
    # ==The address the booking was made from — PERSONAL DATA, and the only column here that does
    # not
    # wear the ``guest_`` prefix.== Roughly "where this person was". It is theirs, not the host's,
    # and nothing needs it after an erasure: the per-IP cap it feeds counts a ROLLING 24 hours, and
    # nobody purges the traffic of the last day.
    #
    # The prefix is exactly why this is argued in prose rather than left to the loop. The coverage
    # lock derives its expectation from ``guest_*``, so a test would never have DEMANDED this column
    # of the purge — a person had to decide it. That is precisely the shape of the defect the lock
    # exists to catch, arriving through the one door the lock does not watch. So the lock grew a
    # second half (``test_privacy``): every guest-prefixed column must be classified, AND everything
    # classified must be a column that really exists.
    "source_ip": None,
}
"""Every guest-bearing column on ``bookings``, and the value it is erased to.

This mapping is the contract, not an implementation detail: a test asserts it covers every
``guest_*`` column the model actually has, so the next one added must be classified here."""

BOOKING_RETAINED_GUEST_COLUMNS: frozenset[str] = frozenset()
"""Guest-bearing columns deliberately NOT erased, each with its reason. Empty today.

It exists so that "keep this one" has to be a decision somebody wrote down, rather than an omission
that looks exactly like a bug."""

_PURGED_BY_BOOKING: dict[str, Any] = {
    "outbox": Outbox,
    "guest_tokens": GuestToken,
    "sent_notifications": SentNotification,
}
"""Table name → model, for every table hanging off a booking. ==This IS the purge, not a list.==

The purge iterates this mapping, so the declaration and the mechanism are the same object. Kept as a
decorative set instead, the anti-drift lock below could be satisfied by *adding a name to it* — the
test would go green while the rows stayed exactly where they were. A lock you can pick by writing
down that you did the work, without doing it, is not a lock.

Deleted outright rather than redacted, because each of these exists ONLY in order to message this
person: an outbox intent would carry their address to a provider after the erasure; a guest token is
a live capability over their booking; the ledger is the record of everything they were sent."""

TABLES_PURGED_BY_BOOKING: frozenset[str] = frozenset(_PURGED_BY_BOOKING)
"""The anti-drift lock: asserted against every ``booking_id``-bearing table in ``Base.metadata``.

Derived from :data:`_PURGED_BY_BOOKING`, so satisfying it means WIRING the table, not listing it.
``webhook_deliveries`` is absent on purpose — it carries no ``booking_id``, so it is found by its
payload instead, which is exactly why a foreign-key-walking purge misses it."""

# The keys that carry the guest INSIDE a JSON payload, and what they become. Applied recursively, at
# any depth: the outbox keeps `guest_email` at the top level, while a webhook delivery buries the
# whole serialised booking under `data`, with `answers` a level below that again.
_JSON_PII_KEYS: dict[str, Any] = {
    "guest_name": ERASED_NAME,
    "guest_email": ERASED_EMAIL,
    "guest_phone": None,
    "guest_notes": None,
    "answers": {},
}


@dataclass(frozen=True, slots=True)
class PurgeReport:
    """What the purge actually touched. Counts, never contents."""

    bookings: int = 0
    webhook_deliveries: int = 0
    deleted_by_table: Mapping[str, int] = field(default_factory=dict)
    """Rows deleted, per table — keyed by :data:`_PURGED_BY_BOOKING`, so a table added there is
    counted here without anybody having to remember to add a field for it."""

    @property
    def outbox_intents(self) -> int:
        return self.deleted_by_table.get("outbox", 0)

    @property
    def guest_tokens(self) -> int:
        return self.deleted_by_table.get("guest_tokens", 0)

    @property
    def sent_notifications(self) -> int:
        return self.deleted_by_table.get("sent_notifications", 0)


def _redact_json(value: Any) -> tuple[Any, bool]:
    """Strip the guest out of a JSON tree BY KEY, at any depth. Returns ``(value, changed)``.

    By key and recursively — not by scanning for the email string. The same blob holds a name, free
    text notes and the guest's own answers, and a redaction that only knew how to find the address
    would leave all of that behind: the partial purge, one level down."""
    if isinstance(value, dict):
        source: dict[str, Any] = value
        redacted: dict[str, Any] = {}
        changed = False
        for key, item in source.items():
            if key in _JSON_PII_KEYS:
                redacted[key] = _JSON_PII_KEYS[key]
                changed = changed or item != _JSON_PII_KEYS[key]
                continue
            new_item, item_changed = _redact_json(item)
            redacted[key] = new_item
            changed = changed or item_changed
        return redacted, changed
    if isinstance(value, list):
        items: list[Any] = value
        results = [_redact_json(item) for item in items]
        return [item for item, _ in results], any(item_changed for _, item_changed in results)
    return value, False


async def purge_guest(session: AsyncSession, *, tenant_id: uuid.UUID, email: str) -> PurgeReport:
    """Erase a guest from ONE tenant (RNF-8). Runs inside the caller's transaction.

    ``tenant_id`` is not optional, and there is no "all tenants" mode. One person can be a guest of
    several businesses on one instance, with no relationship between those bookings; an unscoped
    purge would erase them from a business that never received the request, destroying that host's
    records on somebody else's say-so.

    The address is matched case-insensitively: somebody who booked as ``Ada@Example.com`` and writes
    in as ``ada@example.com`` is the same person, and an erasure that misses on capitalisation is an
    erasure that did not happen.
    """
    target = email.strip().lower()
    bookings = list(
        (
            await session.scalars(
                sa.select(Booking).where(
                    Booking.tenant_id == tenant_id,
                    sa.func.lower(Booking.guest_email) == target,
                )
            )
        ).all()
    )
    booking_ids = {booking.id for booking in bookings}

    # The deliveries go FIRST, matched by payload: that table has no `booking_id`, so it must be
    # found by the booking ids (and by the address, which catches a delivery whose booking is
    # already gone) — and those ids have to be gathered BEFORE anything is redacted away.
    deliveries = await _purge_webhook_deliveries(
        session, tenant_id=tenant_id, booking_ids=booking_ids, email=target
    )

    if not bookings:
        # Nothing of this guest's here. Worth saying out loud: an operator who ran the purge against
        # the wrong tenant slug would otherwise get a cheerful, entirely empty success.
        _logger.info(
            "guest purge for tenant %s matched no bookings (%d webhook delivery payload(s) "
            "redacted). If that is a surprise, check the tenant slug",
            tenant_id,
            deliveries,
        )
        return PurgeReport(webhook_deliveries=deliveries)

    for booking in bookings:
        for column, erased in BOOKING_PII_COLUMNS.items():
            # `answers` erases to a MUTABLE {} — give each row its own, or they share one dict.
            setattr(booking, column, dict(erased) if isinstance(erased, dict) else erased)

    # Driven BY the declaration, not alongside it: a table added to `_PURGED_BY_BOOKING` to satisfy
    # the anti-drift lock is a table whose rows this loop then actually deletes.
    deleted = {
        table: await _delete_by_booking(session, model, booking_ids)
        for table, model in _PURGED_BY_BOOKING.items()
    }
    await session.flush()

    report = PurgeReport(
        bookings=len(bookings),
        webhook_deliveries=deliveries,
        deleted_by_table=deleted,
    )
    # Loud, and with the counts: an erasure is a thing an operator may one day have to PROVE they
    # performed. The address itself is not logged — writing it into a logfile is the one thing an
    # erasure must not do.
    _logger.warning(
        "guest purge (tenant %s): %d booking(s) redacted; deleted %s; %d webhook delivery "
        "payload(s) redacted",
        tenant_id,
        report.bookings,
        ", ".join(f"{count} row(s) from {table}" for table, count in sorted(deleted.items())),
        report.webhook_deliveries,
    )
    return report


async def _delete_by_booking(session: AsyncSession, model: Any, booking_ids: set[uuid.UUID]) -> int:
    """Delete a table's rows for these bookings. Returns how many went."""
    if not booking_ids:
        return 0
    # A CursorResult, because only that carries `rowcount` — and the count is what the purge
    # REPORTS. An erasure an operator may one day have to prove they performed cannot shrug about
    # how much it actually removed.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            sa.delete(model)
            .where(model.booking_id.in_(booking_ids))
            .execution_options(synchronize_session=False)
        ),
    )
    return result.rowcount or 0


async def _purge_webhook_deliveries(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_ids: set[uuid.UUID], email: str
) -> int:
    """Redact the guest out of this tenant's webhook-delivery payloads. Returns how many changed.

    ==The table a purge always misses.== There is no ``booking_id`` here — the whole serialised
    booking sits inside a JSON blob keyed by WEBHOOK — so walking the foreign keys never arrives at
    it, and nothing complains.

    Filtered in Python rather than with a JSON operator, deliberately: the offline suite runs on
    SQLite and production on PostgreSQL, and their JSON path syntaxes differ. An erasure proven on
    only one of the two backends is not proven. The scan is tenant-scoped and a purge is a rare
    administrative act, so that cost is the right thing to trade for a guarantee that holds on both.

    The record itself survives, redacted: "we notified this subscriber about this event" is a fact
    about our integration, and it does not need to name anybody.
    """
    wanted_ids = {str(booking_id) for booking_id in booking_ids}
    rows = (
        await session.scalars(
            sa.select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant_id)
        )
    ).all()
    touched = 0
    for row in rows:
        payload: Any = row.payload
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            continue
        guest: Any = data.get("guest_email")
        booking_id: Any = data.get("id")
        names_the_guest = isinstance(guest, str) and guest.strip().lower() == email
        is_their_booking = isinstance(booking_id, str) and booking_id in wanted_ids
        if not (names_the_guest or is_their_booking):
            continue
        redacted, changed = _redact_json(payload)
        if not changed:
            continue
        # REASSIGNED, never mutated in place: SQLAlchemy does not track mutation inside a plain JSON
        # column, so an in-place edit would leave the object looking clean and would never be
        # written — a purge that runs, reports success, and changes nothing at all.
        row.payload = redacted
        touched += 1
    await session.flush()
    return touched


__all__ = [
    "BOOKING_PII_COLUMNS",
    "BOOKING_RETAINED_GUEST_COLUMNS",
    "ERASED_EMAIL",
    "ERASED_NAME",
    "TABLES_PURGED_BY_BOOKING",
    "PurgeReport",
    "purge_guest",
]
