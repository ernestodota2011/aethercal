"""IP-pinning tests: connect-time validation + SNI/Host/cert preservation (RF-17 / RNF-5).

Fully offline — an injected fake resolver stands in for DNS, and requests are *built* (never sent),
so no test touches the network. The pin dials exactly the address it validates, closing the DNS
rebinding window that the pre-flight guard alone leaves open (a name that resolves public for the
guard and private microseconds later for the socket).
"""

from __future__ import annotations

import httpx
import pytest

from aethercal.server.webhooks.pinning import build_pinned_request, pinned_ip_for
from aethercal.server.webhooks.ssrf import BlockedUrlError, Resolver

PUBLIC_IP = "93.184.216.34"


def _resolves_to(*ips: str) -> Resolver:
    """A fake resolver that returns a fixed set of IPs for any host."""

    async def _resolver(_host: str) -> list[str]:
        return list(ips)

    return _resolver


async def _never_resolve(host: str) -> list[str]:
    """A resolver that must never be called (used to prove a literal IP skips DNS)."""
    raise AssertionError(f"resolver must not be called for literal-IP host {host!r}")


async def test_pinned_ip_for_returns_the_dialed_public_address() -> None:
    assert await pinned_ip_for("consumer.test", resolver=_resolves_to(PUBLIC_IP)) == PUBLIC_IP


async def test_pinned_ip_for_blocks_when_the_dialed_address_is_private() -> None:
    # The address actually dialed is re-validated at connect time; a rebind to loopback is refused.
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("rebind.test", resolver=_resolves_to("127.0.0.1"))


async def test_pinned_ip_for_validates_the_first_address_it_will_dial() -> None:
    # Fail-closed: the address dialed is the first record; a private first record blocks the send
    # even when a later record is public — no "shopping" for a routable IP in a poisoned record set.
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("mixed.test", resolver=_resolves_to("10.0.0.1", PUBLIC_IP))


async def test_pinned_ip_for_allows_a_public_literal_without_dns() -> None:
    assert await pinned_ip_for("8.8.8.8", resolver=_never_resolve) == "8.8.8.8"


async def test_pinned_ip_for_blocks_a_private_literal_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("127.0.0.1", resolver=_never_resolve)


async def test_pinned_ip_for_blocks_a_link_local_metadata_literal_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("169.254.169.254", resolver=_never_resolve)


async def test_pinned_ip_for_fails_closed_on_empty_resolution() -> None:
    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("void.test", resolver=_resolves_to())


async def test_pinned_ip_for_fails_closed_on_resolver_error() -> None:
    async def _boom(_host: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(BlockedUrlError):
        await pinned_ip_for("broken.test", resolver=_boom)


async def test_build_pinned_request_dials_the_ip_but_keeps_host_and_sni() -> None:
    async with httpx.AsyncClient() as client:
        request = await build_pinned_request(
            client,
            "https://consumer.test/hook",
            content=b"payload",
            headers={"Content-Type": "application/json"},
            resolver=_resolves_to(PUBLIC_IP),
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
        )
    assert request.url.host == PUBLIC_IP
    assert request.url.port == 8443
    assert request.headers["Host"] == "consumer.test:8443"
    assert request.extensions["sni_hostname"] == b"consumer.test"


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
            )


async def test_build_pinned_request_rejects_a_url_without_host() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(BlockedUrlError):
            await build_pinned_request(
                client,
                "https:///nohost",
                content=b"x",
                headers={},
                resolver=_resolves_to(PUBLIC_IP),
            )
