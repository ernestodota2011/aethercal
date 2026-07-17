"""The operator's private-target allowlist: parsing, fail-closed defaults, fail-loud misconfig.

The whole security argument of this feature rests on ONE property, and these tests are what hold it
down: **the allowlist comes from the environment and from nowhere else.** A webhook URL is
caller-supplied; the *networks those URLs are allowed to reach* are operator-supplied. Nothing in
this module reads a request, a database row, or an API response.

The rest is fail-closed (unset = the current behaviour, nothing private is reachable) and fail-loud
(a misconfigured allowlist raises at boot rather than silently permitting nothing — or worse,
everything).
"""

from __future__ import annotations

import ipaddress
import logging

import pytest

from aethercal.server.webhooks.allowlist import (
    ALLOWLIST_ENV_VAR,
    NO_PRIVATE_TARGETS,
    AllowlistConfigError,
    PrivateTargetAllowlist,
    warn_if_loopback_is_allowlisted,
)

# --------------------------------------------------------------------------------------
# Fail-closed: no configuration = the behaviour that shipped. Nobody opens a hole by upgrading.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "\n"])
def test_an_unconfigured_allowlist_is_empty_and_permits_nothing(raw: str | None) -> None:
    allowlist = PrivateTargetAllowlist.parse(raw)
    assert allowlist == NO_PRIVATE_TARGETS
    assert allowlist.is_empty
    for ip in ("10.0.0.5", "192.168.1.10", "127.0.0.1", "169.254.169.254", "172.17.0.2"):
        assert allowlist.permits(ip) is False


def test_the_empty_allowlist_permits_nothing_at_all() -> None:
    assert NO_PRIVATE_TARGETS.is_empty
    assert NO_PRIVATE_TARGETS.permits("10.0.0.1") is False


# --------------------------------------------------------------------------------------
# Configured: explicit CIDRs, and only the addresses inside them.
# --------------------------------------------------------------------------------------


def test_a_configured_cidr_permits_addresses_inside_it_and_only_those() -> None:
    allowlist = PrivateTargetAllowlist.parse("192.168.1.0/24")
    assert allowlist.permits("192.168.1.50") is True
    assert allowlist.permits("192.168.1.255") is True
    # One subnet over is a different network the operator did NOT declare.
    assert allowlist.permits("192.168.2.50") is False
    assert allowlist.permits("10.0.0.5") is False
    assert allowlist.permits("127.0.0.1") is False


def test_several_cidrs_are_comma_separated_and_whitespace_tolerant() -> None:
    allowlist = PrivateTargetAllowlist.parse(" 10.0.0.0/8 , 172.17.0.0/16 ,192.168.0.0/16 ")
    assert len(allowlist.networks) == 3
    assert allowlist.permits("10.9.9.9") is True
    assert allowlist.permits("172.17.0.2") is True  # the Docker bridge — the canonical self-host
    assert allowlist.permits("192.168.4.4") is True
    assert allowlist.permits("100.64.0.1") is False  # CGNAT was not declared


def test_ipv6_unique_local_addresses_are_allowlistable() -> None:
    allowlist = PrivateTargetAllowlist.parse("fd00::/8")
    assert allowlist.permits("fd00::1") is True
    assert allowlist.permits("2606:4700::1111") is False  # a public v6 address is not "permitted"
    # ...and an address of the other family is simply not in this network.
    assert allowlist.permits("10.0.0.1") is False


def test_a_tailscale_cgnat_range_is_allowlistable() -> None:
    # 100.64.0.0/10 is shared/CGNAT space — ip_is_public() rejects it, and it is exactly where a
    # self-hoster's Tailscale peers live. It must be declarable.
    allowlist = PrivateTargetAllowlist.parse("100.64.0.0/10")
    assert allowlist.permits("100.101.102.103") is True


# --------------------------------------------------------------------------------------
# Fail-loud: every misconfiguration raises at BOOT, naming the variable and the offending token.
# --------------------------------------------------------------------------------------


def test_a_cidr_without_an_explicit_prefix_is_refused() -> None:
    """``10.0.0.0`` is not "the 10 network" — Python reads it as ``10.0.0.0/32``.

    An operator who writes it believes they opened their LAN and has in fact opened a single unused
    address: every delivery still fails, and nothing says why. That is the silent no-op wearing the
    costume of a fix, so it is a boot error instead."""
    with pytest.raises(AllowlistConfigError) as excinfo:
        PrivateTargetAllowlist.parse("10.0.0.0")
    assert "10.0.0.0" in str(excinfo.value)
    assert ALLOWLIST_ENV_VAR in str(excinfo.value)


def test_a_cidr_with_host_bits_set_is_refused() -> None:
    # 192.168.1.5/24 is a typo for 192.168.1.0/24. Guessing which one they meant is not our job.
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse("192.168.1.5/24")


def test_garbage_is_refused() -> None:
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse("not-a-cidr")


def test_a_value_that_parses_to_no_networks_at_all_is_refused() -> None:
    # The operator typed SOMETHING. Reading it as "off" would be reading a mistake as an intention.
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse(",,")


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_the_default_route_can_never_be_allowlisted(cidr: str) -> None:
    """==The one entry that would turn this feature into the SSRF hole it is designed to avoid.==

    ``0.0.0.0/0`` contains loopback, the cloud-metadata address, and every private range at once. It
    is what a copied tutorial reaches for, and it is structurally impossible to configure."""
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse(cidr)


@pytest.mark.parametrize("cidr", ["169.254.0.0/16", "169.254.169.254/32", "fe80::/10"])
def test_link_local_can_never_be_allowlisted(cidr: str) -> None:
    """``169.254.169.254`` is the cloud metadata endpoint — the credentials of the host, one GET
    away. No webhook consumer has ever lived on link-local, so there is no legitimate reason to
    declare it and one catastrophic reason not to."""
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse(cidr)


@pytest.mark.parametrize("cidr", ["8.8.8.0/24", "93.184.216.0/24", "2606:4700::/32"])
def test_public_space_can_never_be_allowlisted(cidr: str) -> None:
    """This list declares PRIVATE targets. A public CIDR in it is either a misunderstanding (public
    targets already work) or an attempt to launder something past the guard — and a broad public
    CIDR would also swallow addresses the operator never looked at. Refused, with an explanation."""
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse(cidr)


@pytest.mark.parametrize("cidr", ["224.0.0.0/4", "ff00::/8"])
def test_multicast_can_never_be_allowlisted(cidr: str) -> None:
    with pytest.raises(AllowlistConfigError):
        PrivateTargetAllowlist.parse(cidr)


def test_the_error_names_the_variable_and_shows_a_working_example() -> None:
    """A boot error an operator cannot act on just becomes a StackOverflow search."""
    with pytest.raises(AllowlistConfigError) as excinfo:
        PrivateTargetAllowlist.parse("0.0.0.0/0")
    message = str(excinfo.value)
    assert ALLOWLIST_ENV_VAR in message
    assert "192.168" in message or "10.0.0.0/8" in message


# --------------------------------------------------------------------------------------
# Loopback: declarable, but never quietly.
# --------------------------------------------------------------------------------------


def test_loopback_is_declarable_because_a_single_box_self_host_is_real() -> None:
    # AetherCal and n8n on the same bare-metal host: the target genuinely is 127.0.0.1:5678.
    allowlist = PrivateTargetAllowlist.parse("127.0.0.0/8")
    assert allowlist.permits("127.0.0.1") is True


def test_allowlisting_loopback_warns_loudly_at_boot(caplog: pytest.LogCaptureFixture) -> None:
    """It is the widest blast radius an operator can legitimately choose: every service on the box
    that binds to localhost *because it considers localhost trusted* becomes reachable from a
    caller-supplied webhook URL. They may still choose it — but not without being told."""
    allowlist = PrivateTargetAllowlist.parse("127.0.0.0/8,10.0.0.0/8")
    with caplog.at_level(logging.WARNING):
        warn_if_loopback_is_allowlisted(allowlist)
    assert "127.0.0.0/8" in caplog.text
    assert "loopback" in caplog.text.lower()


def test_a_non_loopback_allowlist_warns_about_nothing(caplog: pytest.LogCaptureFixture) -> None:
    allowlist = PrivateTargetAllowlist.parse("10.0.0.0/8")
    with caplog.at_level(logging.WARNING):
        warn_if_loopback_is_allowlisted(allowlist)
    assert caplog.text == ""


# --------------------------------------------------------------------------------------
# Shape.
# --------------------------------------------------------------------------------------


def test_the_allowlist_is_immutable_and_holds_real_network_objects() -> None:
    allowlist = PrivateTargetAllowlist.parse("10.0.0.0/8")
    assert isinstance(allowlist.networks, tuple)
    assert allowlist.networks[0] == ipaddress.ip_network("10.0.0.0/8")
    with pytest.raises((AttributeError, TypeError)):
        allowlist.networks = ()  # type: ignore[misc]  # frozen dataclass


def test_permits_rejects_a_malformed_address_rather_than_raising() -> None:
    # The value reaching permits() comes from a resolver; a garbage answer must be a "no", never a
    # 500 inside the delivery worker.
    allowlist = PrivateTargetAllowlist.parse("10.0.0.0/8")
    assert allowlist.permits("not-an-ip") is False
