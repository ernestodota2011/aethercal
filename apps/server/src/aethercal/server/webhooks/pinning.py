"""Connect-time IP pinning for outbound webhook delivery — closes DNS rebinding (RF-17 / RNF-5).

The pre-flight guard (:mod:`aethercal.server.webhooks.ssrf`) validates the IPs a URL *resolves* to,
but ``httpx`` re-resolves the hostname when it actually opens the socket. Between those two lookups
an attacker-controlled resolver can flip a name from an allowed IP (which the guard accepts) to a
forbidden one (which the socket then dials): classic DNS rebinding / TOCTOU.

This module removes that window at the root. Instead of handing ``httpx`` a hostname to resolve on
its own, we resolve it *ourselves* at connect time, re-validate the exact address we are about to
dial, and rewrite the request's connection target to that IP literal. ``httpx`` then connects
straight to the validated address with no further DNS lookup, so it can never reach a different host
than the one we checked.

.. rubric:: Why the pin now checks IDENTITY, not just class

Before the operator could declare private networks, "is the address I am about to dial allowed?" was
a complete question: private meant forbidden, so a rebind into private space was caught by the class
check alone. ==The allowlist breaks that.== Once ``192.168.1.0/24`` is declared, ``192.168.1.50`` is
a *legal* address — and a rebind that lands there would sail straight through a guard that only asks
about class. A public hostname would have become a tunnel into the operator's LAN.

So a **private** address may be dialed only when it is one of the addresses the pre-flight guard
actually validated *for this URL* (:func:`aethercal.server.webhooks.ssrf.assert_target_allowed`
returns them). The operator declared a NETWORK, not a licence for any name on the internet to be
re-pointed at it mid-flight. A permitted target can never become a pivot.

**Public** addresses are deliberately exempt from the identity check: a CDN or load balancer
legitimately answers with a different public IP on every lookup, and refusing that would break real
subscribers to protect nothing — a public address was going to be allowed either way.

.. rubric:: Pinning must not weaken TLS

We keep the request's ``Host`` header as the original authority (so the consumer's virtual-host
routing is unaffected) and set the ``sni_hostname`` request extension to the original hostname —
httpcore uses that as the TLS ``server_hostname``, which drives both the SNI sent on the wire *and*
certificate-hostname verification. The certificate is therefore still checked against the real
hostname, never against the pinned IP.

The resolver is injected (:data:`aethercal.server.webhooks.ssrf.Resolver`) so the whole path is
deterministic and offline under test; production passes ``None`` and gets the real ``getaddrinfo``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from aethercal.server.webhooks.allowlist import PrivateTargetAllowlist
from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    BlockReason,
    Resolver,
    TargetUnresolvable,
    default_resolver,
    ip_is_public,
    target_is_allowed,
)

_SNI_EXTENSION = "sni_hostname"


async def pinned_ip_for(
    host: str,
    *,
    resolver: Resolver | None = None,
    allowlist: PrivateTargetAllowlist,
    validated: frozenset[str],
) -> str:
    """Return the single IP to dial for ``host``, re-validated at connect time (RF-17 / RNF-5).

    A literal-IP host is checked directly and returned without any DNS lookup — a literal IS the
    declared destination, so there is nothing for a rebind to change. A named host is resolved via
    ``resolver`` (the real ``getaddrinfo`` when ``None``); the **first** returned address is the one
    that will be dialed, and it must clear two bars:

    1. it must be **allowed** — globally routable, or inside the operator's allowlist;
    2. if it is **private** (allowed only *because* of the allowlist), it must also be a member of
       ``validated`` — the set the pre-flight guard resolved for this same URL. A private address
       that appears only at connect time is a rebind, and it is refused
       (:attr:`~aethercal.server.webhooks.ssrf.BlockReason.DNS_REBIND`) *even when the allowlist
       contains it*.

    Fail-closed throughout: a resolver error or an empty answer raises
    :class:`~aethercal.server.webhooks.ssrf.TargetUnresolvable` (retryable — a DNS blip is a network
    failure, not a policy decision), and a forbidden address raises
    :class:`~aethercal.server.webhooks.ssrf.BlockedUrlError` (terminal). No "shopping" for a
    routable IP in a poisoned record set — the first record is authoritative.
    """
    literal = _as_literal_ip(host)
    if literal is not None:
        if not target_is_allowed(literal, allowlist=allowlist):
            raise BlockedUrlError(
                BlockReason.PRIVATE_TARGET,
                f"{BlockReason.PRIVATE_TARGET.value}: URL host {host!r} is not an allowed address",
            )
        return literal

    resolve = resolver if resolver is not None else default_resolver
    try:
        addresses = await resolve(host)
    except (BlockedUrlError, TargetUnresolvable):
        raise
    except Exception as exc:  # a resolution error is transient: retry, never dead-letter
        raise TargetUnresolvable(f"could not resolve host {host!r}: {exc}") from exc
    if not addresses:
        raise TargetUnresolvable(f"host {host!r} did not resolve to any address")

    pinned = addresses[0]
    if not target_is_allowed(pinned, allowlist=allowlist):
        raise BlockedUrlError(
            BlockReason.PRIVATE_TARGET,
            f"{BlockReason.PRIVATE_TARGET.value}: URL host {host!r} resolves to {pinned}, which is "
            "not an allowed address",
        )
    if not ip_is_public(pinned) and pinned not in validated:
        # Allowed by class, refused by identity. The operator opened a network to the destinations
        # that legitimately live in it — not to every hostname that can be made to point there for
        # the width of one TCP connect.
        raise BlockedUrlError(
            BlockReason.DNS_REBIND,
            f"{BlockReason.DNS_REBIND.value}: URL host {host!r} resolved to {sorted(validated)} "
            f"for the egress guard and to the private address {pinned} at connect time. The "
            "address is inside an allowed network, and the send is still refused: it is not the "
            "destination that was validated, and an allowed network must never become a pivot.",
        )
    return pinned


async def build_pinned_request(  # noqa: PLR0913 — one keyword per injected seam (client/DNS/policy)
    client: httpx.AsyncClient,
    url: str,
    *,
    content: bytes,
    headers: Mapping[str, str],
    resolver: Resolver | None = None,
    allowlist: PrivateTargetAllowlist,
    validated: frozenset[str],
) -> httpx.Request:
    """Build a POST :class:`httpx.Request` that dials only a connect-time-validated address.

    Resolves ``url``'s host, re-validates the address it will dial (:func:`pinned_ip_for`), then
    rewrites the request's connection target to that IP literal while keeping the ``Host`` header at
    the original authority and pinning TLS SNI + certificate verification to the original hostname
    via the ``sni_hostname`` extension. Raises
    :class:`~aethercal.server.webhooks.ssrf.BlockedUrlError` when the URL has no host or the pinned
    address is refused. Sending is left to the caller (``await client.send(request)``) so the
    injected transport — a real one in production, a mock in tests — stays the seam.
    """
    host = urlsplit(url).hostname
    if not host:
        raise BlockedUrlError(BlockReason.NO_HOST, f"URL has no host: {url!r}")

    pinned_ip = await pinned_ip_for(
        host, resolver=resolver, allowlist=allowlist, validated=validated
    )
    # Build against the original URL so httpx derives the correct Host header (hostname[:port]).
    request = client.build_request("POST", url, content=content, headers=headers)
    # Repoint the socket to the validated IP literal; the already-materialized Host header is kept.
    request.url = request.url.copy_with(host=pinned_ip)
    # Keep SNI + cert-hostname verification bound to the real hostname, not the dialed IP. httpcore
    # reads this extension as BYTES (it ``.decode("ascii")``s it into the TLS ``server_hostname``);
    # a str would raise ``AttributeError`` mid-handshake. Webhook hosts are ASCII/punycode, so
    # ascii-encoding is the exact round-trip httpcore expects.
    request.extensions[_SNI_EXTENSION] = host.encode("ascii")
    return request


def _as_literal_ip(host: str) -> str | None:
    """The host as an IP literal, or ``None`` when it is a name that needs DNS."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


__all__ = [
    "build_pinned_request",
    "pinned_ip_for",
]
