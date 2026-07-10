"""Unit tests for the pure (DB-free) guest-token helpers (RF-09)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from aethercal.server.services.guest_tokens import (
    MAX_SIGNATURE_AGE,
    GuestTokenPurpose,
    GuestTokenSigner,
    hash_token,
)


def test_purpose_values() -> None:
    assert GuestTokenPurpose.CANCEL.value == "cancel"
    assert GuestTokenPurpose.RESCHEDULE.value == "reschedule"
    assert set(GuestTokenPurpose) == {GuestTokenPurpose.CANCEL, GuestTokenPurpose.RESCHEDULE}


def test_hash_token_is_deterministic_hex_sha256() -> None:
    digest = hash_token("a-guest-token-string")
    assert digest == hashlib.sha256(b"a-guest-token-string").hexdigest()
    assert len(digest) == 64
    assert digest == hash_token("a-guest-token-string")


def test_signer_rejects_empty_secret() -> None:
    with pytest.raises(ValueError):
        GuestTokenSigner("")


def test_sign_then_unsign_roundtrip() -> None:
    signer = GuestTokenSigner("a-good-app-secret")
    booking_id = uuid.uuid4()
    nonce = "nonce-abc"

    token = signer.sign(booking_id=booking_id, purpose=GuestTokenPurpose.CANCEL, nonce=nonce)
    payload = signer.unsign(token, max_age=MAX_SIGNATURE_AGE)

    assert payload is not None
    assert payload.booking_id == booking_id
    assert payload.purpose is GuestTokenPurpose.CANCEL
    assert payload.nonce == nonce


def test_two_signs_differ_by_nonce() -> None:
    signer = GuestTokenSigner("a-good-app-secret")
    booking_id = uuid.uuid4()
    a = signer.sign(booking_id=booking_id, purpose=GuestTokenPurpose.CANCEL, nonce="n1")
    b = signer.sign(booking_id=booking_id, purpose=GuestTokenPurpose.CANCEL, nonce="n2")
    assert a != b


def test_unsign_rejects_wrong_secret() -> None:
    minter = GuestTokenSigner("secret-a")
    other = GuestTokenSigner("secret-b")
    token = minter.sign(booking_id=uuid.uuid4(), purpose=GuestTokenPurpose.CANCEL, nonce="n")
    assert other.unsign(token, max_age=MAX_SIGNATURE_AGE) is None


def test_unsign_rejects_tampered_token() -> None:
    signer = GuestTokenSigner("a-good-app-secret")
    token = signer.sign(booking_id=uuid.uuid4(), purpose=GuestTokenPurpose.CANCEL, nonce="n")
    # Flip a character in the middle of the payload segment.
    middle = len(token) // 2
    tampered = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1 :]
    assert signer.unsign(tampered, max_age=MAX_SIGNATURE_AGE) is None


def test_unsign_rejects_expired_signature() -> None:
    signer = GuestTokenSigner("a-good-app-secret")
    token = signer.sign(booking_id=uuid.uuid4(), purpose=GuestTokenPurpose.CANCEL, nonce="n")
    # A negative max_age forces the freshly-minted signature to read as already expired,
    # exercising the itsdangerous SignatureExpired branch deterministically (no sleep).
    assert signer.unsign(token, max_age=timedelta(seconds=-1)) is None


def test_unsign_rejects_garbage() -> None:
    signer = GuestTokenSigner("a-good-app-secret")
    assert signer.unsign("", max_age=MAX_SIGNATURE_AGE) is None
    assert signer.unsign("not-a-real-token", max_age=MAX_SIGNATURE_AGE) is None
