"""SSRF egress-guard tests: address classification, URL admission, and the operator's allowlist.

Fully offline and hermetic — ``ip_is_public`` is pure, and ``assert_target_allowed`` takes an
injected fake resolver so no test ever touches real DNS. A literal-IP URL must be judged without
calling the resolver at all (proven by injecting a resolver that raises if used).

Two axes are asserted here, and keeping them apart is the whole point:

* **the policy** — is this address reachable at all? Public always; private only when the OPERATOR
  declared its network in the allowlist (RF-17 / RNF-5);
* **the reason** — when it is not, WHICH refusal was it? A policy block is terminal and must say
  ``blocked-private-target``; a DNS failure is a network failure and must NOT be terminal at all.
  Collapsing the two is what parked a self-hoster's every delivery in ``dead`` with no explanation.
"""

from __future__ import annotations

import pytest

from aethercal.server.webhooks.allowlist import NO_PRIVATE_TARGETS, PrivateTargetAllowlist
from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    BlockReason,
    Resolver,
    TargetUnresolvable,
    assert_target_allowed,
    ip_is_public,
    target_is_allowed,
)

LAN = PrivateTargetAllowlist.parse("192.168.1.0/24,172.17.0.0/16")


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
        "100.64.0.1",  # RFC6598 CGNAT / shared address space — not globally routable
        "192.0.2.1",  # RFC5737 documentation (TEST-NET-1)
        "198.18.0.1",  # RFC2544 benchmarking
    ],
)
def test_ip_is_public_blocks_non_routable(ip: str) -> None:
    assert ip_is_public(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34"])
def test_ip_is_public_allows_globally_routable(ip: str) -> None:
    assert ip_is_public(ip) is True


def test_target_is_allowed_is_ip_is_public_plus_the_operators_allowlist() -> None:
    # Public is always allowed; private is allowed ONLY where the operator declared it.
    assert target_is_allowed("93.184.216.34", allowlist=NO_PRIVATE_TARGETS) is True
    assert target_is_allowed("192.168.1.50", allowlist=NO_PRIVATE_TARGETS) is False
    assert target_is_allowed("192.168.1.50", allowlist=LAN) is True
    assert target_is_allowed("192.168.2.50", allowlist=LAN) is False
    # The allowlist cannot resurrect what ip_is_public rejects and the operator never declared.
    assert target_is_allowed("169.254.169.254", allowlist=LAN) is False


def _resolves_to(*ips: str) -> Resolver:
    """A fake resolver that returns a fixed set of IPs for any host."""

    async def _resolver(_host: str) -> list[str]:
        return list(ips)

    return _resolver


async def _never_resolve(host: str) -> list[str]:
    """A resolver that must never be called (used to prove a literal IP skips DNS)."""
    raise AssertionError(f"resolver must not be called for literal-IP host {host!r}")


# --------------------------------------------------------------------------------------
# Public targets — unchanged behaviour, with or without an allowlist configured.
# --------------------------------------------------------------------------------------


async def test_a_public_host_is_allowed_and_its_addresses_are_returned() -> None:
    validated = await assert_target_allowed(
        "https://consumer.test/hook",
        resolver=_resolves_to("93.184.216.34"),
        allowlist=NO_PRIVATE_TARGETS,
    )
    # The guard returns exactly what it validated, so the pin dials one of THOSE and nothing else.
    assert validated == frozenset({"93.184.216.34"})


async def test_a_public_literal_ip_is_allowed_without_dns() -> None:
    validated = await assert_target_allowed(
        "http://8.8.8.8/x", resolver=_never_resolve, allowlist=NO_PRIVATE_TARGETS
    )
    assert validated == frozenset({"8.8.8.8"})


# --------------------------------------------------------------------------------------
# Fail-closed: with NO allowlist, a private target is refused exactly as it is today.
# --------------------------------------------------------------------------------------


async def test_without_an_allowlist_a_host_resolving_to_a_private_ip_is_blocked() -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "https://sneaky.test/hook",
            resolver=_resolves_to("10.0.0.1"),
            allowlist=NO_PRIVATE_TARGETS,
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET


async def test_without_an_allowlist_a_private_literal_ip_is_blocked_without_dns() -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "http://127.0.0.1/x", resolver=_never_resolve, allowlist=NO_PRIVATE_TARGETS
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET


async def test_the_cloud_metadata_literal_is_blocked_without_dns() -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "http://169.254.169.254/meta", resolver=_never_resolve, allowlist=NO_PRIVATE_TARGETS
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET


async def test_any_private_ip_among_the_resolved_set_blocks_the_whole_target() -> None:
    # One public + one private address is still blocked (fail-closed on the weakest link).
    with pytest.raises(BlockedUrlError):
        await assert_target_allowed(
            "https://mixed.test/hook",
            resolver=_resolves_to("93.184.216.34", "127.0.0.1"),
            allowlist=NO_PRIVATE_TARGETS,
        )


# --------------------------------------------------------------------------------------
# With an allowlist: the self-hoster's own network becomes reachable — and NOTHING else does.
# --------------------------------------------------------------------------------------


async def test_a_declared_private_network_is_allowed() -> None:
    # "Connect AetherCal to your n8n" — the whole point of a self-hostable product.
    validated = await assert_target_allowed(
        "http://n8n.internal:5678/webhook/x", resolver=_resolves_to("172.17.0.5"), allowlist=LAN
    )
    assert validated == frozenset({"172.17.0.5"})


async def test_a_declared_private_literal_ip_is_allowed_without_dns() -> None:
    validated = await assert_target_allowed(
        "http://192.168.1.50:5678/hook", resolver=_never_resolve, allowlist=LAN
    )
    assert validated == frozenset({"192.168.1.50"})


async def test_a_private_target_outside_the_declared_cidr_is_blocked_with_its_reason() -> None:
    # The allowlist is a declaration about specific networks, not about "private" as a category.
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "http://10.0.0.9:5678/hook", resolver=_never_resolve, allowlist=LAN
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET
    assert excinfo.value.reason.value == "blocked-private-target"  # the greppable token
    assert "10.0.0.9" in str(excinfo.value)


async def test_an_allowlist_never_opens_link_local_or_metadata() -> None:
    # 169.254.0.0/16 cannot even BE declared (see test_webhooks_allowlist), so a target there stays
    # blocked no matter what the operator configured.
    with pytest.raises(BlockedUrlError):
        await assert_target_allowed(
            "http://169.254.169.254/meta", resolver=_never_resolve, allowlist=LAN
        )


# --------------------------------------------------------------------------------------
# Terminal vs retryable — a refusal by POLICY is not the same event as a refusal by DNS.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://consumer.test/hook", "file:///etc/passwd"])
async def test_a_non_http_scheme_is_blocked(url: str) -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            url, resolver=_resolves_to("93.184.216.34"), allowlist=NO_PRIVATE_TARGETS
        )
    assert excinfo.value.reason is BlockReason.BAD_SCHEME


async def test_a_url_with_no_host_is_blocked() -> None:
    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "https:///no-host", resolver=_resolves_to("93.184.216.34"), allowlist=NO_PRIVATE_TARGETS
        )
    assert excinfo.value.reason is BlockReason.NO_HOST


async def test_an_empty_resolution_is_unresolvable_not_blocked() -> None:
    """==A name that does not resolve is a NETWORK failure, not a policy decision.==

    It used to raise the same ``BlockedUrlError`` as a metadata-address target, and the delivery
    worker parks a blocked target ``dead`` with no retry. So one DNS hiccup during one tick killed
    a legitimate subscriber's delivery permanently — and silently. The type is the fix: this one is
    retryable, and the worker treats it like any other transient failure."""
    with pytest.raises(TargetUnresolvable):
        await assert_target_allowed(
            "https://void.test/hook", resolver=_resolves_to(), allowlist=NO_PRIVATE_TARGETS
        )


async def test_a_failed_resolution_is_unresolvable_not_blocked() -> None:
    async def _boom(_host: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(TargetUnresolvable):
        await assert_target_allowed(
            "https://broken.test/hook", resolver=_boom, allowlist=NO_PRIVATE_TARGETS
        )


async def test_unresolvable_is_not_a_blocked_url_error() -> None:
    """The distinction has to survive an ``except`` clause, or it does not exist."""
    assert not issubclass(TargetUnresolvable, BlockedUrlError)
    assert not issubclass(BlockedUrlError, TargetUnresolvable)


async def test_a_resolver_that_raises_blocked_url_error_keeps_its_reason() -> None:
    # The resolver seam must not swallow a real policy refusal into a generic "cannot resolve".
    async def _blocking(_host: str) -> list[str]:
        raise BlockedUrlError(BlockReason.PRIVATE_TARGET, "nope")

    with pytest.raises(BlockedUrlError) as excinfo:
        await assert_target_allowed(
            "https://x.test/hook", resolver=_blocking, allowlist=NO_PRIVATE_TARGETS
        )
    assert excinfo.value.reason is BlockReason.PRIVATE_TARGET
