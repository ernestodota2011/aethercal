"""Signed, single-use, expiring guest tokens for account-less cancel/reschedule (RF-09).

A guest who booked without an account gets a link carrying an opaque token. The token is validated
in **two independent layers**, so compromising either one is not enough:

1. *Cryptographic* — the token is an :class:`itsdangerous.URLSafeTimedSerializer` payload signed
   with the app secret and stamped with a signing time. A bad signature, a tampered payload, or a
   signature older than ``max_age`` fails here. The signature is self-describing, so this layer
   needs no database.
2. *Database* — issuing also stores a :class:`~aethercal.server.db.models.GuestToken` row keyed by
   ``sha256(token)`` (only the hash, never the token). The row pins the precise per-token expiry
   (``expires_at``), single use (``used_at``), and revocability, and binds the token to one tenant,
   booking, and purpose.

A token is valid only if **both** layers agree. Every failure mode — bad signature, tampered,
expired, wrong purpose, unknown, already used, revoked — collapses to ``None``; the service never
raises with booking data attached, so a caller cannot distinguish the reasons and leak information
about a booking it should not see (RF-09).

The signer is constructed from a secret **passed in by the caller** (never read from global
settings here), which keeps the whole module unit-testable without process configuration. The app
wires it from ``Settings.app_secret``; see the module ``__all__`` for the public surface.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from itsdangerous import BadData
from itsdangerous.url_safe import URLSafeTimedSerializer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import GuestToken

# Domain-separation salt: this serializer signs guest tokens and nothing else, so a token minted for
# some other itsdangerous use with the same secret can never validate here.
_SIGNER_SALT: Final = "aethercal.guest-token.v1"

# Bytes of url-safe entropy folded into each token's nonce → two tokens for the same
# (booking, purpose) are still distinct values with distinct hashes (no token_hash collision).
_NONCE_NBYTES: Final = 16

# A generous hard ceiling on how old a signature may be, independent of the per-token ``expires_at``
# in the database (which is the precise business expiry). This is defense-in-depth: even if a stale
# row outlived a cleanup bug, the cryptographic layer still refuses an ancient signature.
MAX_SIGNATURE_AGE: Final = timedelta(days=400)


class GuestTokenPurpose(StrEnum):
    """What a guest token authorizes. A token is bound to exactly one purpose (RF-09)."""

    CANCEL = "cancel"
    RESCHEDULE = "reschedule"


@dataclass(frozen=True, slots=True)
class GuestTokenPayload:
    """The verified, decoded contents of a guest token's signed payload."""

    booking_id: uuid.UUID
    purpose: GuestTokenPurpose
    nonce: str


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of ``token`` — the only form of the token ever persisted (RF-09)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GuestTokenSigner:
    """Signs and verifies the cryptographic layer of a guest token.

    Wraps :class:`itsdangerous.URLSafeTimedSerializer`, built from a secret **passed in** by the
    caller (kept out of global settings so the service unit-tests without process configuration).
    """

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("secret must be a non-empty string")
        self._serializer: URLSafeTimedSerializer = URLSafeTimedSerializer(secret, salt=_SIGNER_SALT)

    def sign(self, *, booking_id: uuid.UUID, purpose: GuestTokenPurpose, nonce: str) -> str:
        """Sign ``{booking_id, purpose, nonce}`` into an opaque, timestamped, url-safe token."""
        payload = {"booking_id": str(booking_id), "purpose": purpose.value, "nonce": nonce}
        return self._serializer.dumps(payload)

    def unsign(self, token: str, *, max_age: timedelta) -> GuestTokenPayload | None:
        """Return the decoded payload, or ``None`` if the signature is bad, tampered, malformed, or
        older than ``max_age``. Never raises on adversarial input (RF-09: no information leak)."""
        try:
            raw = self._serializer.loads(token, max_age=int(max_age.total_seconds()))
        except BadData:
            return None
        if not isinstance(raw, dict):
            return None
        booking_id_raw = raw.get("booking_id")
        purpose_raw = raw.get("purpose")
        nonce_raw = raw.get("nonce")
        if (
            not isinstance(booking_id_raw, str)
            or not isinstance(purpose_raw, str)
            or not isinstance(nonce_raw, str)
        ):
            return None
        try:
            booking_id = uuid.UUID(booking_id_raw)
            purpose = GuestTokenPurpose(purpose_raw)
        except ValueError:
            return None
        return GuestTokenPayload(booking_id=booking_id, purpose=purpose, nonce=nonce_raw)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware_utc(value: datetime) -> datetime:
    """Coerce a naive datetime (as some backends return for ``DateTime(timezone=True)``) to UTC, so
    comparisons never raise on a naive/aware mismatch. Aware values pass through unchanged."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# The keyword-only inputs below are the spec-mandated API contract (F1-05 bookings and F1-10 the
# booking page call this by keyword); the explicit arg list is the interface, so PLR0913 is waived.
async def issue_guest_token(  # noqa: PLR0913
    session: AsyncSession,
    signer: GuestTokenSigner,
    *,
    booking_id: uuid.UUID,
    tenant_id: uuid.UUID,
    purpose: GuestTokenPurpose,
    ttl: timedelta,
) -> str:
    """Mint a guest token for a booking, persist its hashed row, and return the token string.

    The returned string is the only time the plaintext token exists — only ``sha256(token)`` is
    stored. The row is flushed (id/defaults populated) but not committed: the caller owns the
    transaction (RF-09).
    """
    nonce = secrets.token_urlsafe(_NONCE_NBYTES)
    token = signer.sign(booking_id=booking_id, purpose=purpose, nonce=nonce)
    row = GuestToken(
        tenant_id=tenant_id,
        booking_id=booking_id,
        purpose=purpose.value,
        token_hash=hash_token(token),
        expires_at=_now() + ttl,
    )
    session.add(row)
    await session.flush()
    return token


async def verify_guest_token(
    session: AsyncSession,
    signer: GuestTokenSigner,
    token: str,
    *,
    expected_purpose: GuestTokenPurpose,
) -> GuestToken | None:
    """Return the backing :class:`GuestToken` row iff the token is valid, else ``None``.

    Valid means: the signature verifies and is not older than :data:`MAX_SIGNATURE_AGE`, its purpose
    equals ``expected_purpose``, a matching row exists whose ``booking_id`` and purpose agree with
    the signed payload (so the two layers genuinely bind the token to the same booking), it has not
    been used, and it has not expired. Any failure returns ``None`` uniformly — no exception carries
    booking data (RF-09: no information leak). This is read-only; it does not mark the token used.
    """
    payload = signer.unsign(token, max_age=MAX_SIGNATURE_AGE)
    if payload is None or payload.purpose is not expected_purpose:
        return None

    row = (
        await session.scalars(select(GuestToken).where(GuestToken.token_hash == hash_token(token)))
    ).one_or_none()
    if row is None:
        return None

    row_is_valid = (
        row.booking_id == payload.booking_id
        and row.purpose == expected_purpose.value
        and row.used_at is None
        and _as_aware_utc(row.expires_at) > _now()
    )
    return row if row_is_valid else None


async def consume_guest_token(
    session: AsyncSession,
    signer: GuestTokenSigner,
    token: str,
    *,
    expected_purpose: GuestTokenPurpose,
) -> GuestToken | None:
    """Verify the token and atomically mark it used, returning the row or ``None``.

    Single use is enforced by a compare-and-set: the ``used_at`` stamp is written only ``WHERE
    used_at IS NULL``, and the row is claimed only if that conditional UPDATE actually matched. Two
    concurrent consumes therefore have exactly one winner; the loser (and every later attempt) gets
    ``None``. The write is flushed but not committed — the caller owns the transaction (RF-09).
    """
    row = await verify_guest_token(session, signer, token, expected_purpose=expected_purpose)
    if row is None:
        return None

    used_at = _now()
    claimed = await session.execute(
        update(GuestToken)
        .where(GuestToken.id == row.id, GuestToken.used_at.is_(None))
        .values(used_at=used_at)
        .returning(GuestToken.id)
        .execution_options(synchronize_session=False)
    )
    if claimed.scalar_one_or_none() is None:
        return None
    row.used_at = used_at
    return row


__all__ = [
    "MAX_SIGNATURE_AGE",
    "GuestTokenPayload",
    "GuestTokenPurpose",
    "GuestTokenSigner",
    "consume_guest_token",
    "hash_token",
    "issue_guest_token",
    "verify_guest_token",
]
