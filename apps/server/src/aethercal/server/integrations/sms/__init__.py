"""The SMS channel, backed by Twilio (RF-24).

.. warning::

   **NOT VERIFIED AGAINST A LIVE ACCOUNT.** There is no Twilio account for this project, so this
   adapter has **never sent a real message**. It is built and tested against Twilio's *documented*
   Messages API — the request it builds, the auth it uses, and how it classifies each response — and
   it ships **unverified-live**, which is exactly what the design says it is.

   Nothing here should be read as evidence that it works end to end against Twilio: the contract
   tests prove only that we send what the documentation says to send. First live use should be
   treated as a first integration, not as a regression.
"""

from __future__ import annotations

from aethercal.server.integrations.sms.config import TwilioConfig
from aethercal.server.integrations.sms.sender import TwilioSmsSender

__all__ = ["TwilioConfig", "TwilioSmsSender"]
