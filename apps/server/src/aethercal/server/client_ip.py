"""WHO a request is, when the request carries no credentials. ==The proxy contract, declared.==

The public router has no API key, so a caller's only identity is their address — and behind a
reverse proxy the transport peer is the PROXY, not the caller. There are exactly two ways to get
this wrong, and ==neither of them raises==:

* **count the peer.** Behind a CDN every guest collapses onto one address: the first handful of
  requests exhaust the cap, and the endpoint then denies service to everybody. A self-inflicted
  outage that looks exactly like an attack — and reads in the logs like one;
* **believe ``X-Forwarded-For`` from anybody.** The header is caller-authored. A client sets
whatever
  it likes, takes a fresh identity per request, and the cap enforces nothing — while the dashboard
  fills with plausible client addresses. The same no-op, with better decoration.

So the header is honoured ONLY from a peer the operator has DECLARED (``AETHERCAL_TRUSTED_PROXIES``,
a list of CIDRs), and within it the identity is the **rightmost entry that is not itself a trusted
proxy** — the address the outermost hop we trust actually observed. Whatever a client prepends to
the left of that is decoration, and is skipped.

Empty by default, which means: trust nobody, count the peer. That is the SECURE default (nothing can
be forged) and the WRONG one behind a proxy — so ``deploy/docker-compose.yml`` and ``deploy/README``
say, in the place an operator is actually looking, that a real deployment must declare its proxy.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from starlette.requests import Request

TRUSTED_PROXIES_ENV = "AETHERCAL_TRUSTED_PROXIES"
FORWARDED_FOR_HEADER = "X-Forwarded-For"

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True, slots=True)
class TrustedProxies:
    """The peers whose ``X-Forwarded-For`` this instance believes. Empty = nobody."""

    networks: tuple[_Network, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> TrustedProxies:
        """Parse a comma-separated CIDR list. ==A malformed entry FAILS; it is not dropped.==

        A silently-ignored typo is the worst outcome available here: the operator believes they
        declared their proxy, the header is then never trusted, every guest shares the proxy's
        single
        bucket — and the endpoint denies service to everyone, having been configured, in writing, by
        somebody who did everything right. It is a boot error, where it can still be seen.
        """
        networks: list[_Network] = []
        for entry in (item.strip() for item in raw.split(",")):
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError as exc:
                raise ValueError(
                    f"{TRUSTED_PROXIES_ENV}: {entry!r} is not a valid CIDR. It is the "
                    "comma-separated list of the networks whose X-Forwarded-For header this "
                    "instance may "
                    'believe (e.g. "10.0.0.0/24,172.18.0.0/16" — the reverse proxy, or the compose '
                    "network the booking page runs on). Leave it EMPTY to trust nobody and "
                    "count the "
                    "transport peer."
                ) from exc
        return cls(networks=tuple(networks))

    def trusts(self, host: str) -> bool:
        """Is ``host`` one of our own hops? A peer with no address (a unix socket) never is."""
        if not self.networks:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in self.networks)


def _normalize(value: str) -> str | None:
    """The canonical text form of ``value`` if it is an IP address, else ``None``.

    Canonical, so ``2001:0db8::1`` and ``2001:db8::1`` are ONE identity rather than two buckets and
    two rows; ``None``, so a forged or garbage header value is refused rather than stored.
    """
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def resolve_client_ip(request: Request, trusted: TrustedProxies) -> str | None:
    """The address this request came from — believed only as far as the proxy contract allows.

    ``None`` when the peer has no address at all. Both callers read that as "unknown", never as
    "everyone": the limiter falls back to a single shared key, and ``bookings.source_ip`` stays NULL
    — which means *not capped*, not *capped at zero*.
    """
    client = request.client
    peer = client.host if client is not None else None

    if peer is not None and trusted.trusts(peer):
        forwarded = request.headers.get(FORWARDED_FOR_HEADER)
        if forwarded:
            # Right to left. The RIGHTMOST entry was appended by the hop nearest to us; skip the
            # hops
            # we own, and the first address left is one a trusted proxy genuinely observed. Reading
            # it left-to-right — "the original client", as every tutorial has it — reads the part of
            # the header the CLIENT wrote, which is to say: it believes the attacker.
            for entry in reversed([item.strip() for item in forwarded.split(",")]):
                candidate = _normalize(entry)
                if candidate is None or trusted.trusts(candidate):
                    continue
                return candidate

    return _normalize(peer) if peer is not None else None


__all__ = ["FORWARDED_FOR_HEADER", "TRUSTED_PROXIES_ENV", "TrustedProxies", "resolve_client_ip"]
