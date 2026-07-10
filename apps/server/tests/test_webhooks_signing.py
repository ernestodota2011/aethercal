"""Pure signing tests: deterministic canonical body + HMAC-SHA256 sign/verify (RF-17)."""

from __future__ import annotations

from aethercal.server.webhooks.signing import (
    SIGNATURE_HEADER,
    canonical_body,
    sign,
    signature_header,
    verify_signature,
)


def test_signature_header_name_is_stable() -> None:
    assert SIGNATURE_HEADER == "X-AetherCal-Signature"


def test_canonical_body_is_independent_of_key_order() -> None:
    a = canonical_body({"event": "booking.created", "data": {"b": 1, "a": 2}})
    b = canonical_body({"data": {"a": 2, "b": 1}, "event": "booking.created"})
    assert a == b


def test_canonical_body_is_compact_sorted_json() -> None:
    assert canonical_body({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_sign_is_stable_and_hex() -> None:
    body = canonical_body({"event": "booking.created"})
    first = sign(body, b"topsecret")
    assert first == sign(body, b"topsecret")
    assert len(first) == 64
    int(first, 16)  # a valid lowercase hex digest


def test_signature_header_has_sha256_prefix() -> None:
    body = b"{}"
    assert signature_header(body, b"k") == f"sha256={sign(body, b'k')}"


def test_verify_accepts_the_header_form() -> None:
    body = canonical_body({"event": "booking.created"})
    header = signature_header(body, b"secret")
    assert verify_signature(body, b"secret", header) is True


def test_verify_accepts_the_bare_hex_form() -> None:
    body = b"{}"
    assert verify_signature(body, b"secret", sign(body, b"secret")) is True


def test_verify_rejects_a_tampered_body() -> None:
    body = canonical_body({"event": "booking.created"})
    header = signature_header(body, b"secret")
    tampered = canonical_body({"event": "booking.cancelled"})
    assert verify_signature(tampered, b"secret", header) is False


def test_verify_rejects_a_wrong_secret() -> None:
    body = canonical_body({"event": "booking.created"})
    header = signature_header(body, b"secret")
    assert verify_signature(body, b"other-secret", header) is False
