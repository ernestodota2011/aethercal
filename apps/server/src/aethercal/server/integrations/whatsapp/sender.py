"""The Evolution API adapter: one :class:`ChannelSender` that sends one WhatsApp text (RF-24).

Talks to Evolution's documented text endpoint and nothing else::

    POST {base_url}/message/sendText/{instance}
    apikey: <api key>
    {"number": "13055550123", "text": "...", "linkPreview": false}

Two details that are decisions, not incidentals:

* **the number is sent as bare digits.** Evolution addresses a chat by its JID, whose user part has
  no ``+``; passing E.164 straight through gets the message silently addressed to nobody. The
  booking stores E.164 (which is right), so the adapter — the one place that knows this provider's
  quirk — strips it.
* **``linkPreview`` is false.** The body can carry guest-supplied text; the renderer already defangs
  any link inside it, and this makes sure the provider does not go and render a preview card for
  anything that slipped through. Belt and braces on the phishing surface.

The provider's answer is CLASSIFIED by the ONE shared rule in
:mod:`aethercal.server.integrations.messaging.status`: an explicit allow-list of permanent statuses,
and **everything else retries**. That default is the point — a needless retry costs a duplicate, a
needless retirement costs the message.

And the outcome has THREE cases, not two: a request we wrote and whose answer we then lost is a
:class:`SendOutcomeUnknown`, because the provider may well have sent it, and neither retrying nor
retiring is safe blind.
"""

from __future__ import annotations

import logging
import re

import httpx

from aethercal.server.channels import Channel
from aethercal.server.integrations.messaging.guard import (
    ChannelUnavailable,
    DailyCaps,
    PermanentSendError,
    SendOutcomeUnknown,
)
from aethercal.server.integrations.messaging.status import (
    is_definitely_undelivered,
    raise_for_send_status,
)
from aethercal.server.integrations.whatsapp.config import EvolutionConfig

_logger = logging.getLogger(__name__)

_NON_DIGITS = re.compile(r"\D")

SEND_TIMEOUT_SECONDS = 15.0
"""Bounded per request. The drain also caps every provider call below the lease TTL, but a client
with no timeout of its own would sit on a socket until that outer guard fired."""


class EvolutionWhatsAppSender:
    """The live WhatsApp phone-channel sender, backed by an Evolution instance."""

    channel = Channel.WHATSAPP

    def __init__(self, config: EvolutionConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http_client
        # Both ceilings are now REAL. The boot warning that used to stand here said the per-IP cap
        # counted nothing, because no client address reached the send path; `bookings.source_ip` and
        # `guard.enforce_ip_cap` closed that, so the warning is gone rather than left to go stale
        # into a lie. (A retired confession that keeps being printed is how a fixed bug gets
        # re-reported for a year.)
        self.caps: DailyCaps = config.caps

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        """Send ``body`` to the E.164 number ``to``. ``subject`` is ignored — WhatsApp has none."""
        if subject is not None:
            # Not an error, but it means a caller believes this channel has subjects. It does not,
            # and silently dropping the text would lose whatever they put in there.
            _logger.warning(
                "the WhatsApp channel has no subject line; the subject %r is being dropped rather "
                "than prepended to the body",
                subject,
            )
        number = _NON_DIGITS.sub("", to)
        if not number:
            raise PermanentSendError(
                f"provider-rejected: {to!r} has no digits, so it is not a phone number"
            )

        url = f"{self._config.base_url}/message/sendText/{self._config.instance}"
        try:
            response = await self._http.post(
                url,
                headers={"apikey": self._config.api_key},
                json={"number": number, "text": body, "linkPreview": False},
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            if is_definitely_undelivered(exc):
                # We never connected: the request was never transmitted, so a retry is safe.
                raise ChannelUnavailable(f"the Evolution API could not be reached: {exc}") from exc
            # We wrote the request and then lost the answer. The provider MAY have sent it. Do not
            # guess — neither a retry nor a retirement is safe, so escalate the ambiguity.
            raise SendOutcomeUnknown(
                f"the Evolution API took the request and the answer was lost ({exc!r}); whether "
                "the guest was messaged is UNKNOWN"
            ) from exc

        raise_for_send_status(response, provider="the Evolution API")


__all__ = ["SEND_TIMEOUT_SECONDS", "EvolutionWhatsAppSender"]
