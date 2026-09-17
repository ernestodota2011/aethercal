"""How a provider's answer is classified: permanent, transient, or unknown (RF-24).

ONE rule, for every phone provider. It lived twice — once in the Evolution adapter and once in the
Twilio one — and both copies carried the same defect, which is exactly what a duplicated rule buys
you: it gets fixed once and survives in the other copy.

.. rubric:: The default is RETRY, and that asymmetry is the whole point

The old rule was ``429 or 5xx → transient, everything else → permanent``. So **408** (Request
Timeout), **425** (Too Early) and *every 4xx nobody had thought of* retired the step **forever**:
the guest's reminder was never sent, and nothing errored — just a ``skipped`` row carrying a reason
nobody reads. A silently missing message is precisely the one nobody notices, and here we would have
been the ones causing it.

So the permanent set is an **explicit allow-list of statuses we can prove a retry cannot fix**, and
everything else — known-transient, unclassified, or simply new — retries. The costs are not
symmetric:

* a needless RETRY costs at worst a duplicate message (annoying, visible, recoverable);
* a needless RETIREMENT costs **the message** (invisible, and unrecoverable).

With the two in doubt, buy the recoverable failure.

.. rubric:: What is genuinely permanent

An invalid or unroutable number, an unknown instance, bad credentials, a malformed request, a
recipient who is blocked. Waiting changes none of them. ``401``/``403`` are here not because a retry
would be *harmful* but because it is *pointless*: an operator has to go fix the credentials, and
burning six backoff attempts first only delays them finding out.
"""

from __future__ import annotations

import httpx

from aethercal.server.integrations.messaging.guard import ChannelUnavailable, PermanentSendError

PERMANENT_STATUSES = frozenset(
    {
        400,  # Bad Request — a malformed payload, or an invalid number
        401,  # Unauthorized — the operator's credentials are wrong
        403,  # Forbidden — not permitted to message this recipient (blocked, unsubscribed)
        404,  # Not Found — an unknown instance / account / endpoint
        405,  # Method Not Allowed
        406,  # Not Acceptable
        410,  # Gone — permanently unavailable
        415,  # Unsupported Media Type
        422,  # Unprocessable Entity — the provider parsed it and rejected the content
    }
)
"""The ONLY statuses that retire a step. Everything else retries — see the module docstring.

Deliberately absent, and every one of them was a message the previous rule threw away:

* **408** Request Timeout — the provider was slow. It will not be slow forever.
* **425** Too Early — it is literally asking us to come back.
* **429** Too Many Requests — the provider's own rate limit, and the most common failure a messaging
  API produces under load. Retiring the step here means retiring it exactly when traffic peaks,
  which is exactly when the reminders matter most.
* **409** Conflict — ambiguous, and ambiguity retries rather than dies.
"""

RECIPIENT_REJECTED_STATUSES = frozenset({400, 403})
"""The permanent statuses that are about the GUEST rather than the account.

A subset of :data:`PERMANENT_STATUSES`, deliberately declared apart because the two questions have
different answers: every status here retires the step on this channel, and only these earn the OTP's
one-shot fallback to the other channel. ``404`` is NOT here on purpose: providers use it for an
unknown instance as readily as for a missing recipient, and a bad instance is not a reason to try
SMS against the same broken account."""


class RecipientRejectedError(PermanentSendError):
    """The RECIPIENT is the problem, and the message may still be deliverable another way.

    ==The distinction exists for exactly one caller: the OTP's channel fallback.== A number that is
    not on WhatsApp — the provider parses our well-formed request and answers "this recipient does
    not exist there" — is worth ONE retry over SMS. A permanent PROVIDER error (bad credentials, an
    unknown instance, a malformed request) is not: SMS would hit the same broken account, so trying
    it only burns a second provider call and muddies the operator's log. Both are permanent and both
    retire the step; only the first is about the guest.
    """


def raise_for_send_status(response: httpx.Response, *, provider: str) -> None:
    """Classify a provider's HTTP answer. Success returns; anything else raises.

    :raises RecipientRejectedError: the status is permanent AND about the recipient (400/403) — and
        another configured channel may still reach this guest.
    :raises PermanentSendError: any other status in :data:`PERMANENT_STATUSES` — a retry cannot fix
        it, so the step is retired with its reason instead of burning six attempts of backoff.
    :raises ChannelUnavailable: anything else, INCLUDING every status nobody has classified yet. The
        step retries with backoff, because losing a message costs more than sending one twice.
    """
    if response.is_success:
        return

    status = response.status_code
    # Truncated, and it is the PROVIDER's text — never our credentials, which only ever live in the
    # request. An exception message ends up in the log and in the outbox row.
    detail = response.text[:200]

    if status in RECIPIENT_REJECTED_STATUSES:
        raise RecipientRejectedError(
            f"provider-rejected: {provider} answered {status} for this RECIPIENT (it does not "
            f"exist on that channel, or is blocked there): {detail}"
        )
    if status in PERMANENT_STATUSES:
        raise PermanentSendError(
            f"provider-rejected: {provider} answered {status}, which a retry cannot fix (an "
            f"unknown account, or bad credentials): {detail}"
        )

    raise ChannelUnavailable(
        f"{provider} answered {status}; the step retries with backoff. Anything not on the "
        "permanent list retries ON PURPOSE — a needless retry costs a duplicate, a needless "
        f"retirement costs the message: {detail}"
    )


def is_definitely_undelivered(error: httpx.HTTPError) -> bool:
    """Whether this transport failure PROVES the provider never saw the request.

    The distinction decides whether a retry is safe or whether the outcome is UNKNOWN, and it turns
    on one question: **did the request leave this machine?**

    * We never connected (DNS, refused, connect timeout, no free pool slot) → the request was never
      transmitted. Nothing was delivered, so a retry is safe and correct.
    * We connected, wrote the request, and THEN the read timed out or the connection broke → the
      provider may well have accepted it and sent the message. Retrying could message the guest
      twice; retiring could lose the message. Neither is acceptable blind, so the caller escalates
      it as an UNKNOWN outcome rather than guessing which.
    """
    return isinstance(
        error,
        httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout | httpx.UnsupportedProtocol,
    )


__all__ = [
    "PERMANENT_STATUSES",
    "RECIPIENT_REJECTED_STATUSES",
    "RecipientRejectedError",
    "is_definitely_undelivered",
    "raise_for_send_status",
]
