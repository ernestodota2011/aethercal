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

The response is classified by the ONE shared rule in
:mod:`aethercal.server.integrations.messaging.status`: an explicit allow-list of permanent statuses,
and **everything else retries** (a needless retry costs a duplicate; a needless retirement costs the
message). A request we wrote and whose answer we then lost is :class:`SendOutcomeUnknown` — neither
retry nor retire is safe blind.
"""

from __future__ import annotations

import logging

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
from aethercal.server.integrations.sms.config import TwilioConfig

_logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 15.0
"""Bounded per request; the drain's provider ceiling is the outer backstop, not the only one."""


class TwilioSmsSender:
    """The live SMS phone-channel sender, backed by Twilio's Messages API. Unverified live."""

    channel = Channel.SMS

    def __init__(self, config: TwilioConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http_client
        # Both ceilings are now REAL — see the note in the WhatsApp sender. The boot warning that
        # used to stand here (the per-IP cap counts nothing) is retired with the gap it described.
        self.caps: DailyCaps = config.caps

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
            if is_definitely_undelivered(exc):
                # We never connected: the request was never transmitted, so a retry is safe.
                raise ChannelUnavailable(f"the Twilio API could not be reached: {exc}") from exc
            # We wrote the request and then lost the answer. Twilio MAY have sent it. Do not guess.
            raise SendOutcomeUnknown(
                f"Twilio took the request and the answer was lost ({exc!r}); whether the guest was "
                "messaged is UNKNOWN"
            ) from exc

        raise_for_send_status(response, provider="Twilio")


__all__ = ["SEND_TIMEOUT_SECONDS", "TwilioSmsSender"]
