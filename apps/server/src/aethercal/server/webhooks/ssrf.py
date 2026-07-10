"""SSRF egress guard for outbound webhook delivery (RF-17 / RNF-5).

A subscriber's ``url`` is caller-supplied, so a naive delivery worker would happily POST to
``169.254.169.254`` (cloud metadata), ``127.0.0.1``, or an RFC1918 host and reach into the server's
own network — a classic Server-Side Request Forgery. This module is the egress allowlist-by-
exclusion: it resolves the target host right before the send and refuses any address that is not
globally routable. Resolving at *send* time (not only at registration) is what defeats DNS
rebinding — the IP that is inspected here is the IP that will be dialed.

The DNS resolver is injected (:data:`Resolver`) so the whole guard is deterministic and offline
under test; production passes ``None`` and gets a real ``getaddrinfo`` lookup. The guard fails
closed: a missing scheme/host, an empty resolution, or a resolver error all raise
:class:`BlockedUrlError`.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from socket import SOCK_STREAM
from urllib.parse import urlsplit

Resolver = Callable[[str], Awaitable[list[str]]]
"""Host → resolved IP strings. Injected so tests never touch real DNS (RF-17 / RNF-5)."""

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedUrlError(ValueError):
    """Raised when a webhook URL is refused by the SSRF egress guard (RF-17 / RNF-5)."""


async def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its IP strings via the running loop's ``getaddrinfo`` (RF-17 / RNF-5)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def ip_is_public(ip: str) -> bool:
    """Return ``True`` iff ``ip`` is a globally routable address (RF-17 / RNF-5).

    Blocks private (RFC1918), loopback, link-local (which covers the ``169.254.169.254`` cloud
    metadata endpoint), multicast, reserved, and unspecified (``0.0.0.0`` / ``::``) ranges. Pure —
    it performs no DNS and no I/O.
    """
    address = ipaddress.ip_address(ip)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def assert_public_url(url: str, *, resolver: Resolver | None = None) -> None:
    """Raise :class:`BlockedUrlError` unless ``url`` resolves solely to public IPs (RF-17 / RNF-5).

    Requires an ``http``/``https`` scheme and a hostname. A literal-IP host is checked directly
    (no DNS). A named host is resolved via ``resolver`` (real ``getaddrinfo`` when ``None``) and
    every returned address must be public; an empty or failed resolution is refused. Returns
    ``None`` when the URL is safe to dial.
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"URL scheme must be http or https, got {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise BlockedUrlError(f"URL has no host: {url!r}")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not ip_is_public(str(literal)):
            raise BlockedUrlError(f"URL host {host!r} is not a public address")
        return

    resolve = resolver if resolver is not None else _default_resolver
    try:
        addresses = await resolve(host)
    except BlockedUrlError:
        raise
    except Exception as exc:  # fail closed: any resolution error blocks the send
        raise BlockedUrlError(f"could not resolve host {host!r}") from exc
    if not addresses:
        raise BlockedUrlError(f"host {host!r} did not resolve to any address")
    for address in addresses:
        if not ip_is_public(address):
            raise BlockedUrlError(f"URL host {host!r} resolves to non-public address {address}")


__all__ = [
    "BlockedUrlError",
    "Resolver",
    "assert_public_url",
    "ip_is_public",
]
