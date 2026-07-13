"""The Twilio adapter: one :class:`ChannelSender` that sends one SMS (RF-24).

.. warning::

   **Unverified live.** No Twilio account exists for this project, so this has never sent a real
   message. Everything below is built against Twilio's *documented* Messages API and proven only
   against that documentation (see ``tests/test_sms_channel.py``). See the package docstring.

The documented contract::

    POST {base_url}/2010-04-01/Accounts/{AccountSid}/Messages.json
    Authorization: Basic base64(AccountSid:AuthToken)
    Content-Type: application/x-www-form-urlencoded

    To=%2B13055550123&From=%2B13055559999&Body=...

Form-encoded (Twilio does not take JSON here), HTTP Basic auth, and E.164 with the ``+`` INTACT —
the opposite of Evolution's bare digits, which is precisely why each provider gets its own adapter
instead of one "send a message" helper with an if-statement in it.

The response is classified the same way as every other channel: 4xx → :class:`SendRefused`
(terminal, retire the step), 429/5xx/network → :class:`ChannelUnavailable` (retry with backoff).
"""

from __future__ import annotations

import logging

import httpx

from aethercal.server.channels import Channel
from aethercal.server.integrations.messaging.guard import (
    ChannelUnavailable,
    DailyCaps,
    PermanentSendError,
    warn_if_ip_cap_unenforceable,
)
from aethercal.server.integrations.sms.config import TwilioConfig

_logger = logging.getLogger(__name__)

_TOO_MANY_REQUESTS = 429
_SERVER_ERROR_FLOOR = 500

SEND_TIMEOUT_SECONDS = 15.0
"""Bounded per request; the drain's provider ceiling is the outer backstop, not the only one."""


class TwilioSmsSender:
    """The live SMS phone-channel sender, backed by Twilio's Messages API. Unverified live."""

    channel = Channel.SMS

    def __init__(self, config: TwilioConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http_client
        self.caps: DailyCaps = config.caps
        warn_if_ip_cap_unenforceable(channel=self.channel, caps=config.caps)

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        """Send ``body`` to the E.164 number ``to``. ``subject`` is ignored — SMS has none."""
        if subject is not None:
            _logger.warning(
                "the SMS channel has no subject line; the subject %r is being dropped rather than "
                "prepended to the body",
                subject,
            )
        recipient = to.strip()
        if not recipient:
            raise PermanentSendError("provider-rejected: an SMS send needs a recipient")

        try:
            response = await self._http.post(
                self._config.messages_url,
                auth=(self._config.account_sid, self._config.auth_token),
                data={"To": recipient, "From": self._config.from_number, "Body": body},
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise ChannelUnavailable(f"the Twilio API could not be reached: {exc}") from exc

        _raise_for_status(response)


def _raise_for_status(response: httpx.Response) -> None:
    """Classify Twilio's answer. Permanent → retire the step; transient → retry it."""
    if response.is_success:
        return

    status = response.status_code
    detail = response.text[:200]
    if status == _TOO_MANY_REQUESTS or status >= _SERVER_ERROR_FLOOR:
        raise ChannelUnavailable(
            f"Twilio answered {status} (transient); the step retries with backoff: {detail}"
        )
    raise PermanentSendError(
        f"provider-rejected: Twilio answered {status}, which a retry cannot fix (an unreachable "
        f"or unroutable number, or bad credentials): {detail}"
    )


__all__ = ["SEND_TIMEOUT_SECONDS", "TwilioSmsSender"]
