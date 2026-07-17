"""The email-sending seam (RF-08): a small :class:`EmailSender` protocol and its live SMTP backing.

Every caller (the notification service, the reminder job) depends only on the :class:`EmailSender`
protocol, so tests inject a fake recording sender and the process wires the real
:class:`SmtpEmailSender`. The SMTP send is the only thing here that touches the network; it is
marked ``# pragma: no cover - live`` and configured entirely from :class:`SmtpConfig` (no secret).
"""

from __future__ import annotations

import socket
from collections.abc import Awaitable, Callable
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


SmtpConnector = Callable[[], Awaitable[socket.socket]]
"""Opens a socket to an ALREADY-VALIDATED address. ==The seam that closes SMTP rebinding.==

Wired by :func:`~aethercal.server.services.tenant_senders._assert_smtp_host_reachable` for a
business's OWN relay, and ``None`` for the operator's — see :class:`SmtpEmailSender`."""


class SmtpEmailSender:
    """The live :class:`EmailSender`, backed by ``aiosmtplib`` and an :class:`SmtpConfig`.

    .. rubric:: ==``connect`` is what makes the egress guard true rather than hopeful==

    Without it, ``aiosmtplib`` resolves ``config.host`` itself when it opens the socket — so a guard
    that checked that host's address a moment earlier has only checked *a previous answer to a
    question it is about to ask again*. A business that controls its own DNS answers public for the
    guard and ``127.0.0.1`` for the connect, and this server relays its mail through the operator's
    own local MTA: ==an open relay wearing the operator's IP reputation==. Unlike the HTTP path
    there
    is no certificate to fall back on — ``use_tls`` is the business's own field, and port 25 on
    loopback talks plaintext happily. The address check is not one layer of several. It is the
    layer.

    So a business's relay is dialed through a connector that re-validates and pins at connect time,
    and ``aiosmtplib`` is handed the **already-connected socket**: it performs no lookup of its own.

    ==TLS does not weaken, and that is a documented property of the library rather than a hope.==
    ``aiosmtplib`` REQUIRES ``hostname`` when given a socket with TLS ("If using a socket with TLS,
    hostname is required") and assigns it to the TLS ``server_hostname`` — so SNI and
    certificate-hostname verification stay bound to the real name while the socket goes to the
    pinned address. Exactly the property ``webhooks.pinning`` gives the HTTP path.

    ``connect=None`` means "resolve as you always did", which is right for exactly one caller: the
    OPERATOR's own relay, configured by the person running the process. Provenance decides here too.
    """

    def __init__(self, config: SmtpConfig, *, connect: SmtpConnector | None = None) -> None:
        self._config = config
        self._connect = connect

    async def send(self, message: EmailMessage) -> None:
        """Stamp the ``From`` header from config (if unset) and send over SMTP.

        ==This owns the socket completely, or not at all.== When a connector is wired the socket is
        opened here and closed here, in a ``finally``, on every path — success, refusal, or the
        provider hanging up mid-conversation. ``socket.close()`` is idempotent, so closing one
        ``aiosmtplib`` has already finished with costs nothing, and closing one it never reached
        prevents a descriptor leaked per drain item. Half-ownership of a socket on the egress path
        is
        exactly the fragility this design refused to accept.
        """
        if message["From"] is None:
            message["From"] = self._config.from_addr
        implicit_tls = self._config.use_tls and self._config.port == _IMPLICIT_TLS_PORT
        # A refusal from the connector (a rebind; a target that resolved inward) propagates
        # untouched. The email path writes no provider-call marker — that is PHONE-only, and
        # deliberately so, because aiosmtplib's failures are undifferentiated — so an exception here
        # simply fails the intent: retried with backoff, dead-lettered after the budget, and never
        # parked as "unknown". Same outcome as the HTTP path reaches by a longer route.
        sock = await self._connect() if self._connect is not None else None
        try:
            await aiosmtplib.send(  # pragma: no cover - the live network call
                message,
                hostname=self._config.host,
                # `port` is refused alongside `sock` ("If using a socket, port is not required"):
                # the socket already IS the destination, and a second opinion about it would only be
                # a second chance to disagree.
                port=None if sock is not None else self._config.port,
                sock=sock,
                username=self._config.username,
                password=self._config.password,
                use_tls=implicit_tls,
                start_tls=self._config.use_tls and not implicit_tls,
            )
        finally:
            if sock is not None:
                sock.close()


__all__ = ["EmailSender", "SmtpConnector", "SmtpEmailSender"]
