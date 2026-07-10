"""Deriving symmetric keys from the single app secret (RF-19: no secrets in source).

The environment carries one high-entropy value, ``AETHERCAL_APP_SECRET``. The Fernet key that
encrypts stored provider credentials (``external_connections.encrypted_credentials``, once F1-07
lands) is *derived* from it deterministically, so operators manage one secret rather than two and
the same secret always yields the same key across restarts and replicas.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def derive_fernet_key(app_secret: str) -> bytes:
    """Derive a valid Fernet key (32 url-safe-base64-encoded bytes) from ``app_secret``.

    Fernet requires exactly a url-safe base64 encoding of 32 raw bytes; a SHA-256 digest of the
    secret is exactly 32 bytes, so the encoded digest is a well-formed key.
    """
    if not app_secret:
        raise ValueError("app_secret must be a non-empty string")
    digest = hashlib.sha256(app_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: bytes, fernet_key: bytes) -> bytes:
    """Encrypt ``plaintext`` with ``fernet_key`` — used to store per-subscriber secrets at rest.

    The same helper backs the webhook subscriber secret (RF-17) and any future credential-at-rest.
    """
    return Fernet(fernet_key).encrypt(plaintext)


def decrypt_secret(token: bytes, fernet_key: bytes) -> bytes:
    """Decrypt a token produced by :func:`encrypt_secret` back to its plaintext bytes."""
    return Fernet(fernet_key).decrypt(token)
