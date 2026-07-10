"""Outbound webhook delivery machinery (RF-17): signing and the injectable delivery worker.

Subscription CRUD and the event fan-out live in ``aethercal.server.services.webhooks``; this package
holds the pure signing helpers and the fully injected ``deliver_due`` worker.
"""

from __future__ import annotations
