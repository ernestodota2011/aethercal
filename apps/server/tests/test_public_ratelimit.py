"""The API's own per-IP rate limit, and the proxy contract the IP identity rests on.

Until this cut the API had **no rate limit of any kind**: the only limiter in the product lived in
the booking PAGE, on four of its POST handlers. Anyone calling the API directly skipped it entirely
— which mattered little while every write needed an API key, and matters enormously the moment one
of them does not.

.. rubric:: The proxy contract, and why BOTH of its failure modes are silent

The identity of a request is its client IP, and behind a reverse proxy the transport peer is the
PROXY. Two ways to get this wrong, and neither of them raises:

* count ``request.client.host`` behind a CDN and **every guest shares one bucket** — the cap is
  exhausted by the proxy's own address and the endpoint denies service to everybody. A
  self-inflicted
  outage that looks exactly like an attack;
* honour ``X-Forwarded-For`` from ANY peer and the header is **forgeable** — a client sets whatever
  it likes, gets a fresh bucket per request, and the cap enforces nothing at all. Another no-op with
  the light left on.

So the header is honoured only from a peer inside ``AETHERCAL_TRUSTED_PROXIES`` — a list of CIDRs
the OPERATOR declares — and, within it, the identity is the RIGHTMOST entry that is not itself a
trusted proxy: the address the outermost trusted hop actually observed. Anything a client appends to
the left of that is decoration.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from aethercal.server.api.ratelimit import SlidingWindowLimiter
from aethercal.server.client_ip import TrustedProxies, resolve_client_ip

_PROXY = "10.0.0.7"
_GUEST = "203.0.113.9"


def _request(*, peer: str, headers: dict[str, str] | None = None) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/public/acme/intro/bookings",
        "headers": Headers(headers or {}).raw,
        "client": (peer, 51234),
    }
    return Request(scope)


# --------------------------------------------------------------------------------------
# Who the client IS.
# --------------------------------------------------------------------------------------


def test_with_no_trusted_proxy_declared_the_forwarded_header_is_IGNORED() -> None:
    """Secure by default: an undeclared deployment trusts nobody, so the header cannot be forged."""
    trusted = TrustedProxies.parse("")

    resolved = resolve_client_ip(
        _request(peer=_GUEST, headers={"x-forwarded-for": "1.2.3.4"}), trusted
    )

    assert resolved == _GUEST


def test_an_untrusted_peer_cannot_forge_its_identity() -> None:
    trusted = TrustedProxies.parse("10.0.0.0/24")

    resolved = resolve_client_ip(
        _request(peer="198.51.100.4", headers={"x-forwarded-for": "1.2.3.4"}), trusted
    )

    assert resolved == "198.51.100.4"


def test_a_trusted_proxy_hands_over_the_real_client() -> None:
    trusted = TrustedProxies.parse("10.0.0.0/24")

    resolved = resolve_client_ip(
        _request(peer=_PROXY, headers={"x-forwarded-for": _GUEST}), trusted
    )

    assert resolved == _GUEST


def test_the_identity_is_the_rightmost_entry_the_trusted_hops_did_not_append() -> None:
    """A client may PREPEND anything it likes to ``X-Forwarded-For``; only what a trusted hop
    appended can be believed. So we walk from the right, skipping our own proxies, and stop at the
    first address one of them actually observed — never the leftmost, which is attacker-authored."""
    trusted = TrustedProxies.parse("10.0.0.0/24")

    resolved = resolve_client_ip(
        _request(peer=_PROXY, headers={"x-forwarded-for": f"1.2.3.4, {_GUEST}, 10.0.0.9"}), trusted
    )

    assert resolved == _GUEST


def test_a_garbage_forwarded_header_falls_back_to_the_peer() -> None:
    trusted = TrustedProxies.parse("10.0.0.0/24")

    resolved = resolve_client_ip(
        _request(peer=_PROXY, headers={"x-forwarded-for": "not-an-ip"}), trusted
    )

    assert resolved == _PROXY


def test_a_malformed_cidr_fails_the_parse_LOUDLY() -> None:
    """A typo'd CIDR must not silently become "trust nobody" (every guest collapses onto the proxy's
    single bucket) nor "trust everybody". It is a boot error, where an operator can still see it."""
    with pytest.raises(ValueError, match="AETHERCAL_TRUSTED_PROXIES"):
        TrustedProxies.parse("10.0.0.0/24, not-a-cidr")


# --------------------------------------------------------------------------------------
# The limiter.
# --------------------------------------------------------------------------------------


def test_the_limiter_admits_up_to_the_cap_and_then_denies() -> None:
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)

    assert [limiter.allow("ip", now=1.0) for _ in range(4)] == [True, True, True, False]


def test_the_window_SLIDES() -> None:
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)

    assert limiter.allow("ip", now=1.0) is True
    assert limiter.allow("ip", now=30.0) is False
    assert limiter.allow("ip", now=62.0) is True


def test_two_clients_have_two_budgets() -> None:
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)

    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("b", now=1.0) is True
    assert limiter.allow("a", now=1.0) is False


def test_the_keyspace_is_BOUNDED() -> None:
    """The limiter's own memory is an attack surface: an unbounded key set is a slow OOM driven by
    the very traffic it is meant to be limiting."""
    limiter = SlidingWindowLimiter(max_requests=5, window_seconds=60.0, max_keys=10)

    for index in range(100):
        limiter.allow(f"ip-{index}", now=1.0)

    assert limiter.key_count() <= 10
