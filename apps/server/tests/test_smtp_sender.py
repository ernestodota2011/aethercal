"""``EmailSender`` protocol + ``SmtpEmailSender`` seam (RF-08).

The live network send is not exercised offline; this only checks the seam is structurally sound and
injectable — a recording fake and the real ``SmtpEmailSender`` both satisfy the runtime-checkable
:class:`EmailSender` protocol, so the notification service can accept either.
"""

from __future__ import annotations

from email.message import EmailMessage

from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_smtp_email_sender_conforms_to_the_protocol() -> None:
    sender = SmtpEmailSender(SmtpConfig(host="smtp.example.com", from_addr="no-reply@example.com"))
    assert isinstance(sender, EmailSender)


def test_a_recording_fake_conforms_to_the_protocol() -> None:
    assert isinstance(_RecordingSender(), EmailSender)
