"""The phone-channel guard (RF-24): daily caps, fail-closed, counted from what was really sent.

.. rubric:: Why a phone channel needs a guard and email does not

The recipient comes from the **public booking form**. Anyone can book with a stranger's number and
make this system message that stranger — and the messaging account it goes out on may be one other
systems of the operator's depend on. A spam complaint against a WhatsApp Business number or a Twilio
sender is not something you recover from by fixing a bug.

So the phone channels are **fail-closed**:

* :class:`DailyCaps` refuses to exist with a missing, zero or negative cap. A cap you forgot to
  configure is not "unlimited" — it is the misconfiguration whose only symptom is the bill;
* :meth:`DailyCaps.from_env` refuses to build from a half-configured environment, so a channel
  cannot come up *sending* but *uncapped*. An entirely unconfigured channel is simply absent from
  the registry, which is a disabled feature and perfectly fine — the difference between "off" and
  "on with no ceiling" is the whole point;
* a booking with **no phone** is REFUSED rather than counted as zero. "The count came back zero, so
  it is under the cap" is a hole every unbounded send walks straight through.

.. rubric:: The cap counts the EFFECTIVE state

:func:`phone_sends_in_window` reads the ``sent_notifications`` ledger — the record of what was
*actually* sent — joined to the phone on the booking. Not a counter in this process's memory: that
would reset to zero on every restart and every deploy, and a second worker would keep its own. The
cap would hold perfectly in a unit test and mean nothing in production, which is this codebase's
signature failure mode.

Two consequences worth stating, because they are deliberate:

* the cap protects a **person, not a booking**. It counts across every booking that carries the
  number, so an attacker who books ten times with a stranger's number does not get ten times the
  budget;
* it is scoped per **tenant** and per **channel** — one business never spends another's budget, and
  each channel has its own account, its own bill, and its own reputation to lose.

.. rubric:: The per-IP cap — ==the no-op, closed==

:attr:`DailyCaps.per_ip` has always been REQUIRED configuration: a phone channel refuses to boot
without it. And until this cut it enforced **nothing whatsoever**, for the reason this module used
to
confess in its own docstring — no client address reached the send path, because ``bookings`` had
nowhere to record one. A ceiling with nothing to count is not a ceiling: it is a knob that everybody
downstream (the operator who set it, the reviewer who saw it, the next author who trusted it)
believes in.

Closing it took THREE pieces, and any two of them would have been worse than none:

1. **the column** — ``bookings.source_ip`` (migration ``0011``);
2. **the value**, written on the one path a stranger can reach: the public router. ``api/public.py``
   stamps it from the declared proxy contract, while the admin's own bookings and the API key's
   carry
   ``None`` — and are therefore not capped by an address they never had;
3. **the enforcement** — :func:`enforce_ip_cap`, called in the NOTIFY handler's READ phase, right
   beside :func:`enforce_phone_cap`, before a provider is ever reached.

==Stopping at (1) would have been the worst outcome available==: a cap that *looks* applied, with a
schema behind it to prove it, and denies nothing. So the criterion this is signed off against is not
"the column exists" — it is "the cap DENIES A REAL SEND".

.. rubric:: Where it reads the address from, and why not from the step's payload

:func:`enforce_ip_cap` reads ``booking.source_ip`` off the row. The obvious alternative — copying
the
address into the NOTIFY step's payload at enqueue time — was rejected: the send path already HOLDS
the booking (``services/outbox._prepare_notify`` loads it to check the consent, the phone and the
staleness), so a payload copy buys nothing and costs a second source of truth for one fact,
snapshotted at a different moment. This codebase has a name for that, and a scar from it: it is why
the guest token was never given a ``tenant_id``. One fact, one home. ==The requirement is that the
address REACH the send path — not that it travel by any particular vehicle — and the row is the
vehicle that cannot drift.==
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, SentNotification

_logger = logging.getLogger(__name__)

CAP_WINDOW = timedelta(days=1)
"""The "daily" window. A ROLLING 24 hours, not a calendar day.

A calendar day would need a timezone to be meaningful (whose midnight?) and would hand an attacker a
free doubling by straddling it: N messages at 23:59 and N more at 00:01. A rolling window has one
answer and no seam.
"""

_PER_PHONE_SUFFIX = "DAILY_CAP_PER_PHONE"
_PER_IP_SUFFIX = "DAILY_CAP_PER_IP"


class SendRefused(Exception):
    """A send that will NEVER succeed. Distinct from a send that merely failed THIS time.

    The distinction decides what the outbox does next, and getting it wrong is expensive in both
    directions. Retried like a transient failure, a permanently-rejected number burns six attempts
    of exponential backoff and dead-letters — noise in the queue, and the message still never
    arrives. Skipped like a permanent one, a provider that was down for a minute silently loses a
    message it would have delivered on the next try.

    So: this is TERMINAL (the outbox retires the step with its reason, consuming no attempt), and
    :class:`ChannelUnavailable` is RETRYABLE."""


class QuotaExceeded(SendRefused):
    """The recipient (or the absence of one) puts this send outside the channel's daily cap."""


class PermanentSendError(SendRefused):
    """The provider rejected the message in a way that a retry cannot fix (a 4xx: bad number...)."""


class ChannelUnavailable(Exception):
    """The provider failed transiently, AND we know the message was not delivered.

    Both halves matter. "Transient" is what makes it retryable; "we know nothing was delivered" is
    what makes the retry SAFE. It covers a status the provider actually answered with (429, a 5xx,
    an unclassified 4xx) and a transport failure proving the request never left this machine (a
    refused connection, a connect timeout, an exhausted pool).

    RETRYABLE: back on the queue with backoff, like any other transient failure."""


class SendOutcomeUnknown(Exception):
    """==We do not know whether the guest got the message.== Neither retry nor retire is safe.

    The request left this machine and the answer was then lost — a read timeout, a connection
    dropped mid-response, a worker killed between the provider accepting and the ledger committing.
    The provider may have sent it. It may not have.

    Deliberately NOT a :class:`ChannelUnavailable` and NOT a :class:`SendRefused`, because both of
    the safe-looking options are wrong here:

    * **retry** and the guest can be messaged twice — and worse, the per-phone daily cap is DERIVED
      from the ledger, so a send nobody recorded also UNDER-COUNTS the very quota that protects that
      person from being messaged repeatedly;
    * **retire** and a message the guest never received is written off as handled, in silence.

    So the step is PARKED as ``unknown``: no automatic retry, an error-level log, and a status the
    ``/metrics`` backlog counts. A human checks the provider and resolves it
    (``aethercal-admin outbox resolve-unknown``). Guessing is the one thing this does not do."""


@runtime_checkable
class PhoneChannelSender(Protocol):
    """A :class:`~aethercal.server.channels.ChannelSender` that messages a PHONE — so it has caps.

    ==This protocol is where "fail-closed" stops being a promise and becomes a type.== A phone
    channel carries the ceilings it must not exceed, so the registry cannot hold a phone sender with
    none: there is no shape of this program in which an uncapped WhatsApp/SMS sender is reachable
    from the drain. A comment saying "remember to configure caps" is not a mechanism; this is.
    """

    channel: Channel
    caps: DailyCaps

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        """Deliver ``body`` to the phone number ``to``."""
        ...


@dataclass(frozen=True, slots=True)
class DailyCaps:
    """The ceilings a phone channel refuses to operate without.

    ``per_phone`` bounds how many messages ONE number may receive in :data:`CAP_WINDOW` — the
    protection for the stranger whose number somebody typed into the form. ``per_ip`` bounds how
    many one SOURCE may cause; see the module docstring for exactly how far that is enforced today.
    """

    per_phone: int
    per_ip: int

    def __post_init__(self) -> None:
        for name, value in (("per_phone", self.per_phone), ("per_ip", self.per_ip)):
            if value < 1:
                raise ValueError(
                    f"{name} cap must be a positive integer, got {value!r}. A zero or negative cap "
                    "is not 'unlimited' — it is a typo, and a phone channel must not boot with one."
                )

    @classmethod
    def from_env(cls, environ: Mapping[str, str], *, prefix: str) -> DailyCaps:
        """Read ``AETHERCAL_<prefix>_DAILY_CAP_PER_PHONE`` / ``..._PER_IP``. FAIL-CLOSED.

        Raises :class:`RuntimeError` naming the missing variable. The caller only reaches this once
        it already knows the channel is *meant* to be on (its credentials are present), so a missing
        cap here is a half-configured channel — the one state that must never come up sending."""
        per_phone = _require_int(environ, f"AETHERCAL_{prefix}_{_PER_PHONE_SUFFIX}")
        per_ip = _require_int(environ, f"AETHERCAL_{prefix}_{_PER_IP_SUFFIX}")
        return cls(per_phone=per_phone, per_ip=per_ip)


def _require_int(environ: Mapping[str, str], key: str) -> int:
    raw = environ.get(key)
    if not raw:
        raise RuntimeError(
            f"{key} is not set. A phone channel refuses to activate without its daily caps: the "
            "recipient comes from a public form, so an uncapped channel can be made to message "
            "strangers on the operator's own account. Set it, or leave the channel entirely "
            "unconfigured (which switches it off)."
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got {raw!r}.") from exc


async def phone_sends_in_window(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str,
    channel: Channel,
    since: datetime,
) -> int:
    """How many messages this tenant has really sent to ``phone`` on ``channel`` since ``since``.

    Read from the ``sent_notifications`` ledger, joined to the phone on the booking — the EFFECTIVE
    state, not a number this process has been keeping in a dict. It therefore survives a restart, a
    deploy, and a second worker, all of which would silently zero an in-memory counter.

    Counting across bookings is the point: the cap protects the PERSON whose number it is."""
    total = await session.scalar(
        select(func.count())
        .select_from(SentNotification)
        .join(Booking, Booking.id == SentNotification.booking_id)
        .where(
            SentNotification.tenant_id == tenant_id,
            SentNotification.channel == channel.value,
            SentNotification.sent_at >= since,
            Booking.guest_phone == phone,
        )
    )
    return int(total or 0)


async def enforce_phone_cap(
    session: AsyncSession,
    *,
    booking: Booking,
    channel: Channel,
    caps: DailyCaps,
    now: datetime,
) -> None:
    """Raise :class:`QuotaExceeded` unless this booking's phone is under the channel's daily cap.

    Called in the outbox handler's READ phase, where there is a session and a booking — and BEFORE
    the network call, so an over-cap message is never handed to the provider at all.

    .. rubric:: The quota is DERIVED, never CONSUMED here

    This function only READS. The quota IS the ``sent_notifications`` ledger, and a ledger row is
    written in exactly one place: after ``run_notify_effect``'s ``send()`` returns. A step retired
    before the provider was ever called — a missing template, a malformed one, a withdrawn consent —
    writes nothing, and therefore costs nothing.

    ==That is load-bearing, and it is why the check sits here rather than being "moved closer to the
    send".== If a retired step could spend the phone's budget, a tenant's template typo would
    SILENCE A REAL GUEST: their legitimate reminder would hit a ceiling raised by a message that was
    never sent — and it would not even error, the reminder would simply never arrive. Pinned by
    ``test_a_step_retired_before_sending_does_NOT_spend_the_phones_daily_quota``, which is built to
    fail the moment the count is taken over ATTEMPTS (outbox rows) instead of SENDS (ledger rows).

    .. rubric:: The residual, stated rather than left to be discovered

    The check is not atomic with the send: two workers could each read ``already == cap - 1`` and
    both send, overshooting by one per racing worker. It is bounded, and it is not reachable in the
    deployed shape — the scheduler runs in exactly ONE process (``deploy/README.md``) — so this is a
    spend/abuse ceiling, not a hard legal limit. Closing it properly means making the ledger insert
    the RESERVATION (reserve-first, as the transactional email path does), which cannot be done from
    here without holding a transaction across the network call — and R8 forbids precisely that."""
    phone = booking.guest_phone
    if not phone:
        # Nothing to key the cap on. Refuse, rather than sail through on a zero count.
        raise QuotaExceeded(
            f"daily-cap: the booking has no phone number, so a {channel.value} send cannot be "
            "capped and must not be attempted"
        )

    already = await phone_sends_in_window(
        session,
        tenant_id=booking.tenant_id,
        phone=phone,
        channel=channel,
        since=now - CAP_WINDOW,
    )
    if already >= caps.per_phone:
        raise QuotaExceeded(
            f"daily-cap: this number has already received {already} {channel.value} message(s) in "
            f"the last {CAP_WINDOW}, at or over the per-phone cap of {caps.per_phone}. The "
            "recipient comes from a public form; the cap is what stops a stranger being messaged "
            "on repeat."
        )


async def sends_from_ip_in_window(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ip: str,
    channel: Channel,
    since: datetime,
) -> int:
    """How many messages this tenant has really sent on ``channel`` because of ``source_ip``.

    Read from the ``sent_notifications`` ledger, joined to the address on the booking — the same
    EFFECTIVE-state discipline as :func:`phone_sends_in_window`, and for the same reason: an
    in-process counter is zeroed by every restart and kept privately by every second worker, so it
    would hold perfectly in a test and mean nothing in production.

    ==There is NO status filter, and that is criterion 17.== A free event type confirms DIRECTLY —
    ``create_booking`` writes ``CONFIRMED``, and no unpaid hold exists anywhere on this path — so a
    cap that only counted holds would cover exactly nothing. Every booking carrying this address
    counts, whatever state it is in.

    Counting ACROSS bookings is the whole point: the cheap way to defeat a per-booking ceiling is to
    make more bookings, and an address is the one thing an attacker cannot mint on demand.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(SentNotification)
        .join(Booking, Booking.id == SentNotification.booking_id)
        .where(
            SentNotification.tenant_id == tenant_id,
            SentNotification.channel == channel.value,
            SentNotification.sent_at >= since,
            Booking.source_ip == source_ip,
        )
    )
    return int(total or 0)


async def enforce_ip_cap(
    session: AsyncSession,
    *,
    booking: Booking,
    channel: Channel,
    caps: DailyCaps,
    now: datetime,
) -> None:
    """Raise :class:`QuotaExceeded` unless the address behind this booking is under the daily cap.

    Called in the outbox handler's READ phase, beside :func:`enforce_phone_cap` and BEFORE the
    network call — so an over-cap message is never handed to a provider at all.

    .. rubric:: A booking with NO address is NOT capped — the deliberate asymmetry

    :func:`enforce_phone_cap` REFUSES a booking with no phone: there, the missing value IS the thing
    being messaged, and "the count came back zero, so it is under the cap" is the hole every
    unbounded send walks straight through.

    Here the missing value means something else entirely: ==this booking did not come through the
    public form.== The host booked it by hand in the admin, or the business's own integration
    created
    it with its API key. Refusing those would silence a host's own appointments because a stranger,
    somewhere, was abusing the public page. So ``None`` means "not capped", never "capped at zero" —
    and an attacker cannot reach that branch, because they cannot make the public router forget to
    stamp the address it has just resolved.

    .. rubric:: The residual, stated rather than left to be discovered

    Like the per-phone cap, the check is not atomic with the send: two workers could each read
    ``already == cap - 1`` and both send, overshooting by one per racing worker. It is bounded, and
    it is not reachable in the deployed shape (the scheduler runs in exactly ONE process —
    ``deploy/README.md``). This is an abuse ceiling, not a legal limit.
    """
    source_ip = booking.source_ip
    if not source_ip:
        return

    already = await sends_from_ip_in_window(
        session,
        tenant_id=booking.tenant_id,
        source_ip=source_ip,
        channel=channel,
        since=now - CAP_WINDOW,
    )
    if already >= caps.per_ip:
        raise QuotaExceeded(
            f"daily-cap: this source address has already caused {already} {channel.value} "
            f"message(s) in the last {CAP_WINDOW}, at or over the per-ip cap of {caps.per_ip}. The "
            "booking form is PUBLIC: this cap is what stops one caller turning a business's own "
            "messaging account into a spam cannon."
        )


__all__ = [
    "CAP_WINDOW",
    "ChannelUnavailable",
    "DailyCaps",
    "PermanentSendError",
    "PhoneChannelSender",
    "QuotaExceeded",
    "SendOutcomeUnknown",
    "SendRefused",
    "enforce_ip_cap",
    "enforce_phone_cap",
    "phone_sends_in_window",
    "sends_from_ip_in_window",
]
