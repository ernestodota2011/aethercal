"""SMTP integration (RF-08): env-sourced config, the pure email composition, and the async sender.

Transactional booking emails (confirmation / cancellation / reschedule) and the 24 h reminder are
composed here as ``email.message.EmailMessage`` objects with a ``text/calendar`` invite attached,
then handed to an :class:`~aethercal.server.integrations.smtp.sender.EmailSender`. Composition is
pure and unit-tested; only the live SMTP send touches the network.
"""

from __future__ import annotations
