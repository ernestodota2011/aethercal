"""Twilio configuration, sourced from the environment (RF-19: no secrets in the source).

Same three-state contract as every other channel config — unconfigured → ``None`` (the channel is
off); half-configured → :class:`RuntimeError` (==never *sending* but *uncapped*==); configured → a
:class:`TwilioConfig` with its caps. See :mod:`aethercal.server.integrations.whatsapp.config` for
the reasoning in full.

``base_url`` defaults to Twilio's public API; the override exists so an operator can point at a
regional edge or a local mock.

.. rubric:: ==It is no longer only the operator's, and this used to say otherwise==

This module used to state that ``base_url`` "is never derived from inbound data, which is why it
legitimately bypasses the SSRF guard". **B-03bis made that false**: a business now brings its own
SMS credential, and may set ``base_url`` with it. See
:mod:`aethercal.server.integrations.whatsapp.config` for the reasoning in full. The short version:
a ``CredentialSource.TENANT`` URL goes through the egress guard
(:func:`~aethercal.server.services.tenant_senders._assert_target_reachable`) and a
``CredentialSource.INSTANCE`` one does not, because provenance decides and the operator is not
their own threat model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aethercal.server.integrations.messaging.guard import DailyCaps

_ACCOUNT_SID_ENV = "AETHERCAL_SMS_ACCOUNT_SID"
_AUTH_TOKEN_ENV = "AETHERCAL_SMS_AUTH_TOKEN"
_FROM_NUMBER_ENV = "AETHERCAL_SMS_FROM_NUMBER"
_BASE_URL_ENV = "AETHERCAL_SMS_BASE_URL"

_CREDENTIAL_ENVS = (_ACCOUNT_SID_ENV, _AUTH_TOKEN_ENV, _FROM_NUMBER_ENV)

_DEFAULT_BASE_URL = "https://api.twilio.com"
_CAP_PREFIX = "SMS"


@dataclass(frozen=True, slots=True)
class TwilioConfig:
    """Everything needed to send one SMS through Twilio's Messages API."""

    account_sid: str
    auth_token: str
    from_number: str
    caps: DailyCaps
    base_url: str = _DEFAULT_BASE_URL

    @property
    def messages_url(self) -> str:
        """The documented endpoint: ``/2010-04-01/Accounts/{AccountSid}/Messages.json``."""
        return f"{self.base_url}/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> TwilioConfig | None:
        """Build from ``AETHERCAL_SMS_*``. ``None`` when the channel is entirely unconfigured.

        Raises :class:`RuntimeError` when it is HALF-configured — some credentials but not all, or
        credentials but no caps."""
        present = [key for key in _CREDENTIAL_ENVS if environ.get(key)]
        if not present:
            return None  # The channel is off. That is a decision, not a failure.

        missing = [key for key in _CREDENTIAL_ENVS if not environ.get(key)]
        if missing:
            raise RuntimeError(
                f"the SMS channel is half-configured: {', '.join(present)} is set but "
                f"{', '.join(missing)} is not. Set all of them, or none (which switches the "
                "channel off)."
            )

        caps = DailyCaps.from_env(environ, prefix=_CAP_PREFIX)
        return cls(
            account_sid=environ[_ACCOUNT_SID_ENV],
            auth_token=environ[_AUTH_TOKEN_ENV],
            from_number=environ[_FROM_NUMBER_ENV],
            caps=caps,
            base_url=(environ.get(_BASE_URL_ENV) or _DEFAULT_BASE_URL).rstrip("/"),
        )


__all__ = ["TwilioConfig"]
