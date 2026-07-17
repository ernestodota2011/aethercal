"""The multichannel sending seam (RF-24): one :class:`ChannelSender` protocol per delivery channel.

A workflow step says *"send THIS body, on THIS channel, to THIS recipient"*. Everything below that
sentence — SMTP envelopes, the Evolution API, Twilio — is an implementation detail behind a single
narrow protocol, so the workflow engine never grows an ``if channel == ...`` ladder and a new
channel is a new class, not a new branch.

The existing :class:`~aethercal.server.integrations.smtp.sender.SmtpEmailSender` is **wrapped**
(:class:`EmailChannelSender`), never rewritten: it already carries the TLS/port logic and the
``From`` stamping, and the transactional-email path keeps using it directly (it composes a full MIME
message with the ``.ics`` attachment, not a plain body). The wrapper is the adapter between "a
rendered channel body" and "an :class:`~email.message.EmailMessage`".

A channel that is not configured is simply ABSENT from the sender registry — the workflow engine
skips its steps and says so. An unconfigured channel is never a boot failure (see the
``AETHERCAL_WHATSAPP_*`` / ``AETHERCAL_SMS_*`` config contract).
"""

from __future__ import annotations

from email.message import EmailMessage
from enum import StrEnum
from typing import Protocol, runtime_checkable

from aethercal.server.integrations.smtp.sender import EmailSender


class Channel(StrEnum):
    """The delivery channels a workflow step can target. The single source of truth for the name."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"


@runtime_checkable
class ChannelSender(Protocol):
    """Anything that can deliver a rendered body on one channel.

    ``subject`` is ``None`` for the channels that have no such concept (WhatsApp, SMS); an
    implementation must not invent one. ``to`` is the channel's address — an email address for
    :attr:`Channel.EMAIL`, an E.164 phone number for WhatsApp/SMS. Raising means "not delivered",
    which the outbox turns into a retry with backoff."""

    channel: Channel

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        """Deliver ``body`` to ``to`` on this channel."""
        ...


class EmailChannelSender:
    """Adapts the existing :class:`EmailSender` (``SmtpEmailSender``) to :class:`ChannelSender`.

    Composes the minimal :class:`EmailMessage` a workflow step needs (To / optional Subject / plain
    body) and hands it to the untouched sender, which stamps ``From`` and owns the transport. The
    richer transactional emails (confirmation/cancellation/reschedule, with their ``.ics`` part)
    keep composing their own message and calling the sender directly — this wrapper serves the
    *workflow* path, it does not replace that one.
    """

    channel = Channel.EMAIL

    def __init__(self, sender: EmailSender) -> None:
        self._sender = sender

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        """Compose a plain-text message and delegate to the wrapped :class:`EmailSender`."""
        if not to.strip():
            raise ValueError("an email channel send needs a recipient")
        message = EmailMessage()
        message["To"] = to
        if subject is not None:
            message["Subject"] = subject
        message.set_content(body)
        await self._sender.send(message)


__all__ = ["Channel", "ChannelSender", "EmailChannelSender"]
