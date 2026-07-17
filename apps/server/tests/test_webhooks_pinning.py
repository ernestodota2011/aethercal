"""IP-pinning tests: connect-time validation + SNI/Host/cert preservation (RF-17 / RNF-5).

Fully offline — an injected fake resolver stands in for DNS, and requests are *built* (never sent),
so no test touches the network. The pin dials exactly the address it validates, closing the DNS
rebinding window that the pre-flight guard alone leaves open (a name that resolves public for the
guard and private microseconds later for the socket).

.. rubric:: The pin is what stops the allowlist becoming a pivot

Once the operator declares ``192.168.1.0/24``, "is this address private?" is no longer a sufficient
question — ``192.168.1.50`` is now a *legal* destination. So a rebind that lands **inside the
allowlisted range** would sail through a pin that only re-checks the address CLASS. The pin
therefore also checks IDENTITY: a private address may be dialed only when it is one of the addresses
the pre-flight guard actually validated for this URL. A name that resolved public and then rebinds
into the allowlisted LAN is refused (``blocked-dns-rebind``) — the operator declared a NETWORK, not
a licence for any hostname on the internet to be pointed at it mid-flight.

Public addresses are exempt from the identity check on purpose: a CDN or load balancer legitimately
answers with a different public IP on every lookup, and rejecting that would break real subscribers
to protect nothing (a public address was allowed either way).
"""

from __future__ import annotations

import httpx
import pytest

from aethercal.server.webhooks.allowlist import NO_PRIVATE_TARGETS, PrivateTargetAllowlist
from aethercal.server.webhooks.pinning import build_pinned_request, pinned_ip_for
from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    BlockReason,
    Resolver,
    TargetUnresolvable,
)

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "93.184.216.35"

LAN = PrivateTargetAllowlist.parse("192.168.1.0/24")


def _resolves_to(*ips: str) -> Resolver:
    """A fake resolver that returns a fixed set of IPs for any host."""

    async def _resolver(_host: str) -> list[str]:
        return list(ips)

    return _resolver


async def _never_resolve(host: str) -> list[str]:
    """A resolver that must never be called (used to prove a literal IP skips DNS)."""
    raise AssertionError(f"resolver must not be called for literal-IP host {host!r}")


# --------------------------------------------------------------------------------------
# Public targets.
# --------------------------------------------------------------------------------------


async def test_pinned_ip_for_returns_the_dialed_public_address() -> None:
    pinned = await pinned_ip_for(
        "consumer.test",
        resolver=_resolves_to(PUBLIC_IP),
        allowlist=NO_PRIVATE_TARGETS,
        validated=frozenset({PUBLIC_IP}),
    )
    assert pinned == PUBLIC_IP


async def test_a_public_address_need_not_match_the_preflight_answer() -> None:
    """Round-robin DNS and CDNs answer differently on every lookup. Both answers are public, so both
    were always going to be allowed; demanding they be the SAME address would break real subscribers
    to protect nothing."""
    pinned = await pinned_ip_for(
        "cdn.test",
        resolver=_resolves_to(OTHER_PUBLIC_IP),
        allowlist=NO_PRIVATE_TARGETS,
        validated=frozenset({PUBLIC_IP}),
    )
    assert pinned == OTHER_PUBLIC_IP


async def test_pinned_ip_for_allows_a_public_literal_without_dns() -> None:
    pinned = await pinned_ip_for(
        "8.8.8.8",
        resolver=_never_resolve,
        allowlist=NO_PRIVATE_TARGETS,
        validated=frozenset({"8.8.8.8"}),
    )
    assert pinned == "8.8.8.8"


# --------------------------------------------------------------------------------------
# Fail-closed without an allowlist (today's behaviour, unchanged).
# --------------------------------------------------------------------------------------


async def test_pinned_ip_for_blocks_when_the_dialed_address_is_private() -> None:
    # The address actually dialed is re-validated at connect time; a rebind to loopback is refused.
    with pytest.raises(BlockedUrlError) as excinfo:
        await pinned_ip_for(
            "rebind.test",
            resolver=_resolves_to("127.0.0.1"),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET


async def test_pinned_ip_for_validates_the_first_address_it_will_dial() -> None:
    # Fail-closed: the address dialed is the first record; a private first record blocks the send
    # even when a later record is public — no "shopping" for a routable IP in a poisoned record set.
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for(
            "mixed.test",
            resolver=_resolves_to("10.0.0.1", PUBLIC_IP),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )


async def test_pinned_ip_for_blocks_a_private_literal_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for(
            "127.0.0.1",
            resolver=_never_resolve,
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({"127.0.0.1"}),
        )


async def test_pinned_ip_for_blocks_a_link_local_metadata_literal_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for(
            "169.254.169.254",
            resolver=_never_resolve,
            allowlist=LAN,
            validated=frozenset({"169.254.169.254"}),
        )


async def test_pinned_ip_for_is_unresolvable_on_empty_resolution() -> None:
    # A network failure, not a policy block: retryable, never a permanent dead-letter.
    with pytest.raises(TargetUnresolvable):
        await pinned_ip_for(
            "void.test",
            resolver=_resolves_to(),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )


async def test_pinned_ip_for_is_unresolvable_on_resolver_error() -> None:
    async def _boom(_host: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(TargetUnresolvable):
        await pinned_ip_for(
            "broken.test",
            resolver=_boom,
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )


# --------------------------------------------------------------------------------------
# With an allowlist: the declared LAN is dialable — but only as the destination it declared.
# --------------------------------------------------------------------------------------


async def test_a_declared_private_target_is_pinned_when_it_is_the_validated_address() -> None:
    # The ordinary self-host: `http://n8n.lan:5678` resolves to the same LAN address both times.
    pinned = await pinned_ip_for(
        "n8n.lan",
        resolver=_resolves_to("192.168.1.50"),
        allowlist=LAN,
        validated=frozenset({"192.168.1.50"}),
    )
    assert pinned == "192.168.1.50"


async def test_a_declared_private_literal_is_pinned_without_dns() -> None:
    pinned = await pinned_ip_for(
        "192.168.1.50",
        resolver=_never_resolve,
        allowlist=LAN,
        validated=frozenset({"192.168.1.50"}),
    )
    assert pinned == "192.168.1.50"


async def test_a_public_host_that_rebinds_into_the_allowlisted_cidr_is_refused() -> None:
    """==The attack the allowlist would otherwise create, and the reason the pin checks IDENTITY.==

    ``evil.test`` resolves PUBLIC for the pre-flight guard and then, microseconds later, to
    ``192.168.1.50`` for the socket — an address the operator DID declare. A pin that only asked "is
    this address allowed?" would answer yes and dial straight into the LAN. The declared destination
    was the public address the guard validated; the rebound one is not it, so the send is refused.
    ==A permitted target must never become a pivot.=="""
    with pytest.raises(BlockedUrlError) as excinfo:
        await pinned_ip_for(
            "evil.test",
            resolver=_resolves_to("192.168.1.50"),  # in the allowlist — and still refused
            allowlist=LAN,
            validated=frozenset({PUBLIC_IP}),  # what the guard actually validated
        )
    assert excinfo.value.reason is BlockReason.DNS_REBIND
    assert excinfo.value.reason.value == "blocked-dns-rebind"  # distinct, greppable


async def test_a_private_target_outside_the_cidr_is_refused_as_a_private_target() -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await pinned_ip_for(
            "other.lan",
            resolver=_resolves_to("10.0.0.9"),
            allowlist=LAN,
            validated=frozenset({"10.0.0.9"}),
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET


# --------------------------------------------------------------------------------------
# The built request: dial the IP, keep TLS bound to the hostname.
# --------------------------------------------------------------------------------------


async def test_build_pinned_request_dials_the_ip_but_keeps_host_and_sni() -> None:
    async with httpx.AsyncClient() as client:
        request = await build_pinned_request(
            client,
            "https://consumer.test/hook",
            content=b"payload",
            headers={"Content-Type": "application/json"},
            resolver=_resolves_to(PUBLIC_IP),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )
    assert request.url.host == PUBLIC_IP  # the TCP connection targets the validated IP literal
    assert request.headers["Host"] == "consumer.test"  # vhost routing keeps the real hostname
    # SNI + cert host = real hostname, as BYTES (httpcore .decode("ascii")s this extension).
    assert request.extensions["sni_hostname"] == b"consumer.test"
    assert request.content == b"payload"


async def test_build_pinned_request_preserves_a_non_default_port() -> None:
    async with httpx.AsyncClient() as client:
        request = await build_pinned_request(
            client,
            "https://consumer.test:8443/hook",
            content=b"x",
            headers={},
            resolver=_resolves_to(PUBLIC_IP),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )
    assert request.url.host == PUBLIC_IP
    assert request.url.port == 8443
    assert request.headers["Host"] == "consumer.test:8443"
    assert request.extensions["sni_hostname"] == b"consumer.test"


async def test_build_pinned_request_dials_a_declared_private_target() -> None:
    # The self-hoster's n8n on the LAN, on its own port.
    async with httpx.AsyncClient() as client:
        request = await build_pinned_request(
            client,
            "http://n8n.lan:5678/webhook/aethercal",
            content=b"x",
            headers={},
            resolver=_resolves_to("192.168.1.50"),
            allowlist=LAN,
            validated=frozenset({"192.168.1.50"}),
        )
    assert request.url.host == "192.168.1.50"
    assert request.url.port == 5678
    assert request.headers["Host"] == "n8n.lan:5678"


async def test_sni_extension_is_bytes_httpcore_can_decode() -> None:
    """The sni_hostname extension must be the exact type httpcore consumes.

    httpcore derives the TLS ``server_hostname`` by calling ``.decode("ascii")`` on this extension,
    so a ``str`` would blow up mid-handshake with ``AttributeError``. Assert the value is ``bytes``
    and round-trips through httpcore's decode back to the real hostname (never the dialed IP).
    """
    async with httpx.AsyncClient() as client:
        request = await build_pinned_request(
            client,
            "https://consumer.test/hook",
            content=b"x",
            headers={},
            resolver=_resolves_to(PUBLIC_IP),
            allowlist=NO_PRIVATE_TARGETS,
            validated=frozenset({PUBLIC_IP}),
        )
    sni = request.extensions["sni_hostname"]
    assert isinstance(sni, bytes)
    assert sni.decode("ascii") == "consumer.test"


async def test_build_pinned_request_blocks_a_rebound_private_ip() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(BlockedUrlError):
            await build_pinned_request(
                client,
                "https://rebind.test/hook",
                content=b"x",
                headers={},
                resolver=_resolves_to("10.0.0.1"),
                allowlist=NO_PRIVATE_TARGETS,
                validated=frozenset({PUBLIC_IP}),
            )


async def test_build_pinned_request_rejects_a_url_without_host() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(BlockedUrlError) as excinfo:
            await build_pinned_request(
                client,
                "https:///nohost",
                content=b"x",
                headers={},
                resolver=_resolves_to(PUBLIC_IP),
                allowlist=NO_PRIVATE_TARGETS,
                validated=frozenset({PUBLIC_IP}),
            )
    assert excinfo.value.reason is BlockReason.NO_HOST
