"""The operator's private-target allowlist for outbound webhooks (RF-17 / RNF-5 / RNF-9).

.. rubric:: The problem this exists to solve

The SSRF egress guard (:mod:`aethercal.server.webhooks.ssrf`) refuses every target that is not
globally routable. That is exactly right against Server-Side Request Forgery — and it makes the
**primary use case of a self-hostable product impossible**. A self-hoster whose n8n, CRM or ERP runs
on the same Docker network, the same LAN, or the same VPN gets **nothing**: every delivery to
``http://n8n:5678/webhook/...`` is parked ``dead``, and nobody is told why. "Connect AetherCal to
your n8n" was, until this module, a sentence that could not be executed.

.. rubric:: Why an allowlist is not a hole

The whole security argument rests on **one** property, and it is the reason this module exists at
all rather than a ``webhooks.allow_private = true`` flag:

    ==The allowlist comes from the ENVIRONMENT. Never from inbound data, never from the database,
    never from an API request.==

A webhook URL is caller-supplied — anyone who can create a subscription chooses it. The *networks
those URLs may reach* are operator-supplied, and an attacker who controls a URL **cannot widen the
list**. That asymmetry is the entire difference between a legitimate feature and an SSRF hole, and
nothing in this module reads anything but a string handed to it by the process edge.

Four rules hold the rest of it up:

* **fail-closed.** Unconfigured = the empty allowlist = today's behaviour, exactly. Upgrading to a
  build that *can* reach private networks must not, by itself, reach any;
* **explicit CIDRs, never a boolean.** ``allow_private_targets = true`` is a knob somebody copies
  out of a forum post without reading the sentence after it.
  ``AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS=192.168.1.0/24`` is a statement about a specific network
  the operator had to go and look up;
* **fail-loud.** Every misconfiguration raises at BOOT, naming the variable and the token. The one
  outcome this project refuses is the third state — "configured, and quietly doing nothing";
* **the pin still holds.** The allowlist decides which addresses are *reachable*; it does not relax
  the connect-time IP pin. A permitted network never becomes a pivot — see
  :mod:`aethercal.server.webhooks.pinning`.

.. rubric:: What can never be allowlisted, no matter what the operator writes

A declared CIDR must be a subnet of a known non-global parent (RFC1918, CGNAT/Tailscale, IPv6 ULA,
or loopback). Everything else is refused at boot:

* ``0.0.0.0/0`` / ``::/0`` — the default route contains loopback, the metadata address and every
  private range at once. It is what a copied tutorial reaches for, and it is the single entry that
  would turn this feature into the vulnerability it is designed to avoid. It is not "discouraged";
  it is unrepresentable;
* **link-local** (``169.254.0.0/16``, ``fe80::/10``) — ``169.254.169.254`` is the cloud metadata
  endpoint: the host's credentials, one GET away. No webhook consumer has ever lived there;
* **public space** — a public CIDR here is either a misunderstanding (public targets already work)
  or an attempt to launder something past the guard;
* **multicast**, and anything else that is not somebody's private network.

Loopback (``127.0.0.0/8``) *is* declarable — a bare-metal box running AetherCal and n8n side by side
is a real deployment, and refusing it would push that operator to a worse workaround. But it is the
widest blast radius they can legitimately choose (every service that binds to localhost *because it
trusts localhost* becomes reachable from a caller-supplied URL), so
:func:`warn_if_loopback_is_allowlisted` says so at boot. A decision, never a default.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

ALLOWLIST_ENV_VAR = "AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS"
"""The ONE source of this list. Named here so every error message can point the operator at it."""

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_ALLOWLISTABLE_PARENTS: tuple[IpNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918 — includes the Docker bridge networks
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),  # RFC6598 shared/CGNAT — where Tailscale peers live
    ipaddress.ip_network("127.0.0.0/8"),  # loopback — allowed, but never quietly (see below)
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local (ULA)
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
)
"""A declared CIDR must live inside one of these. Anything else is refused at boot.

An allowlist-of-parents rather than a denylist-of-dangers on purpose: a denylist is a list of the
mistakes somebody has already made. This is the set of places a self-hoster's own services actually
run — and it makes ``0.0.0.0/0``, link-local and public space *unrepresentable* rather than merely
discouraged."""

_LOOPBACK_PARENTS: tuple[IpNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)

_EXAMPLE = "192.168.1.0/24,172.17.0.0/16"


class AllowlistConfigError(ValueError):
    """The allowlist is misconfigured. Raised at BOOT — never swallowed into "nothing allowed".

    ==The failure mode this class exists to prevent is the silent one.== An operator who mistypes a
    CIDR and gets an empty allowlist sees precisely what they saw before: every delivery to their
    LAN parked ``dead``, no error, nothing in the log. They would then conclude the feature does not
    work — which, for them, would be true."""


@dataclass(frozen=True, slots=True)
class PrivateTargetAllowlist:
    """The private networks the OPERATOR has declared this instance may POST webhooks to.

    Immutable, and built exactly once at boot from :data:`ALLOWLIST_ENV_VAR`. It is passed *down*
    into the delivery worker as an argument — the worker never reaches out to fetch it, and there is
    no code path by which a request, a row or a response can add a network to it.
    """

    networks: tuple[IpNetwork, ...] = ()

    @property
    def is_empty(self) -> bool:
        """No network declared: nothing private is reachable. The default, and the safe one."""
        return not self.networks

    @classmethod
    def parse(cls, raw: str | None) -> PrivateTargetAllowlist:
        """Build from the raw environment value. Fail-closed when absent, fail-LOUD when wrong.

        ``None`` / blank → the empty allowlist (today's behaviour, unchanged). Anything else must be
        a comma-separated list of explicit CIDRs, each inside :data:`_ALLOWLISTABLE_PARENTS`; a
        single bad token raises :class:`AllowlistConfigError` and the process does not come up.
        """
        if raw is None or not raw.strip():
            return NO_PRIVATE_TARGETS

        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        if not tokens:
            # They typed SOMETHING (",," or similar). Reading a mistake as an intention — "off" —
            # is how a misconfiguration becomes a silent no-op instead of an error.
            raise AllowlistConfigError(
                f"{ALLOWLIST_ENV_VAR}={raw!r} contains no CIDR at all. Set it to a comma-separated "
                f"list (e.g. {_EXAMPLE}), or leave it unset to allow no private targets."
            )
        return cls(networks=tuple(_parse_cidr(token) for token in tokens))

    def permits(self, ip: str) -> bool:
        """Is ``ip`` inside one of the declared networks?

        A malformed address is a plain ``False``, never an exception: the value reaching here comes
        from a DNS resolver, and a garbage answer must be a refusal, not a crash inside the worker.

        This answers only "did the operator declare it?". It is never the whole decision — see
        :func:`aethercal.server.webhooks.ssrf.target_is_allowed`, which is what the guard calls.
        """
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in self.networks)


NO_PRIVATE_TARGETS = PrivateTargetAllowlist()
"""The empty allowlist: no private target is reachable. ==The default, and the fail-closed one.=="""


def _parse_cidr(token: str) -> IpNetwork:
    """Parse ONE declared CIDR, or raise :class:`AllowlistConfigError` saying exactly why not."""
    if "/" not in token:
        # ``10.0.0.0`` parses cleanly — as ``10.0.0.0/32``, a single address that is almost
        # certainly not the one they meant. The operator would believe they had opened their LAN and
        # would have opened one unused host: every delivery still dead, and now with a "configured"
        # allowlist to prove it should have worked. The silent no-op, dressed as a fix.
        raise AllowlistConfigError(
            f"{ALLOWLIST_ENV_VAR}: {token!r} has no prefix length. Write the network explicitly — "
            f"{token}/24, {token}/16, ... — because {token!r} alone means {token}/32 (that ONE "
            f"address), which is almost never what was meant. Example: "
            f"{ALLOWLIST_ENV_VAR}={_EXAMPLE}"
        )

    try:
        # strict=True: 192.168.1.5/24 is a typo for 192.168.1.0/24, and guessing which of the two
        # they meant is not a decision this module gets to make silently.
        network = ipaddress.ip_network(token, strict=True)
    except ValueError as exc:
        raise AllowlistConfigError(
            f"{ALLOWLIST_ENV_VAR}: {token!r} is not a valid CIDR ({exc}). Host bits must be zero "
            f"(192.168.1.0/24, not 192.168.1.5/24). Example: {ALLOWLIST_ENV_VAR}={_EXAMPLE}"
        ) from exc

    if not any(_is_subnet_of(network, parent) for parent in _ALLOWLISTABLE_PARENTS):
        raise AllowlistConfigError(
            f"{ALLOWLIST_ENV_VAR}: {token!r} is not a private network this instance may be told to "
            "reach. Only subnets of the ranges a self-hoster's own services actually run in can be "
            "declared: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 (CGNAT/Tailscale), "
            "127.0.0.0/8 (loopback), fc00::/7 (IPv6 ULA), ::1/128.\n"
            "\n"
            "Refused on purpose, and NOT negotiable:\n"
            "  0.0.0.0/0 and ::/0        — the default route holds loopback, the cloud-metadata "
            "address and every private range at once. This is the entry that turns the feature "
            "into the vulnerability it exists to avoid.\n"
            "  169.254.0.0/16, fe80::/10 — link-local. 169.254.169.254 is the cloud metadata "
            "endpoint: the host's own credentials, one request away.\n"
            "  public CIDRs              — public targets already work; they need no allowlist.\n"
            "\n"
            f"Example: {ALLOWLIST_ENV_VAR}={_EXAMPLE}"
        )
    return network


def _is_subnet_of(network: IpNetwork, parent: IpNetwork) -> bool:
    """``network ⊆ parent``, tolerating a version mismatch (v4 is never a subnet of a v6 parent)."""
    if isinstance(network, ipaddress.IPv4Network) and isinstance(parent, ipaddress.IPv4Network):
        return network.subnet_of(parent)
    if isinstance(network, ipaddress.IPv6Network) and isinstance(parent, ipaddress.IPv6Network):
        return network.subnet_of(parent)
    return False


def warn_if_loopback_is_allowlisted(allowlist: PrivateTargetAllowlist) -> None:
    """Say, at boot, that loopback is now reachable from a caller-supplied webhook URL.

    It is a legitimate choice — AetherCal and n8n on one bare-metal box is a real deployment — and
    it is the widest one available. Everything that binds to ``127.0.0.1`` *because it considers
    localhost trusted* (an unauthenticated admin port, a local Redis, this app's own ``/admin``) is
    now reachable by anyone who can register a subscription. The operator may still want it. They
    may not have it by accident, and they may not have it in silence."""
    loopback = [
        network
        for network in allowlist.networks
        if any(_is_subnet_of(network, parent) for parent in _LOOPBACK_PARENTS)
    ]
    if not loopback:
        return
    _logger.warning(
        "%s allows LOOPBACK (%s): a webhook subscription — whose URL is chosen by whoever creates "
        "it — can now reach services bound to localhost on this host, including any that treat "
        "localhost as trusted. This is a valid choice for a single-box self-host; it is not a safe "
        "default. Narrow it to the exact service you meant, or remove it if the target is on the "
        "LAN/Docker network instead.",
        ALLOWLIST_ENV_VAR,
        ", ".join(str(network) for network in loopback),
    )


__all__ = [
    "ALLOWLIST_ENV_VAR",
    "NO_PRIVATE_TARGETS",
    "AllowlistConfigError",
    "IpNetwork",
    "PrivateTargetAllowlist",
    "warn_if_loopback_is_allowlisted",
]
