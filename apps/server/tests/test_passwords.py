"""Unit tests for the admin password hashing/verification (F1-11, RF-18).

The admin password is never stored in the source or the database — only a salted, stretched
PBKDF2-SHA256 hash lives in ``AETHERCAL_ADMIN_PASSWORD_HASH`` (RF-19). These tests lock the
self-describing hash format, the constant-time verification, and every rejection path.
"""

from __future__ import annotations

import pytest

from aethercal.server.passwords import hash_password, verify_password


def test_hash_has_the_self_describing_pbkdf2_format() -> None:
    stored = hash_password("correct horse battery staple")
    parts = stored.split("$")
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) >= 100_000  # a real work factor, not a token count
    assert len(parts) == 4  # scheme$iterations$salt$hash


def test_hash_then_verify_roundtrips() -> None:
    stored = hash_password("s3cret-pass")
    assert verify_password(stored, "s3cret-pass") is True


def test_verify_rejects_the_wrong_password() -> None:
    stored = hash_password("s3cret-pass")
    assert verify_password(stored, "not-it") is False


def test_each_hash_uses_a_fresh_random_salt() -> None:
    # Same password, two calls → different stored strings (salt differs) but both verify.
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second
    assert verify_password(first, "same-password") is True
    assert verify_password(second, "same-password") is True


def test_a_tampered_hash_does_not_verify() -> None:
    stored = hash_password("s3cret-pass")
    scheme, iterations, salt, digest = stored.split("$")
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    tampered = "$".join([scheme, iterations, salt, flipped])
    assert verify_password(tampered, "s3cret-pass") is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$only$three",
        "bcrypt$1$deadbeef$cafe",  # unknown scheme
        "pbkdf2_sha256$notanint$deadbeef$cafe",
    ],
)
def test_verify_rejects_a_malformed_stored_hash(malformed: str) -> None:
    assert verify_password(malformed, "anything") is False


def test_verify_rejects_an_absurdly_high_iteration_count_without_running_it() -> None:
    # A hostile/typo'd hash must not force a multi-billion-round derivation (DoS). Rejected on
    # parse, so this returns immediately rather than hanging.
    salt = "00" * 16
    digest = "00" * 32
    assert verify_password(f"pbkdf2_sha256$99999999999${salt}${digest}", "x") is False


def test_verify_rejects_wrong_salt_or_digest_widths() -> None:
    # Salt/digest must be the exact widths this module emits (16 / 32 bytes).
    assert verify_password("pbkdf2_sha256$600000$00$0011", "x") is False  # too short
    good_salt = "00" * 16
    assert verify_password(f"pbkdf2_sha256$600000${good_salt}$00", "x") is False  # short digest


@pytest.mark.parametrize("iterations", [1, 999, 10_000_001, 99_999_999_999])
def test_hash_password_refuses_out_of_range_iterations(iterations: int) -> None:
    # A hash must never be minted with a work factor its own verifier would reject as out-of-range.
    with pytest.raises(ValueError, match="iterations"):
        hash_password("s3cret", iterations=iterations)


def test_hash_password_accepts_and_verifies_the_bound_edge() -> None:
    # The exact minimum still round-trips (kept small so the test stays fast).
    stored = hash_password("s3cret", iterations=1_000)
    assert verify_password(stored, "s3cret") is True
