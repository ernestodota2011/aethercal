"""The multichannel sending seam (Wave 0 contract): ``Channel`` + the ``ChannelSender`` protocol.

Wave 1 implements WhatsApp (Evolution) and SMS (Twilio) against this protocol. Wave 0 only has to
guarantee two things, and both are asserted here:

* the vocabulary is exactly ``email`` / ``whatsapp`` / ``sms`` (the shared contract every worktree
  copies verbatim — a stray spelling breaks integration);
* the ALREADY-WORKING ``SmtpEmailSender`` satisfies the protocol *through a wrapper* and is not
  rewritten: the wrapper composes an :class:`EmailMessage` from ``to``/``subject``/``body`` and
  hands it to the untouched sender.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from aethercal.server.channels import Channel, ChannelSender, EmailChannelSender
from aethercal.server.integrations.smtp.sender import EmailSender


class _RecordingEmailSender:
    """A fake :class:`EmailSender` — the exact seam ``SmtpEmailSender`` implements."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_channel_vocabulary_is_the_shared_contract() -> None:
    assert {channel.value for channel in Channel} == {"email", "whatsapp", "sms"}


def test_the_email_wrapper_satisfies_the_channel_sender_protocol() -> None:
    sender = EmailChannelSender(_RecordingEmailSender())

    assert isinstance(sender, ChannelSender)
    assert sender.channel is Channel.EMAIL


def test_the_wrapper_wraps_the_existing_email_sender_seam() -> None:
    inner = _RecordingEmailSender()
    # The wrapper takes an EmailSender — the protocol SmtpEmailSender already satisfies. The live
    # SMTP sender is WRAPPED, never rewritten.
    assert isinstance(inner, EmailSender)
    EmailChannelSender(inner)


async def test_send_builds_the_email_message_from_the_channel_arguments() -> None:
    inner = _RecordingEmailSender()
    sender = EmailChannelSender(inner)

    await sender.send(to="ada@example.com", subject="Your booking", body="See you tomorrow.")

    assert len(inner.sent) == 1
    message = inner.sent[0]
    assert message["To"] == "ada@example.com"
    assert message["Subject"] == "Your booking"
    assert message.get_content().strip() == "See you tomorrow."


async def test_a_subjectless_send_is_allowed_and_omits_the_header() -> None:
    """WhatsApp/SMS have no subject, so the protocol makes it optional; the email wrapper must not
    invent one (an empty ``Subject:`` header is a spam signal)."""
    inner = _RecordingEmailSender()
    sender = EmailChannelSender(inner)

    await sender.send(to="ada@example.com", subject=None, body="Reminder.")

    assert inner.sent[0]["Subject"] is None


async def test_the_wrapper_refuses_an_empty_recipient() -> None:
    sender = EmailChannelSender(_RecordingEmailSender())

    with pytest.raises(ValueError, match="recipient"):
        await sender.send(to="", subject=None, body="body")
