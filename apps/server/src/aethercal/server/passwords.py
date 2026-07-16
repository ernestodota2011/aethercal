"""Password hashing (F1-11, RF-18/RF-19, B-02): a salted, stretched, stdlib-only KDF.

A raw SHA-256 (as the API-key *secret* uses) is fine for a 40-char random key and wrong for a
human-chosen password: it is unsalted and fast to brute force. So every human password in this
product uses PBKDF2-HMAC-SHA256 with a per-hash random salt and a real work factor, encoded in a
self-describing, Django-style string::

    pbkdf2_sha256$<iterations>$<salt_hex>$<derived_key_hex>

Verification is constant-time (``hmac.compare_digest``) and every malformed/tampered/unknown-scheme
input collapses to ``False`` — a verifier never raises on bad stored data or leaks *why* it failed.

.. rubric:: Why this is no longer ``admin.passwords`` (B-02)

It hashed exactly one password — the instance operator's, which lives in the environment and never
touches the database — so living inside the admin package was right. B-02 gives BUSINESS MEMBERS
passwords (``users.hashed_password``, written by ``services.users``), and a service reaching *up*
into the admin package for its KDF is an inverted dependency and, worse, the shape in which a second
copy of "how we hash a password" gets written. There is ONE KDF, it is a server primitive, and it
lives here.

Mint a hash for the instance operator (the value goes into the env, never committed)::

    python -m aethercal.server.passwords
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import secrets

_SCHEME = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600_000  # OWASP 2023 guidance for PBKDF2-HMAC-SHA256.
_SALT_BYTES = 16
_DIGEST_BYTES = 32  # SHA-256 output width.
# Bound the accepted cost so a malformed/hostile hash string cannot force an arbitrarily expensive
# derivation (a DoS): reject anything below a sane floor or above a hard ceiling, and require the
# exact salt/digest widths this module emits. A verifier only ever runs the KDF inside this window.
_MIN_ITERATIONS = 1_000
_MAX_ITERATIONS = 10_000_000


def hash_password(plain: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Hash ``plain`` into the self-describing ``pbkdf2_sha256$...`` string (fresh random salt).

    ``iterations`` is held to the SAME bounds :func:`verify_password` enforces, so this function can
    never mint a hash its own verifier would reject as out-of-range.
    """
    if not _MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS:
        raise ValueError(f"iterations must be in [{_MIN_ITERATIONS}, {_MAX_ITERATIONS}]")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _derive(plain, salt=salt, iterations=iterations)
    return f"{_SCHEME}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(stored: str, presented: str) -> bool:
    """Return whether ``presented`` matches the ``stored`` hash; ``False`` on any malformed input.

    Constant-time in the derived-key comparison (:func:`hmac.compare_digest`). A stored value that
    is empty, has the wrong shape, an unknown scheme, or a non-integer iteration count is a
    non-match rather than an error (RF-16: the caller learns only pass/fail).
    """
    parsed = _parse(stored)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    candidate = _derive(presented, salt=salt, iterations=iterations)
    return hmac.compare_digest(candidate, expected)


def _derive(plain: str, *, salt: bytes, iterations: int) -> bytes:
    """The raw PBKDF2-HMAC-SHA256 derived key for ``plain`` under ``salt``/``iterations``."""
    return hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)


def _parse(stored: str) -> tuple[int, bytes, bytes] | None:
    """Split a stored hash into ``(iterations, salt, expected)`` or ``None`` if it is malformed."""
    parts = stored.split("$")
    if len(parts) != 4:
        return None
    scheme, iterations_raw, salt_hex, digest_hex = parts
    if scheme != _SCHEME:
        return None
    try:
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return None
    if not _MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS:
        return None
    if len(salt) != _SALT_BYTES or len(expected) != _DIGEST_BYTES:
        return None
    return iterations, salt, expected


def _main() -> None:  # pragma: no cover - operator convenience entrypoint
    """Prompt for a password (no echo) and print its hash for ``AETHERCAL_ADMIN_PASSWORD_HASH``."""
    first = getpass.getpass("Admin password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("passwords do not match")
    if not first:
        raise SystemExit("password must not be empty")
    print(hash_password(first))


if __name__ == "__main__":  # pragma: no cover
    _main()


__all__ = ["hash_password", "verify_password"]
