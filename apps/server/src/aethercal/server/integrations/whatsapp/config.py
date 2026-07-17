"""Evolution API configuration, sourced from the environment (RF-19: no secrets in the source).

Modeled on :class:`~aethercal.server.integrations.smtp.config.SmtpConfig`: a frozen dataclass with a
``from_env`` classmethod reading an explicit ``environ`` mapping. ==A service NEVER reads
``os.environ`` itself== — the process edge does that once, and everything below takes its
configuration as an argument, which is what makes all of this testable without monkeypatching the
environment.

Three states, and the middle one is the whole point:

* **unconfigured** (no credentials at all) → ``None``. The channel is simply absent from the
  registry and its steps are SKIPPED with a reason. A disabled feature, not an error;
* **half-configured** (some credentials, or credentials without caps) → :class:`RuntimeError`.
  ==A phone channel must never come up *sending* but *uncapped*.== The recipient comes from a public
  form, so an uncapped channel can be made to message strangers on the operator's own account — and
  the symptom of the missing cap would be the bill, not an error;
* **configured** → an :class:`EvolutionConfig`, caps included.

.. rubric:: ==``base_url`` is no longer only the operator's. Read this before trusting it.==

This module used to say that ``base_url`` was "operator configuration, read only from the
environment... there is no user in this loop, only the operator who runs the process", and concluded
that it therefore needed no SSRF guard. **B-03bis made that false.** A business now brings its own
WhatsApp credential, ``base_url`` included, so the value can arrive from a THIRD PARTY — and a
config this server obeys, supplied by somebody else, is exactly what the webhook URLs are guarded
for.

So the guard is applied where the value becomes a client, and only to the values that need it:
:func:`~aethercal.server.services.tenant_senders._assert_target_reachable` puts a
``CredentialSource.TENANT`` URL through ``webhooks.ssrf.assert_target_allowed`` (https, public
address, validated by RESOLVED IP), and leaves a ``CredentialSource.INSTANCE`` URL — the env read
here — untouched, because that one really is the operator configuring their own instance.
==Provenance decides, not the field name.==

A ``from_env`` value is therefore still trusted, and a self-hoster's Evolution on
``http://192.168.1.50`` keeps working. That is why this file needs no guard of its own — and why it
must not be read as evidence that the value is trusted wherever else it turns up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aethercal.server.integrations.messaging.guard import DailyCaps

_BASE_URL_ENV = "AETHERCAL_WHATSAPP_BASE_URL"
_INSTANCE_ENV = "AETHERCAL_WHATSAPP_INSTANCE"
_API_KEY_ENV = "AETHERCAL_WHATSAPP_API_KEY"

_CREDENTIAL_ENVS = (_BASE_URL_ENV, _INSTANCE_ENV, _API_KEY_ENV)

_CAP_PREFIX = "WHATSAPP"


@dataclass(frozen=True, slots=True)
class EvolutionConfig:
    """Everything needed to send one text through an Evolution instance."""

    base_url: str
    instance: str
    api_key: str
    caps: DailyCaps

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> EvolutionConfig | None:
        """Build from ``AETHERCAL_WHATSAPP_*``. ``None`` when the channel is entirely unconfigured.

        Raises :class:`RuntimeError` when it is HALF-configured — some credentials but not all, or
        credentials but no caps. Silence is reserved for "off"; anything else is loud."""
        present = [key for key in _CREDENTIAL_ENVS if environ.get(key)]
        if not present:
            return None  # The channel is off. That is a decision, not a failure.

        missing = [key for key in _CREDENTIAL_ENVS if not environ.get(key)]
        if missing:
            raise RuntimeError(
                f"the WhatsApp channel is half-configured: {', '.join(present)} is set but "
                f"{', '.join(missing)} is not. Set all of them, or none (which switches the "
                "channel off)."
            )

        # Caps are read AFTER we know the channel is meant to be on, so their absence is
        # unambiguously a misconfiguration rather than "the operator did not want WhatsApp".
        caps = DailyCaps.from_env(environ, prefix=_CAP_PREFIX)
        return cls(
            base_url=environ[_BASE_URL_ENV].rstrip("/"),
            instance=environ[_INSTANCE_ENV],
            api_key=environ[_API_KEY_ENV],
            caps=caps,
        )


__all__ = ["EvolutionConfig"]
