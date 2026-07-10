"""The email-sending seam (RF-08): a small :class:`EmailSender` protocol and its live SMTP backing.

Every caller (the notification service, the reminder job) depends only on the :class:`EmailSender`
protocol, so tests inject a fake recording sender and the process wires the real
:class:`SmtpEmailSender`. The SMTP send is the only thing here that touches the network; it is
marked ``# pragma: no cover - live`` and configured entirely from :class:`SmtpConfig` (no secret).
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol, runtime_checkable

import aiosmtplib

from aethercal.server.integrations.smtp.config import SmtpConfig

# Port 465 speaks TLS from the first byte (implicit TLS); the submission port 587 upgrades a plain
# connection with STARTTLS. This decides which knob the ``use_tls`` config flag drives.
_IMPLICIT_TLS_PORT = 465


@runtime_checkable
class EmailSender(Protocol):
    """Anything that can deliver a composed message. The seam every email caller depends on."""

    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` (already composed with Subject/To/body/attachment)."""
        ...


class SmtpEmailSender:
    """The live :class:`EmailSender`, backed by ``aiosmtplib`` and an :class:`SmtpConfig`."""

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    async def send(self, message: EmailMessage) -> None:  # pragma: no cover - live
        """Stamp the ``From`` header from config (if unset) and send over SMTP."""
        if message["From"] is None:
            message["From"] = self._config.from_addr
        implicit_tls = self._config.use_tls and self._config.port == _IMPLICIT_TLS_PORT
        await aiosmtplib.send(
            message,
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            use_tls=implicit_tls,
            start_tls=self._config.use_tls and not implicit_tls,
        )


__all__ = ["EmailSender", "SmtpEmailSender"]
