"""Connect-time IP pinning for outbound webhook delivery — closes DNS rebinding (RF-17 / RNF-5).

The pre-flight guard (:mod:`aethercal.server.webhooks.ssrf`) validates the IPs a URL *resolves* to,
but ``httpx`` re-resolves the hostname when it actually opens the socket. Between those two lookups
an attacker-controlled resolver can flip a name from a public IP (which the guard accepts) to a
private/loopback/link-local one (which the socket then dials): classic DNS rebinding / TOCTOU.

This module removes that window at the root. Instead of handing ``httpx`` a hostname to resolve on
its own, we resolve it *ourselves* at connect time, re-validate the exact address we are about to
dial (:func:`aethercal.server.webhooks.ssrf.ip_is_public`, fail-closed), and rewrite the request's
connection target to that IP literal. ``httpx`` then connects straight to the validated address with
no further DNS lookup, so it can never reach a different host than the one we checked.

Pinning the IP must not weaken TLS. We keep the request's ``Host`` header as the original authority
(so the consumer's virtual-host routing is unaffected) and set the ``sni_hostname`` request
extension to the original hostname — httpcore uses that as the TLS ``server_hostname``, which drives
both the SNI sent on the wire *and* certificate-hostname verification. The certificate is therefore
still checked against the real hostname, never against the pinned IP.

The resolver is injected (:data:`aethercal.server.webhooks.ssrf.Resolver`) so the whole path is
deterministic and offline under test; production passes ``None`` and gets the real ``getaddrinfo``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    Resolver,
    default_resolver,
    ip_is_public,
)

_SNI_EXTENSION = "sni_hostname"


async def pinned_ip_for(host: str, *, resolver: Resolver | None = None) -> str:
    """Return the single public IP to dial for ``host``, validated at connect time (RF-17 / RNF-5).

    A literal-IP host is validated directly and returned without any DNS lookup. A named host is
    resolved via ``resolver`` (the real ``getaddrinfo`` when ``None``); the **first** returned
    address is the one that will be dialed, and it must be globally routable. The exact IP that gets
    a socket is re-checked here, so a record that rebinds to a private/loopback/link-local or CGNAT
    address after the pre-flight guard is refused. Fail-closed: an empty resolution, a resolver
    error, or a non-public dialed address all raise :class:`BlockedUrlError`. No "shopping" for a
    routable IP in a poisoned record set — the first record is authoritative.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not ip_is_public(str(literal)):
            raise BlockedUrlError(f"URL host {host!r} is not a public address")
        return str(literal)

    resolve = resolver if resolver is not None else default_resolver
    try:
        addresses = await resolve(host)
    except BlockedUrlError:
        raise
    except Exception as exc:  # fail closed: any resolution error blocks the send
        raise BlockedUrlError(f"could not resolve host {host!r}") from exc
    if not addresses:
        raise BlockedUrlError(f"host {host!r} did not resolve to any address")

    pinned = addresses[0]
    if not ip_is_public(pinned):
        raise BlockedUrlError(f"URL host {host!r} resolves to non-public address {pinned}")
    return pinned


async def build_pinned_request(
    client: httpx.AsyncClient,
    url: str,
    *,
    content: bytes,
    headers: Mapping[str, str],
    resolver: Resolver | None = None,
) -> httpx.Request:
    """Build a POST :class:`httpx.Request` that dials only a connect-time-validated public IP.

    Resolves ``url``'s host, re-validates the address it will dial (:func:`pinned_ip_for`), then
    rewrites the request's connection target to that IP literal while keeping the ``Host`` header at
    the original authority and pinning TLS SNI + certificate verification to the original hostname
    via the ``sni_hostname`` extension. Raises :class:`BlockedUrlError` when the URL has no host or
    the pinned address is not public. Sending is left to the caller (``await client.send(request)``)
    so the injected transport — a real one in production, a mock in tests — stays the seam.
    """
    host = urlsplit(url).hostname
    if not host:
        raise BlockedUrlError(f"URL has no host: {url!r}")

    pinned_ip = await pinned_ip_for(host, resolver=resolver)
    # Build against the original URL so httpx derives the correct Host header (hostname[:port]).
    request = client.build_request("POST", url, content=content, headers=headers)
    # Repoint the socket to the validated IP literal; the already-materialized Host header is kept.
    request.url = request.url.copy_with(host=pinned_ip)
    # SNI + cert-hostname verification stay bound to the real hostname, not the dialed IP.
    request.extensions[_SNI_EXTENSION] = host
    return request


__all__ = [
    "build_pinned_request",
    "pinned_ip_for",
]
