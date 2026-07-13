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

.. rubric:: The per-IP cap, honestly

:attr:`DailyCaps.per_ip` is **required configuration** (the design mandates it, and this module
enforces that it is set). It is NOT yet enforced at send time, and that is not an oversight left to
be discovered later: **no client IP reaches the send path**. A booking does not record the address
it was created from — ``bookings`` has no such column — so at drain time there is nothing to key an
IP cap on. The public booking page has its own per-IP rate limiter on its POST handlers, which is
the flood control that exists today.

Rather than ship a knob that reads as protection and quietly enforces nothing,
:func:`warn_if_ip_cap_unenforceable` says so out loud at channel construction. Closing it for real
needs a ``bookings.source_ip`` column carried into the workflow step's payload — a schema change,
and this batch is deliberately limited to one migration with one owner.
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


def warn_if_ip_cap_unenforceable(*, channel: Channel, caps: DailyCaps) -> None:
    """Say, out loud and at boot, that the configured per-IP cap has nothing to count yet.

    An operator who sets ``DAILY_CAP_PER_IP=50`` believes they bought a protection. Today they did
    not: no client IP reaches the send path, because a booking never records the address it was made
    from. Letting them keep that belief is precisely the silent no-op this project exists to kill —
    so the gap is stated at boot, with its fix, instead of being discovered from an invoice."""
    _logger.warning(
        "%s: DAILY_CAP_PER_IP is configured (%d) but is NOT enforced at send time — a booking does "
        "not record the IP it was created from, so the drain has nothing to count. The per-PHONE "
        "cap (%d) IS enforced. Closing this needs a bookings.source_ip column carried into the "
        "workflow step's payload; until then the public booking page's own per-IP rate limit is "
        "the flood control in place.",
        channel.value,
        caps.per_ip,
        caps.per_phone,
    )


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


__all__ = [
    "CAP_WINDOW",
    "ChannelUnavailable",
    "DailyCaps",
    "PermanentSendError",
    "PhoneChannelSender",
    "QuotaExceeded",
    "SendOutcomeUnknown",
    "SendRefused",
    "enforce_phone_cap",
    "phone_sends_in_window",
    "warn_if_ip_cap_unenforceable",
]
