"""SSRF egress-guard tests: public/non-public classification and URL admission (RF-17 / RNF-5).

Fully offline and hermetic — ``ip_is_public`` is pure, and ``assert_public_url`` takes an injected
fake resolver so no test ever touches real DNS. A literal-IP URL must be judged without calling the
resolver at all (proven by injecting a resolver that raises if used).
"""

from __future__ import annotations

import pytest

from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    Resolver,
    assert_public_url,
    ip_is_public,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918 private
        "192.168.1.1",  # RFC1918 private
        "169.254.169.254",  # link-local — cloud metadata endpoint
        "::1",  # IPv6 loopback
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
    ],
)
def test_ip_is_public_blocks_non_routable(ip: str) -> None:
    assert ip_is_public(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34"])
def test_ip_is_public_allows_globally_routable(ip: str) -> None:
    assert ip_is_public(ip) is True


def _resolves_to(*ips: str) -> Resolver:
    """A fake resolver that returns a fixed set of IPs for any host."""

    async def _resolver(_host: str) -> list[str]:
        return list(ips)

    return _resolver


async def _never_resolve(host: str) -> list[str]:
    """A resolver that must never be called (used to prove a literal IP skips DNS)."""
    raise AssertionError(f"resolver must not be called for literal-IP host {host!r}")


async def test_assert_public_url_allows_a_public_host() -> None:
    # Returns None (no exception) when every resolved address is public.
    await assert_public_url("https://consumer.test/hook", resolver=_resolves_to("93.184.216.34"))


async def test_assert_public_url_blocks_a_host_resolving_to_a_private_ip() -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url("https://sneaky.test/hook", resolver=_resolves_to("10.0.0.1"))


async def test_assert_public_url_blocks_if_any_resolved_ip_is_private() -> None:
    # One public + one private address is still blocked (fail-closed on the weakest link).
    with pytest.raises(BlockedUrlError):
        await assert_public_url(
            "https://mixed.test/hook", resolver=_resolves_to("93.184.216.34", "127.0.0.1")
        )


async def test_assert_public_url_blocks_a_literal_internal_ip_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url("http://127.0.0.1/x", resolver=_never_resolve)


async def test_assert_public_url_blocks_link_local_metadata_literal_without_dns() -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url("http://169.254.169.254/meta", resolver=_never_resolve)


async def test_assert_public_url_allows_a_public_literal_ip_without_dns() -> None:
    await assert_public_url("http://8.8.8.8/x", resolver=_never_resolve)


@pytest.mark.parametrize("url", ["ftp://consumer.test/hook", "file:///etc/passwd"])
async def test_assert_public_url_blocks_non_http_scheme(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url(url, resolver=_resolves_to("93.184.216.34"))


async def test_assert_public_url_blocks_a_url_with_no_host() -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url("https:///no-host", resolver=_resolves_to("93.184.216.34"))


async def test_assert_public_url_blocks_empty_resolution() -> None:
    with pytest.raises(BlockedUrlError):
        await assert_public_url("https://void.test/hook", resolver=_resolves_to())


async def test_assert_public_url_blocks_a_failed_resolution() -> None:
    async def _boom(_host: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(BlockedUrlError):
        await assert_public_url("https://broken.test/hook", resolver=_boom)
