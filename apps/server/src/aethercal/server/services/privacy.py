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
``outbox``              the ``payload`` JSON carries ``guest_email`` (``services/bookings.py``) —
                        but ==only for the effects that are MESSAGES==; see :func:`purge_policy`
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

.. rubric:: ==The erasure does not keep their money== (B-05c)

"Everything that exists only in order to message this person" was a true description of the outbox
when it was written, and B-05b made it false. A ``REFUND`` intent is not a message: it is money the
guest is OWED, its payload is ``{provider, provider_ref}``, and it names nobody. Deleted before the
drain reaches it, the refund never runs — and the guest ends up erased AND out of pocket, silently,
with the purge reporting success. An ``EXPIRE_HOLD`` is the same story told about a slot: delete it
and the hold never lapses, so a guest's erasure blocks the HOST's calendar for ever.

So WHICH intents go is decided one row at a time by :func:`purge_policy`, and the survivors are
redacted rather than trusted (:func:`_redact_retained_outbox`). ==The tension with the erasure is
real and is resolved, not dodged==: ``provider_ref`` is a pseudonymous identifier — it resolves, at
Stripe, to a person — and it is retained on exactly the footing ``payments`` already is (a financial
record, kept for the obligation it discharges and for the very refund the subject is owed). The
intent introduces no identifier the purge does not already keep one table over, and it is transient
besides: the drain runs it and it goes ``delivered``. What is NOT exempt is anything more than that,
which is why the retained payload's keys are declared and the funnel refuses an undeclared one.

.. rubric:: The three structural locks

A purge is written once, against the schema of that day, and then rots in silence. Three
module-level declarations are asserted against the live code by the suite:

* :data:`BOOKING_PII_COLUMNS` + :data:`BOOKING_RETAINED_GUEST_COLUMNS` must together cover every
  ``guest_*`` column on ``bookings``. A new one has to be CLASSIFIED — erased, or explicitly kept.
* :data:`TABLES_PURGED_BY_BOOKING` must cover every table carrying a ``booking_id``. The channels
  cut is about to add rendered message bodies hanging off a booking; the day it lands, the suite
  fails until this module accounts for them.
* :func:`purge_policy` must classify every ``OutboxEffect``, and the suite proves the EFFECTIVE
  state per effect — it seeds a row of each and checks it went (or stayed) as classified. So the
  thirteenth effect somebody adds is covered by that test on the day they add it, and both
  ``assert_never`` and the suite refuse it until they have decided whether an erasure deletes it.
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
    Payment,
    PaymentEvent,
    PaymentEventStatus,
    SentNotification,
    WebhookDelivery,
)
from aethercal.server.services.outbox import (
    PURGEABLE_EFFECTS,
    RETAINED_PAYLOAD_KEYS,
    OutboxEffect,
)

# ==A payment event is redactable ONLY once it is TERMINAL (r6 finding 2).== ``received``/``parked``
# events are still ACTIONABLE — the parked tick re-runs the arbiter from their payload — so
# redacting one would destroy the amount/currency it needs and strand a CHARGED payment that never
# confirms nor refunds — the worst outcome the system can produce. Only ``applied``/``dead`` are.
_TERMINAL_PAYMENT_EVENT_STATUSES = frozenset({PaymentEventStatus.APPLIED, PaymentEventStatus.DEAD})

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


@dataclass(frozen=True, slots=True)
class _PurgedTable:
    """A table the purge deletes from, and (optionally) WHICH of its rows."""

    model: Any
    only: Any = None
    """An extra row predicate, or ``None`` for "every row of this booking".

    It exists for ``outbox``, where "delete the lot" is wrong for one effect — see below."""


_PURGED_BY_BOOKING: dict[str, _PurgedTable] = {
    # ==NOT every row: only the effects that are MESSAGES (B-05c).== The predicate is derived from
    # `purge_policy`, so this line never has to be revisited when an effect is added — the table in
    # `services/outbox.py` is where that decision is made, and it is made or the build fails.
    #
    # ==And note what the predicate does NOT say: nothing about STATUS.== A message intent a worker
    # has CLAIMED and is mid-send on is deleted like any other, and that is a decision rather than
    # an oversight — `purge_guest`'s docstring argues it (there is no second purge pass, so sparing
    # a row means keeping the guest's address for good) and `test_privacy` locks it.
    "outbox": _PurgedTable(Outbox, only=Outbox.effect.in_(PURGEABLE_EFFECTS)),
    "guest_tokens": _PurgedTable(GuestToken),
    "sent_notifications": _PurgedTable(SentNotification),
}
"""Table name → what to delete, for every table hanging off a booking. ==This IS the purge.==

The purge iterates this mapping, so the declaration and the mechanism are the same object. Kept as a
decorative set instead, the anti-drift lock below could be satisfied by *adding a name to it* — the
test would go green while the rows stayed exactly where they were. A lock you can pick by writing
down that you did the work, without doing it, is not a lock.

Deleted rather than redacted, because these exist ONLY in order to message this person: a guest
token is a live capability over their booking; the ledger is the record of everything they were
sent; and an outbox intent would carry their address to a provider after the erasure.

.. rubric:: ==Except the intent that is not a message (B-05c)==

That last clause was written when every effect WAS a message, and B-05b made it false. A ``REFUND``
intent is money the guest is owed and an ``EXPIRE_HOLD`` is a slot waiting to be freed; neither
names anybody, and deleting either takes something from the person the erasure exists to serve — the
refund silently never runs, and the guest ends up erased AND out of pocket. So which rows go is
decided by :func:`purge_policy`, one row at a time, and what stays is redacted by
:func:`_redact_retained_outbox` rather than trusted."""

TABLES_PURGED_BY_BOOKING: frozenset[str] = frozenset(_PURGED_BY_BOOKING)
"""The anti-drift lock: asserted against every ``booking_id``-bearing table in ``Base.metadata``.

Derived from :data:`_PURGED_BY_BOOKING`, so satisfying it means WIRING the table, not listing it.
``webhook_deliveries`` is absent on purpose — it carries no ``booking_id``, so it is found by its
payload instead, which is exactly why a foreign-key-walking purge misses it."""

TABLES_RETAINED_OFF_BOOKING: dict[str, str] = {
    "payments": (
        "A FINANCIAL RECORD, kept — not deleted, and with no guest-PII column to redact. It is the "
        "'proven otherwise' the lock's default (a booking_id table holds guest data → delete it) "
        "leaves room for, spelled out so the exemption is a DECISION rather than an omission that "
        "looks like one. Three reasons it must survive an erasure: it is the ledger a refund is "
        "audited against; its UNIQUE (tenant_id, provider, provider_ref) is the money's "
        "idempotency, and a deleted row lets the same charge be re-processed; and it names no "
        "person — only a booking, a provider, an opaque provider_ref and an amount. The guest is "
        "erased from the "
        "BOOKING it points at; the money record it keeps identifies nobody."
    ),
}
"""The SECOND category the tables lock needs: tables that hang off a booking but are RETAINED.

The mirror of :data:`BOOKING_RETAINED_GUEST_COLUMNS` (which does this for columns). Without it the
only way to satisfy the ``booking_id`` lock would be to DELETE ``payments`` — destroying the refund
audit trail and the money's idempotency. Each entry carries, in prose, WHY the row is kept and why
keeping it leaks no PII. ``payment_events`` is NOT here — it has no ``booking_id``, so the lock
reaches it; its payload is redacted by :func:`_purge_payment_events`, the same way
``webhook_deliveries`` is."""

# The keys that carry the guest INSIDE a JSON payload, and what they become. Applied recursively, at
# any depth: the outbox keeps `guest_email` at the top level, while a webhook delivery buries the
# whole serialised booking under `data`, with `answers` a level below that again.
JSON_PII_KEYS: dict[str, Any] = {
    "guest_name": ERASED_NAME,
    "guest_email": ERASED_EMAIL,
    "guest_phone": None,
    "guest_notes": None,
    "answers": {},
}

SAFE_TO_NAME_KEYS: frozenset[str] = frozenset(JSON_PII_KEYS) | frozenset(
    key for keys in RETAINED_PAYLOAD_KEYS.values() for key in keys
)
"""The payload keys an anomaly line may QUOTE. ==Because a key can BE the datum.==

The anomaly reporter exists so a strange erasure cannot pass unseen, and it was itself a leak.
"Names keys, never values" sounds like the safe rule and is not: a dict keyed by address —
``{"ada@example.com": {...}}``, a group-by that got serialised, an old per-recipient map — puts the
guest's email in the KEY position. Quoting it writes the address to an ERROR log and an operator's
terminal moments after the erasure removed it from the table, and a log is replicated and outlives
the row. That is the erasure undone in the one artefact nobody audits as a database.

So this is an ALLOWLIST, the same lesson a third time: ==a key may be quoted only if it is OUR OWN
word.== ``guest_email`` is not somebody's email — it is a name chosen in this module, a constant in
the source tree, and printing it discloses nothing while telling the operator the most useful thing
there is. A key nobody declared is DATA until proven otherwise: it gets counted, never quoted.

DERIVED from the two declarations that already exist — the guest-bearing key names, and everything
the retained effects declare — so a new declared key is quotable for free and no separate list of
"safe words" can rot alongside them.

==No hash, and that is a decision.== Hashing an unrecognised key would let an operator confirm a
guess, and that is exactly the problem: an email is low-entropy, so a hash of one is re-identifiable
by dictionary attack, and a pseudonymised identifier of an erased person is still their personal
data. It would be the same leak, one step slower. The effect, the intent id and a count are what
make the anomaly actionable; the row itself is RETAINED, so an operator can go and read its
``created_at``/``dedupe_key`` and find the write path that produced it."""


@dataclass(frozen=True, slots=True)
class PurgeReport:
    """What the purge actually touched. Counts, never contents."""

    bookings: int = 0
    webhook_deliveries: int = 0
    payment_events: int = 0
    """Payment-event rows whose raw provider payload was redacted (the row itself stands)."""
    outbox_retained: int = 0
    """Queued intents KEPT because they are not messages: a refund owed, a hold expiry.

    Reported rather than left silent — an erasure is a thing an operator may have to PROVE they
    performed, and "we kept two rows about this person" is exactly the sort of fact they must be
    able to explain. The answer is that those rows name nobody (see :func:`purge_policy`)."""
    outbox_payloads_redacted: int = 0
    """RETAINED intents whose payload carried an undeclared key, now rebuilt from its allowlist.

    Normally ``0``, and that is the point: :data:`RETAINED_PAYLOAD_KEYS` plus the funnel guard mean
    a retained payload should never hold anything undeclared. This counts rows written BEFORE that
    guard existed, so a non-zero here is worth an operator's attention rather than a shrug."""
    purge_anomalies: list[str] = field(default_factory=list)
    """One line per anomaly, for a HUMAN — naming the effect and the ROW, never the person.

    ==A detected anomaly nobody is told about is the silent no-op wearing a hat.== The count alone
    ("1 payload redacted") cannot be acted on: an operator who must PROVE what they erased needs to
    know WHICH effect and WHICH key. Carried as data rather than only as a log line, so the CLI can
    surface it and the suite can assert it. Empty on an ordinary purge, and the CLI then says
    nothing at all — an alarm that fires every time is an alarm nobody reads."""
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
            if key in JSON_PII_KEYS:
                redacted[key] = JSON_PII_KEYS[key]
                changed = changed or item != JSON_PII_KEYS[key]
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

    .. rubric:: ==ONE SHOT. There is no second pass.==

    This is reached from ``run_guest_purge``, which is reached from the ``guest purge`` CLI command,
    and from nowhere else. The scheduler registers three recurring jobs — webhook delivery, busy
    refresh, outbox drain — and none of them is this. No cron, no timer, no tick.

    That fact decides things that look like matters of taste. A queued message intent a worker has
    CLAIMED and is mid-send on is deleted like any other, rather than being spared for "a later
    pass": there is no later pass, so sparing it would mean the guest's address stays in that row
    for good — and a legal obligation cannot rest on an operator remembering to run a command twice.

    ==So nothing here may DEFER erasing PII.== :func:`_purge_payment_events` looks like a precedent
    for deferring (it retains a still-actionable event "for a later purge pass") and it is not one:
    what it retains is PII-free by CONSTRUCTION — ``payment_webhooks.PaymentEventBody.payload``
    builds ``{kind, provider_ref, amount_cents, currency, checkout_session_id}`` and never the raw
    provider body. It defers nothing that a person could be identified by. Any future deferral has
    to clear that same bar, and "we will get it next time" is not available as an argument.

    .. rubric:: ==What an erasure CANNOT do, stated plainly==

    A message already handed to the SMTP server, or to WhatsApp, or to Google, **will arrive**.
    Deleting its intent does not recall it; sparing the intent would not either; nothing here can.
    The drain claims a row, then runs its network I/O with no transaction open, and an erasure
    landing in that window is racing something that has already left the building.

    So the guarantee is exact, and narrower than "everything is gone": ==this erases the DATA we
    hold about a person==. It does not un-send what was already sent, and the ledger of what WAS
    sent is deleted (``sent_notifications``) even though the messages themselves are out there. An
    operator answering an erasure request should say that, rather than the thing everybody would
    prefer to be true.
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
    # The guest's PAYMENT EVENTS, found by the provider_refs of their payments — that table has no
    # booking_id either, so the booking is reached through the payment. The rows STAND (the columns
    # carry the anti-replay key); only the raw provider payload, which can hold a customer email, is
    # cleared. Gathered before any booking is redacted, like the deliveries above.
    payment_events = await _purge_payment_events(
        session, tenant_id=tenant_id, booking_ids=booking_ids
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
        return PurgeReport(webhook_deliveries=deliveries, payment_events=payment_events)

    for booking in bookings:
        for column, erased in BOOKING_PII_COLUMNS.items():
            # `answers` erases to a MUTABLE {} — give each row its own, or they share one dict.
            setattr(booking, column, dict(erased) if isinstance(erased, dict) else erased)

    # Driven BY the declaration, not alongside it: a table added to `_PURGED_BY_BOOKING` to satisfy
    # the anti-drift lock is a table whose rows this loop then actually deletes.
    deleted = {
        name: await _delete_by_booking(session, table, booking_ids)
        for name, table in _PURGED_BY_BOOKING.items()
    }
    # What the outbox KEPT (a refund owed, a hold to expire) loses the guest but not its job.
    retained, redacted_payloads, anomalies = await _redact_retained_outbox(session, booking_ids)
    await session.flush()

    report = PurgeReport(
        bookings=len(bookings),
        webhook_deliveries=deliveries,
        payment_events=payment_events,
        outbox_retained=retained,
        outbox_payloads_redacted=redacted_payloads,
        purge_anomalies=anomalies,
        deleted_by_table=deleted,
    )
    # Loud, and with the counts: an erasure is a thing an operator may one day have to PROVE they
    # performed. The address itself is not logged — writing it into a logfile is the one thing an
    # erasure must not do.
    _logger.warning(
        "guest purge (tenant %s): %d booking(s) redacted; deleted %s; %d webhook delivery "
        "payload(s) redacted; %d outbox intent(s) RETAINED (money/slot, naming nobody)",
        tenant_id,
        report.bookings,
        ", ".join(f"{count} row(s) from {table}" for table, count in sorted(deleted.items())),
        report.webhook_deliveries,
        report.outbox_retained,
    )
    return report


async def _delete_by_booking(
    session: AsyncSession, table: _PurgedTable, booking_ids: set[uuid.UUID]
) -> int:
    """Delete a table's rows for these bookings. Returns how many went.

    ``table.only`` narrows WHICH rows — the outbox keeps the intents that are not messages."""
    if not booking_ids:
        return 0
    where = [table.model.booking_id.in_(booking_ids)]
    if table.only is not None:
        where.append(table.only)
    # A CursorResult, because only that carries `rowcount` — and the count is what the purge
    # REPORTS. An erasure an operator may one day have to prove they performed cannot shrug about
    # how much it actually removed.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            sa.delete(table.model).where(*where).execution_options(synchronize_session=False)
        ),
    )
    return result.rowcount or 0


REDACTED_OUTBOX_PAYLOAD: dict[str, Any] = {"redacted": True}
"""What a retained payload becomes when nothing can vouch for a single one of its keys."""


@dataclass(frozen=True, slots=True)
class _Rebuilt:
    """What :func:`_retained_payload` did to one payload, and how to say it to a human.

    The ``reason`` travels WITH the result on purpose. The caller used to re-derive what had been
    dropped by diffing the two payloads — and that only worked for the one shape it had in mind: it
    raised ``TypeError`` on a scalar and silently produced a list of CHARACTERS for a string. The
    function that knows the shape is the function that describes what it did to it.
    """

    payload: Any
    changed: bool
    reason: str = ""
    """Operator-facing, and ``""`` when nothing changed. Names keys and shapes, never VALUES."""


def _retained_payload(effect: str, payload: Any) -> _Rebuilt:
    """Rebuild a retained payload from what it may KEEP.

    ==An ALLOWLIST, and the distinction is the whole finding.== :func:`_redact_json` erases the keys
    it KNOWS (``guest_email``, ``guest_name``, ...), which is right for a row that is about to be
    deleted anyway and wrong for one the erasure KEEPS: it leaves behind every key it has not met.
    ``receipt_email``, ``billing_name``, ``customer`` — a denylist has the same defect as a list of
    instances. It is a photograph, and the key somebody adds tomorrow is not in it.

    So the retained payload is rebuilt from :data:`RETAINED_PAYLOAD_KEYS`, and everything else is
    dropped. Nothing operational is lost by construction: those keys are exactly what the runners
    read (``work.payload["provider"]`` / ``["provider_ref"]`` in ``make_refund_runner``,
    ``["booking_id"]`` in ``make_expire_hold_runner``), so an undeclared key was, by definition,
    read by nobody.

    An effect no enum member recognises (one removed from ``OutboxEffect`` later; a hand-written
    row) has no allowlist at all, so it FAILS CLOSED: the payload goes wholesale, the way
    :func:`_purge_payment_events` treats a provider schema that is not ours. That costs nothing
    either — an effect with no member has no handler, so the row was never going to run.

    .. rubric:: ==A payload is not necessarily an object==

    ``payload`` is a JSON column, and JSON is not ``dict``: a list, a string, a number, ``true`` and
    ``null`` all live there legally, and a row from before the funnel guard can hold any of them.
    The rule is the same one, a third time: ==a shape nobody can vouch for is a shape we do not
    keep.== A scalar has no keys, so there is no allowlist to apply and it goes wholesale.

    It matters more than the leak it prevents. Raising here does not leak a key — it aborts the
    ERASURE, and an erasure that raises is one that did not happen at all, for that guest, in that
    tenant. The wrong shape must never be able to take the rest of the purge down with it.
    """
    if payload == REDACTED_OUTBOX_PAYLOAD:
        # Already erased wholesale. The command is one-shot, but nothing stops an operator running
        # it twice, and the second run must not "drop" the marker and report a phantom anomaly.
        return _Rebuilt(payload, changed=False)
    try:
        allowed = RETAINED_PAYLOAD_KEYS[OutboxEffect(effect)]
    except (ValueError, KeyError):
        # No member, or a RETAINED member that declared nothing: we cannot vouch for any key here.
        return _Rebuilt(
            dict(REDACTED_OUTBOX_PAYLOAD),
            changed=True,
            reason=f"effect {effect!r} has no declared allowlist; payload replaced wholesale",
        )
    if not isinstance(payload, dict):
        return _Rebuilt(
            dict(REDACTED_OUTBOX_PAYLOAD),
            changed=True,
            reason=(
                f"payload is {type(payload).__name__}, not a JSON object; replaced wholesale "
                "(no keys to allow)"
            ),
        )
    source: dict[str, Any] = payload
    kept = {key: value for key, value in source.items() if key in allowed}
    if kept == source:
        return _Rebuilt(source, changed=False)
    dropped = set(source) - set(kept)
    return _Rebuilt(kept, changed=True, reason=_describe_dropped(dropped))


def _describe_dropped(dropped: set[str]) -> str:
    """Say what was dropped, quoting only OUR OWN words. See :data:`SAFE_TO_NAME_KEYS`.

    The split is the whole point: a key we declared is a constant in the source tree and safe to
    print; a key we have never seen may BE the person we just erased, so it is counted instead.
    Useless as a rule of thumb, exact as a rule — and the operator still gets a number, an effect
    and (from the caller) the row, which is what makes the line worth acting on.
    """
    ours = sorted(dropped & SAFE_TO_NAME_KEYS)
    unrecognised = len(dropped) - len(ours)
    parts: list[str] = []
    if ours:
        parts.append(f"declared-elsewhere key(s) {ours}")
    if unrecognised:
        # NOT named: it could be an address, a phone, anything. The count is the whole message.
        parts.append(f"{unrecognised} unrecognised key(s) (not quoted — a key can BE the data)")
    return f"carried undeclared payload {' + '.join(parts)}; dropped"


async def _redact_retained_outbox(
    session: AsyncSession, booking_ids: set[uuid.UUID]
) -> tuple[int, int, list[str]]:
    """Strip the guest out of the intents the purge KEEPS. ``(retained, redacted, anomalies)``.

    ==The purge does not merely trust the classification.== A ``REFUND`` is retained because its
    payload is ``{provider, provider_ref}`` and names nobody; :data:`RETAINED_PAYLOAD_KEYS` and the
    guard in ``enqueue_effect`` are what keep that true going forward. But neither binds a row
    committed a year ago, before either existed — and an erasure cannot be conditional on the
    history of the codebase. So what survives is rebuilt from its allowlist, the same spirit as
    ``webhook_deliveries`` and ``payment_events``: ==keep the row, remove the person==.

    Ordinarily this changes nothing and reports ``0``, and it says nothing — an anomaly line printed
    on every ordinary purge is one nobody reads, which is how the real one gets missed.

    Selected by the COMPLEMENT of :data:`PURGEABLE_EFFECTS` rather than by naming the retained
    effects: a row whose effect nothing recognises is then retained and redacted rather than
    deleted — the purge does not destroy what it cannot classify, but it does take the person out.
    """
    if not booking_ids:
        return 0, 0, []
    rows = (
        await session.scalars(
            sa.select(Outbox).where(
                Outbox.booking_id.in_(booking_ids),
                Outbox.effect.not_in(PURGEABLE_EFFECTS),
            )
        )
    ).all()
    redacted_count = 0
    anomalies: list[str] = []
    for row in rows:
        rebuilt = _retained_payload(row.effect, row.payload)
        if not rebuilt.changed:
            continue
        # REASSIGNED, never mutated in place: SQLAlchemy does not track mutation inside a plain JSON
        # column, so an in-place edit would leave the object looking clean and would never be
        # written — a purge that runs, reports success, and changes nothing at all.
        row.payload = rebuilt.payload
        redacted_count += 1
        # The reason comes FROM the rebuild rather than being re-derived here: this line used to
        # diff the two payloads to work out what went, which raised on a scalar and produced a list
        # of characters for a string. It carries keys and shapes, never VALUES — an anomaly line is
        # evidence an operator may have to show, and evidence naming the person who asked to be
        # forgotten is the one thing an erasure must not write down.
        # The intent's ID is what makes the line actionable without quoting anything of the guest's:
        # it is a UUID we generated, and the row is RETAINED — so an operator can go and read its
        # created_at / dedupe_key / tenant and find the write path that got past the funnel guard.
        # That is the action here. Recovering the dropped key is not: it was the person.
        anomalies.append(f"outbox intent {row.id} ({row.effect}) {rebuilt.reason}")
    if anomalies:
        # ERROR, not warning: a row got past the funnel guard. It predates it, or something wrote to
        # the table directly — and either way somebody should find out which.
        _logger.error(
            "purge: %d RETAINED outbox intent(s) carried undeclared payload key(s). The intents "
            "still run and the person is gone, but a row reached the table without passing "
            "enqueue_effect's guard: %s",
            redacted_count,
            "; ".join(anomalies),
        )
    await session.flush()
    return len(rows), redacted_count, anomalies


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


async def _purge_payment_events(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking_ids: set[uuid.UUID]
) -> int:
    """Redact the raw provider payload out of this guest's payment events. Returns how many changed.

    ==The row STANDS; only its ``payload`` is cleared.== Deleting the row would destroy the
    ``UNIQUE (tenant_id, provider, event_id)`` that IS the anti-replay guard — and then the same
    provider event could be applied again, refunding or confirming against a booking that has
    already been dealt with. So the anti-replay key (which lives in COLUMNS, not the payload) is
    kept, and the payload — a raw provider event that can carry a customer's email — is replaced
    wholesale.

    Replaced wholesale, not key-by-key like the booking-shaped blobs: a provider's event schema
    is not ours and puts PII under keys we do not control (Stripe's ``customer_details.email``,
    ``receipt_email``, a billing name). A key allow-list would leak the first field a provider
    renamed. What the arbiter needs from an APPLIED event is already in the ``payments`` ledger and
    in the booking; the payload is evidence, and evidence naming an erased person is exactly what
    an erasure removes.

    The events are reached through the guest's PAYMENTS (``payment_events`` has no ``booking_id``
    — it links to a payment by ``provider_ref``), so a purge that walked foreign keys from the
    booking would miss it, which is the whole reason it is handled here by name.

    .. rubric:: Only TERMINAL events are redacted (r6 finding 2)

    ==A charged payment is NEVER orphaned by a purge.== A ``received``/``parked`` event has not been
    applied yet — the parked tick re-runs the arbiter from its payload to CONFIRM or REFUND the
    charge — so redacting it would destroy the amount/currency the arbiter needs and leave a payment
    that was taken but never confirmed nor refunded, the worst outcome this system can produce
    (§4.4). So only ``applied``/``dead`` events are redacted here; an in-flight one is retained (it
    holds financial identifiers, not guest PII) and becomes redactable on a later purge pass once
    the tick has driven it terminal. The count returned is the redactions done, not the retentions.
    """
    if not booking_ids:
        return 0
    provider_refs = set(
        (
            await session.scalars(
                sa.select(Payment.provider_ref).where(
                    Payment.tenant_id == tenant_id, Payment.booking_id.in_(booking_ids)
                )
            )
        ).all()
    )
    if not provider_refs:
        return 0
    rows = (
        await session.scalars(
            sa.select(PaymentEvent).where(
                PaymentEvent.tenant_id == tenant_id,
                PaymentEvent.provider_ref.in_(provider_refs),
            )
        )
    ).all()
    touched = 0
    retained = 0
    for row in rows:
        if row.payload == _REDACTED_PAYMENT_PAYLOAD:
            continue
        if row.status not in _TERMINAL_PAYMENT_EVENT_STATUSES:
            # ==Still actionable (r6 finding 2): DO NOT redact.== A ``received``/``parked`` event
            # has not been applied yet; the parked tick re-runs the arbiter from THIS payload (the
            # amount + currency it needs live here, not in a ledger row that does not exist yet).
            # Redacting it would strand a CHARGED payment that never confirms nor refunds. It holds
            # no guest PII — it is a financial record in flight — so retaining it is safe, and a
            # later purge pass redacts it once the tick has driven it to ``applied``/``dead``.
            retained += 1
            continue
        # REASSIGNED, never mutated in place — SQLAlchemy does not track mutation in a plain JSON
        # column, so an in-place edit would look clean and never be written (a purge that reports
        # success and changes nothing).
        row.payload = dict(_REDACTED_PAYMENT_PAYLOAD)
        touched += 1
    if retained:
        _logger.info(
            "purge: retained %d still-actionable payment event(s) for tenant %s (redacted once the "
            "parked tick resolves them); a charged payment is never orphaned by a purge",
            retained,
            tenant_id,
        )
    await session.flush()
    return touched


_REDACTED_PAYMENT_PAYLOAD: dict[str, Any] = {"redacted": True}
"""What a purged payment-event payload becomes: a marker, carrying no PII and no provider schema."""


__all__ = [
    "BOOKING_PII_COLUMNS",
    "BOOKING_RETAINED_GUEST_COLUMNS",
    "ERASED_EMAIL",
    "ERASED_NAME",
    "JSON_PII_KEYS",
    "SAFE_TO_NAME_KEYS",
    "TABLES_PURGED_BY_BOOKING",
    "TABLES_RETAINED_OFF_BOOKING",
    "PurgeReport",
    "purge_guest",
]
