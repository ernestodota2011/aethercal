"""The WhatsApp channel, backed by the Evolution API (RF-24).

Evolution is chosen over the Meta Cloud API for the reason that matters to this product: it is
**self-hostable**, so the self-hoster this project is built for can run the whole stack without a
Meta business account. The adapter talks to its documented ``/message/sendText/{instance}``
endpoint and nothing else.
"""

from __future__ import annotations

from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender

__all__ = ["EvolutionConfig", "EvolutionWhatsAppSender"]
