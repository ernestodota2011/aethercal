"""API-key issuance and verification (F1-17).

Key format: the plaintext key shown once to the operator is ``ack_<prefix>_<secret>``.

* ``prefix`` — a fixed-length, globally unique, base62 (no ``_``) token stored in
  ``api_keys.prefix``. It identifies the row (and therefore the tenant), so verification needs no
  tenant hint and a single indexed lookup finds the candidate.
* ``secret`` — >= 40 chars of url-safe entropy, never stored. Only ``sha256(secret)`` hex is
  persisted in ``api_keys.hashed_key`` and compared with a constant-time ``hmac.compare_digest``.

Because the prefix is fixed length and base62, the ``ack_`` + prefix + ``_`` + secret string parses
unambiguously even though the secret itself may contain ``-``/``_``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import ApiKey

KEY_SCHEME = "ack"
PREFIX_LENGTH = 8
_PREFIX_ALPHABET = string.ascii_letters + string.digits  # base62 → no '_', so parsing is exact.
_SECRET_NBYTES = 32  # secrets.token_urlsafe(32) → 43 chars, comfortably >= 40.
_PREFIX_MARKER = f"{KEY_SCHEME}_"


def hash_secret(secret: str) -> str:
    """Return the hex SHA-256 of ``secret`` — the only form of the secret ever persisted."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _generate_prefix() -> str:
    return "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))


def generate_key() -> tuple[str, str, str]:
    """Mint a fresh key. Returns ``(full_key, prefix, hashed_key)``.

    Only ``prefix`` and ``hashed_key`` are stored; ``full_key`` is shown once and then discarded.
    """
    prefix = _generate_prefix()
    secret = secrets.token_urlsafe(_SECRET_NBYTES)
    full_key = f"{KEY_SCHEME}_{prefix}_{secret}"
    return full_key, prefix, hash_secret(secret)


def parse_key(presented: str) -> tuple[str, str] | None:
    """Split a presented key into ``(prefix, secret)``, or ``None`` if it is malformed."""
    if not presented.startswith(_PREFIX_MARKER):
        return None
    body = presented[len(_PREFIX_MARKER) :]
    # body must be exactly: <prefix (PREFIX_LENGTH chars)> '_' <secret (>= 1 char)>.
    if len(body) < PREFIX_LENGTH + 2:
        return None
    prefix = body[:PREFIX_LENGTH]
    separator = body[PREFIX_LENGTH]
    secret = body[PREFIX_LENGTH + 1 :]
    if separator != "_" or not secret:
        return None
    if any(char not in _PREFIX_ALPHABET for char in prefix):
        return None
    return prefix, secret


async def issue_api_key(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> tuple[ApiKey, str]:
    """Create and persist a new API key for ``tenant_id``. Returns ``(row, full_key)``.

    The ``full_key`` is the only time the plaintext exists; only ``prefix`` + ``hashed_key`` are
    stored. The row is flushed (so its id/defaults are populated) but not committed — the caller
    owns the transaction.
    """
    full_key, prefix, hashed_key = generate_key()
    api_key = ApiKey(tenant_id=tenant_id, name=name, prefix=prefix, hashed_key=hashed_key)
    session.add(api_key)
    await session.flush()
    return api_key, full_key


async def verify_api_key(session: AsyncSession, presented: str) -> ApiKey | None:
    """Return the matching :class:`ApiKey` for a presented key, or ``None`` on any failure.

    ``None`` is returned uniformly for a malformed key, an unknown prefix, a hash mismatch, or a
    revoked key — the caller must not distinguish these (RF-16: no information leak). On success the
    row's ``last_used_at`` is stamped best-effort (persisted by the caller's commit).
    """
    parsed = parse_key(presented)
    if parsed is None:
        return None
    prefix, secret = parsed

    api_key = (await session.scalars(select(ApiKey).where(ApiKey.prefix == prefix))).one_or_none()
    if api_key is None:
        return None
    if api_key.revoked_at is not None:
        return None
    if not hmac.compare_digest(api_key.hashed_key, hash_secret(secret)):
        return None

    api_key.last_used_at = datetime.now(UTC)
    return api_key


async def list_api_keys(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[ApiKey]:
    """Return all of ``tenant_id``'s API keys (active and revoked), newest first.

    Never touches ``hashed_key`` beyond returning the row — callers (the admin CLI) must only
    surface ``id``/``prefix``/``name``/timestamps/revocation status, never the hash or plaintext.
    """
    rows = await session.scalars(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.all())


async def revoke_api_key(
    session: AsyncSession, *, api_key_id: uuid.UUID, tenant_id: uuid.UUID
) -> bool:
    """Revoke the key ``api_key_id`` iff it belongs to ``tenant_id``.

    Returns ``True`` when a key owned by the tenant was found (revoking is idempotent), ``False``
    when no such key exists for that tenant (cross-tenant revoke is refused).
    """
    api_key = (
        await session.scalars(
            select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.tenant_id == tenant_id)
        )
    ).one_or_none()
    if api_key is None:
        return False
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
    return True
