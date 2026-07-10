"""Unit tests for the pure (DB-free) API-key helpers (F1-17)."""

from __future__ import annotations

import hashlib

from aethercal.server.services.api_keys import (
    KEY_SCHEME,
    PREFIX_LENGTH,
    generate_key,
    hash_secret,
    parse_key,
)


def test_generate_key_shape() -> None:
    full_key, prefix, hashed = generate_key()

    assert full_key.startswith(f"{KEY_SCHEME}_")
    assert len(prefix) == PREFIX_LENGTH
    # full key = scheme _ prefix _ secret
    scheme, key_prefix, secret = full_key.split("_", 2)
    assert scheme == KEY_SCHEME
    assert key_prefix == prefix
    # The stored hash is sha256 of the secret only (never the prefix, never the full key).
    assert hashed == hashlib.sha256(secret.encode()).hexdigest()
    assert len(secret) >= 40


def test_generate_key_is_unique_each_call() -> None:
    keys = [generate_key() for _ in range(50)]
    prefixes = {prefix for _, prefix, _ in keys}
    fulls = {full for full, _, _ in keys}
    assert len(prefixes) == 50
    assert len(fulls) == 50


def test_hash_secret_is_deterministic_hex_sha256() -> None:
    digest = hash_secret("some-secret-value")
    assert digest == hashlib.sha256(b"some-secret-value").hexdigest()
    assert len(digest) == 64
    assert digest == hash_secret("some-secret-value")


def test_parse_key_roundtrips_generate_key() -> None:
    full_key, prefix, hashed = generate_key()
    parsed = parse_key(full_key)
    assert parsed is not None
    parsed_prefix, parsed_secret = parsed
    assert parsed_prefix == prefix
    assert hash_secret(parsed_secret) == hashed


def test_parse_key_rejects_malformed_input() -> None:
    assert parse_key("") is None
    assert parse_key("no-scheme-here") is None
    assert parse_key("ack_only") is None  # missing separator + secret
    assert parse_key("wrong_prefix_secret") is None  # scheme is not ack
    assert parse_key(f"{KEY_SCHEME}_short_") is None  # prefix too short, empty secret
    # A valid-looking prefix but an underscore inside it breaks the base62 rule.
    assert parse_key(f"{KEY_SCHEME}_abcd_efg_secretpart") is None


def test_parse_key_accepts_secret_containing_url_safe_symbols() -> None:
    # The secret is the remainder after the fixed-length prefix, so '-'/'_' inside it are fine.
    prefix = "Ab3xZ9qW"  # exactly PREFIX_LENGTH base62 chars
    assert len(prefix) == PREFIX_LENGTH
    presented = f"{KEY_SCHEME}_{prefix}_secret-with_underscores-and-dashes"
    parsed = parse_key(presented)
    assert parsed == (prefix, "secret-with_underscores-and-dashes")
