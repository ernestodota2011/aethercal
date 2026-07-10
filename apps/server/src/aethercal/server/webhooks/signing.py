"""HMAC-SHA256 signing of the outbound webhook envelope (RF-17).

A consumer verifies a delivery by recomputing the signature over the exact bytes we POST. To make
that reproducible on both ends, the envelope is serialized to a *canonical* JSON form — sorted keys,
no insignificant whitespace — before signing, so the signature is stable regardless of dict order.
The signature travels in the ``X-AetherCal-Signature`` header as ``sha256=<hex>``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

SIGNATURE_HEADER = "X-AetherCal-Signature"
"""The response header carrying the delivery signature (``sha256=<hex>``)."""

_SIGNATURE_PREFIX = "sha256="


def canonical_body(envelope: Mapping[str, Any]) -> bytes:
    """Serialize ``envelope`` to deterministic JSON bytes: sorted keys, compact separators."""
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign(body: bytes, secret: bytes) -> str:
    """Return the hex HMAC-SHA256 of ``body`` keyed by ``secret``."""
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def signature_header(body: bytes, secret: bytes) -> str:
    """Return the ``X-AetherCal-Signature`` value for ``body``: ``sha256=<hex>``."""
    return f"{_SIGNATURE_PREFIX}{sign(body, secret)}"


def verify_signature(body: bytes, secret: bytes, signature: str) -> bool:
    """Constant-time check that ``signature`` matches ``body`` under ``secret``.

    Accepts either the bare hex digest or the ``sha256=<hex>`` header form (for consumers/tests).
    """
    presented = (
        signature[len(_SIGNATURE_PREFIX) :]
        if signature.startswith(_SIGNATURE_PREFIX)
        else signature
    )
    return hmac.compare_digest(presented, sign(body, secret))


__all__ = [
    "SIGNATURE_HEADER",
    "canonical_body",
    "sign",
    "signature_header",
    "verify_signature",
]
