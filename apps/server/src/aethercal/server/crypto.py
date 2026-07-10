"""Deriving symmetric keys from the single app secret (RF-19: no secrets in source).

The environment carries one high-entropy value, ``AETHERCAL_APP_SECRET``. The Fernet key that
encrypts stored provider credentials (``external_connections.encrypted_credentials``, once F1-07
lands) is *derived* from it deterministically, so operators manage one secret rather than two and
the same secret always yields the same key across restarts and replicas.
"""

from __future__ import annotations

import base64
import hashlib


def derive_fernet_key(app_secret: str) -> bytes:
    """Derive a valid Fernet key (32 url-safe-base64-encoded bytes) from ``app_secret``.

    Fernet requires exactly a url-safe base64 encoding of 32 raw bytes; a SHA-256 digest of the
    secret is exactly 32 bytes, so the encoded digest is a well-formed key.
    """
    if not app_secret:
        raise ValueError("app_secret must be a non-empty string")
    digest = hashlib.sha256(app_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
