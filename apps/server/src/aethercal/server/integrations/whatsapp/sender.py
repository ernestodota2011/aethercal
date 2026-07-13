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

The HTTP status is CLASSIFIED, never merely "not 2xx → raise": a 4xx (a malformed number, an
unknown instance) can never succeed, so it is a :class:`SendRefused` and the step is retired; a 429,
a 5xx or a network error is :class:`ChannelUnavailable`, and retries with backoff. Collapsing
the two either fills the dead-letter with noise or throws away a message the provider would have
accepted thirty seconds later.
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
    warn_if_ip_cap_unenforceable,
)
from aethercal.server.integrations.whatsapp.config import EvolutionConfig

_logger = logging.getLogger(__name__)

_NON_DIGITS = re.compile(r"\D")

_TOO_MANY_REQUESTS = 429
_SERVER_ERROR_FLOOR = 500

SEND_TIMEOUT_SECONDS = 15.0
"""Bounded per request. The drain also caps every provider call below the lease TTL, but a client
with no timeout of its own would sit on a socket until that outer guard fired."""


class EvolutionWhatsAppSender:
    """The live WhatsApp phone-channel sender, backed by an Evolution instance."""

    channel = Channel.WHATSAPP

    def __init__(self, config: EvolutionConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http_client
        self.caps: DailyCaps = config.caps
        # The channel is coming up. Say plainly what the per-IP cap does and does not do, once,
        # here — rather than letting an operator infer a protection they do not have.
        warn_if_ip_cap_unenforceable(channel=self.channel, caps=config.caps)

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
            raise ChannelUnavailable(f"the Evolution API could not be reached: {exc}") from exc

        _raise_for_status(response)


def _raise_for_status(response: httpx.Response) -> None:
    """Classify the provider's answer. Permanent → retire the step; transient → retry it."""
    if response.is_success:
        return

    status = response.status_code
    detail = response.text[:200]
    if status == _TOO_MANY_REQUESTS or status >= _SERVER_ERROR_FLOOR:
        raise ChannelUnavailable(
            f"the Evolution API answered {status} (transient); the step retries with backoff: "
            f"{detail}"
        )
    raise PermanentSendError(
        f"provider-rejected: the Evolution API answered {status}, which a retry cannot fix "
        f"(a malformed number, or an unknown instance): {detail}"
    )


__all__ = ["SEND_TIMEOUT_SECONDS", "EvolutionWhatsAppSender"]
