"""Deriving symmetric keys from the single app secret (RF-19: no secrets in source).

The environment carries one high-entropy value, ``AETHERCAL_APP_SECRET``. The Fernet key that
encrypts every stored secret — ``external_connections.encrypted_credentials``, ``webhooks.secret``,
and each business's BYOK ``tenant_credentials.encrypted_payload`` — is *derived* from it
deterministically, so operators manage one secret rather than several, and the same secret always
yields the same key across restarts and replicas.

.. rubric:: ==One key, every business. Encryption at rest — NOT cryptographic isolation.==

Because the key is a pure function of the instance's single app secret, the same key encrypts the
credentials of EVERY business on the instance: whoever operates the instance can decrypt any of
them. What that buys is real, and narrower than it looks — a stolen dump, a leaked backup or a
SQL-injection read is useless without the app secret, which lives in the process environment and not
in the database. It is written up in full in
:mod:`aethercal.server.services.tenant_credentials` and in ``docs/byok-credentials.md``.

Rotating the key therefore means rotating the app secret and re-encrypting everything stored under
the old one: :func:`rotate_secret` is the primitive, and
:mod:`aethercal.server.services.key_rotation` is the operation.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence

from cryptography.fernet import Fernet, MultiFernet


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


def decrypt_secret(token: bytes, fernet_key: bytes | Sequence[bytes]) -> bytes:
    """Decrypt a token produced by :func:`encrypt_secret`, trying each offered key IN ORDER.

    ==One key in the steady state, two during a rotation.== ``fernet_key`` is a single key
    (``bytes``) normally, and the CURRENT-then-PREVIOUS pair (``[current, previous]``) while a key
    rotation is in flight — the reader every app process is handed once
    ``AETHERCAL_PREVIOUS_APP_SECRET`` is set (:meth:`~aethercal.server.settings.Settings.\
decryption_fernet_keys`). A :class:`~cryptography.fernet.MultiFernet` tries the keys left to right,
    so a row already re-encrypted onto the new key opens on the first, and a row the rotation has
    not reached yet — still on the retiring key — opens on the second.

    ==This is what closes the write-under-the-retiring-key window.== With reads limited to a single
    key, a process on the new secret could not open a row still on the old one; the only way to stay
    readable was to write under the old key — and those writes then sat outside a rotation already
    run, unreadable for ever once the old secret was retired. Reading under BOTH keys means every
    process can be restarted onto the new secret (and write under it) while the rotation moves the
    remaining rows across, with nothing stranded on the key about to be retired.

    A token that NO offered key opens raises :class:`~cryptography.fernet.InvalidToken` — a lost row
    is loud, never a silent empty read.
    """
    keys = [fernet_key] if isinstance(fernet_key, bytes) else list(fernet_key)
    return MultiFernet([Fernet(key) for key in keys]).decrypt(token)


def rotate_secret(token: bytes, *, new_key: bytes, previous_key: bytes) -> bytes:
    """Re-encrypt ``token`` under ``new_key``, accepting ciphertext written under EITHER key.

    ``MultiFernet.rotate`` decrypts with any key in the list and re-encrypts with the **first**, so
    with ``[new, previous]``:

    * a token still under the old key is moved onto the new one — the rotation's actual job;
    * a token ALREADY under the new key is decrypted by the first key and simply rewritten. ==That
      is what makes the rotation resumable==: a run interrupted half-way (a killed process, a
      dropped connection) can be finished by running it again, because a row that needs nothing is
      not an error.

    A token under NEITHER key raises :class:`~cryptography.fernet.InvalidToken`, which the rotation
    turns into a hard stop rather than a skip: a row nothing can decrypt is a row that will be
    unreadable for ever once the old secret is retired, and it must be discovered NOW — loudly —
    rather than by whoever needs it, months later.

    The plaintext exists only inside this call, between the decrypt and the encrypt. It is never
    returned, logged or stored.
    """
    return MultiFernet([Fernet(new_key), Fernet(previous_key)]).rotate(token)
