"""SSRF egress guard for outbound webhook delivery (RF-17 / RNF-5).

A subscriber's ``url`` is caller-supplied, so a naive delivery worker would happily POST to
``169.254.169.254`` (cloud metadata), ``127.0.0.1``, or an RFC1918 host and reach into the server's
own network — a classic Server-Side Request Forgery. This module is the egress guard: it resolves
the target host right before the send and refuses any address the instance is not allowed to reach.
Validating at *send* time (not only at registration) catches a URL that only turns malicious after
it is stored.

.. rubric:: "Allowed" is not the same as "public"

Refusing everything non-routable is right against SSRF and **wrong for a self-hosted product**: the
operator's own n8n/CRM/ERP lives on the LAN, the Docker network or the VPN, and a guard that blocks
it makes "connect AetherCal to your n8n" impossible. So the predicate is
:func:`target_is_allowed` = *globally routable* **or** *inside a network the OPERATOR declared*
(:mod:`aethercal.server.webhooks.allowlist`). The allowlist is read from the environment and from
nowhere else, so a caller who controls the URL cannot widen it. With nothing declared the allowlist
is empty and this guard behaves exactly as it always has.

.. rubric:: Two refusals, and why they must not be one

* :class:`BlockedUrlError` is a **policy** refusal: the target is not one this instance may reach.
  It carries a :class:`BlockReason` — a stable, greppable token — and it is **terminal**: retrying
  cannot change the answer, so the worker dead-letters the delivery *with the reason on the row*.
* :class:`TargetUnresolvable` is a **network** failure: DNS said nothing, or said it badly. It is
  **retryable**, and it used to be neither — a resolver hiccup raised ``BlockedUrlError`` and the
  worker permanently killed a perfectly legitimate delivery, silently, on one bad tick.

.. rubric:: Defence in depth

This is the *pre-flight* guard (resolve every A/AAAA record, require all allowed). On its own it
validates the resolved IPs but does not *pin* them into the HTTP connection, so a resolver that
returns an allowed IP here and a forbidden one to httpx's own lookup microseconds later (DNS
rebinding) would slip through. That hole is closed by the connect-time pin in
:mod:`aethercal.server.webhooks.pinning`, which dials only the exact, re-validated address — and
which is handed the set this guard returns, so a *private* address may be dialed only when it is one
this guard actually saw. An allowlisted network can therefore never be pivoted into by a hostname
that resolved public a moment earlier.

The DNS resolver is injected (:data:`Resolver`) so the whole guard is deterministic and offline
under test; production passes ``None`` and gets a real ``getaddrinfo`` lookup.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from enum import StrEnum
from socket import SOCK_STREAM
from urllib.parse import urlsplit

from aethercal.server.webhooks.allowlist import ALLOWLIST_ENV_VAR, PrivateTargetAllowlist

Resolver = Callable[[str], Awaitable[list[str]]]
"""Host → resolved IP strings. Injected so tests never touch real DNS (RF-17 / RNF-5)."""

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockReason(StrEnum):
    """Why a target was refused by policy. ==The value is the greppable token, and it is stable.==

    It goes on the delivery row (``webhook_deliveries.error_reason``), into the log line, and into
    the ``/metrics`` exposition as a bounded label — so "the operator cannot see why nothing is
    being delivered" stops being possible. Distinct from a network failure ON PURPOSE: an operator
    staring at a dead delivery must be able to tell "you pointed this at an address I am not allowed
    to reach" apart from "your DNS was down".
    """

    PRIVATE_TARGET = "blocked-private-target"
    """Not globally routable, and not inside any network the operator declared. ==The common one.==

    If this is what the operator sees for their OWN n8n, the fix is to declare the network in
    ``AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS`` — and the log line says so."""

    DNS_REBIND = "blocked-dns-rebind"
    """The address the socket was about to dial is private and is NOT one the pre-flight guard
    validated for this URL — the name changed its answer between the two lookups.

    Refused even when the rebound address is inside the allowlist: the operator declared a NETWORK,
    not a licence for any hostname on the internet to be re-pointed at it mid-flight."""

    BAD_SCHEME = "blocked-bad-scheme"
    NO_HOST = "blocked-no-host"


class WebhookTargetError(ValueError):
    """Base: this delivery cannot be sent to this target *right now*. See the two subclasses."""


class BlockedUrlError(WebhookTargetError):
    """==TERMINAL.== The target is refused by policy; no retry can change that (RF-17 / RNF-5)."""

    def __init__(self, reason: BlockReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class TargetUnresolvable(WebhookTargetError):
    """==RETRYABLE.== DNS did not answer (or answered nothing). A NETWORK failure, not a policy one.

    Kept out of :class:`BlockedUrlError`'s hierarchy deliberately, because the delivery worker
    branches on the type: a blocked target is dead-lettered immediately, and doing that to a DNS
    timeout threw away a legitimate delivery with five attempts still unspent."""


async def default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its IP strings via the running loop's ``getaddrinfo`` (RF-17 / RNF-5).

    The production DNS default shared by both the pre-flight guard (:func:`assert_target_allowed`)
    and the connect-time pin (``webhooks.pinning``); tests inject a fake resolver instead.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def ip_is_public(ip: str) -> bool:
    """Return ``True`` iff ``ip`` is a globally routable address (RF-17 / RNF-5).

    Uses :attr:`ipaddress.IPv4Address.is_global` as the positive criterion — allowed only when IANA
    marks the address globally reachable, which also rejects shared CGNAT space (``100.64.0.0/10``)
    and the documentation/benchmark ranges — AND layers explicit denials for special-use ranges
    ``is_global`` does not itself exclude (notably multicast, ``224.0.0.0/4``), plus belt-and-braces
    checks for private (RFC1918), loopback, link-local (``169.254.169.254`` cloud metadata),
    reserved, and unspecified (``0.0.0.0`` / ``::``) addresses. Pure — no DNS, no I/O.

    ==This stays the definition of PUBLIC, untouched by the allowlist.== The allowlist is a
    separate, additive question (:func:`target_is_allowed`), which is what keeps "is this the open
    internet?" answerable on its own.
    """
    address = ipaddress.ip_address(ip)
    return address.is_global and not (
        address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_private
    )


def target_is_allowed(ip: str, *, allowlist: PrivateTargetAllowlist) -> bool:
    """May this instance POST to ``ip``? Public always — private only where the OPERATOR said so.

    The full predicate, and the only one the guard and the pin ever ask. Note the shape: the
    allowlist can only ever *widen* the set of reachable addresses, and only within the private
    ranges it is allowed to name (link-local and the default route cannot be declared at all — see
    :mod:`aethercal.server.webhooks.allowlist`). A caller-supplied URL cannot influence either term.
    """
    return ip_is_public(ip) or allowlist.permits(ip)


async def assert_target_allowed(
    url: str, *, resolver: Resolver | None = None, allowlist: PrivateTargetAllowlist
) -> frozenset[str]:
    """Admit ``url`` for delivery, returning the exact addresses that were validated.

    Requires an ``http``/``https`` scheme and a hostname. A literal-IP host is checked directly (no
    DNS). A named host is resolved via ``resolver`` (real ``getaddrinfo`` when ``None``) and
    **every** returned address must be allowed — one forbidden record poisons the whole target, so
    there is no "shopping" for a good IP in a mixed answer.

    Raises :class:`BlockedUrlError` (terminal, with its :class:`BlockReason`) when the target is
    refused by policy, and :class:`TargetUnresolvable` (retryable) when DNS gives nothing back.

    The returned set is not decoration: :mod:`aethercal.server.webhooks.pinning` takes it and
    refuses to dial a *private* address that is not in it. That is what stops an allowlisted network
    from being reachable by any hostname that happens to rebind into it.

    ``allowlist`` is keyword-only and has **no default** on purpose. A default would let a call site
    silently fall back to "nothing private is allowed" — which is precisely the silent no-op this
    whole cut exists to remove; the operator would have configured their LAN and nothing would ship.
    Without a default, forgetting it is a type error at build time.
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(
            BlockReason.BAD_SCHEME, f"URL scheme must be http or https, got {parts.scheme!r}"
        )
    host = parts.hostname
    if not host:
        raise BlockedUrlError(BlockReason.NO_HOST, f"URL has no host: {url!r}")

    literal = _as_literal_ip(host)
    if literal is not None:
        if not target_is_allowed(literal, allowlist=allowlist):
            raise BlockedUrlError(BlockReason.PRIVATE_TARGET, _refusal(host, literal, allowlist))
        return frozenset({literal})

    addresses = await _resolve(host, resolver)
    for address in addresses:
        if not target_is_allowed(address, allowlist=allowlist):
            raise BlockedUrlError(BlockReason.PRIVATE_TARGET, _refusal(host, address, allowlist))
    return frozenset(addresses)


async def _resolve(host: str, resolver: Resolver | None) -> list[str]:
    """Resolve ``host``, or raise :class:`TargetUnresolvable`. A DNS failure is not a policy."""
    resolve = resolver if resolver is not None else default_resolver
    try:
        addresses = await resolve(host)
    except BlockedUrlError:
        # A resolver that refuses on policy grounds keeps its reason; it is not "DNS was down".
        raise
    except Exception as exc:
        raise TargetUnresolvable(f"could not resolve host {host!r}: {exc}") from exc
    if not addresses:
        raise TargetUnresolvable(f"host {host!r} did not resolve to any address")
    return addresses


def _as_literal_ip(host: str) -> str | None:
    """The host as an IP literal, or ``None`` when it is a name that needs DNS."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _refusal(host: str, address: str, allowlist: PrivateTargetAllowlist) -> str:
    """The refusal message — which must be actionable, because the reader is usually the OPERATOR.

    The common case is not an attacker: it is somebody who pointed AetherCal at their own n8n and
    got nothing. Telling them "blocked" and stopping there is what made this failure so expensive,
    so the message names the address, the reason, and the exact variable that would allow it.
    """
    if allowlist.is_empty:
        hint = (
            "No private network is declared. If this is YOUR service (n8n, a CRM, an ERP on the "
            f"same LAN/Docker/VPN), declare its network in {ALLOWLIST_ENV_VAR} — e.g. "
            f"{ALLOWLIST_ENV_VAR}=192.168.1.0/24"
        )
    else:
        declared = ", ".join(str(network) for network in allowlist.networks)
        hint = (
            f"{ALLOWLIST_ENV_VAR} declares {declared}, and {address} is not inside it. Add its "
            "network if the target really is yours."
        )
    return (
        f"{BlockReason.PRIVATE_TARGET.value}: {host!r} resolves to {address}, which is neither "
        f"globally routable nor inside an allowed private network. {hint}"
    )


__all__ = [
    "BlockReason",
    "BlockedUrlError",
    "Resolver",
    "TargetUnresolvable",
    "WebhookTargetError",
    "assert_target_allowed",
    "default_resolver",
    "ip_is_public",
    "target_is_allowed",
]
