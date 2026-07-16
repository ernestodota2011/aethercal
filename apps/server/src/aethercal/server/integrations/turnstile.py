"""Cloudflare Turnstile — ==the PRIMARY defence of a write endpoint with no authentication.==

The caps around the public router are ceilings, not gates. A per-email cap is beaten by an alias; a
per-IP cap is beaten by a proxy pool. Neither makes an attempt *cost* anything. The captcha does,
and
that is why it is the one control this cut refuses to ship without: ``Settings`` will not build with
the public API enabled and no secret configured, so the process does not come up (criterion 14).

.. rubric:: Fail-closed verification, and the bypass it refuses to be

Every answer that is not an explicit ``{"success": true}`` is a **no**: a rejected token, a 5xx from
Cloudflare, a body that is not the documented envelope, a connection that never landed. The
alternative — treating an error as a pass, so that "the guard is down" does not mean "bookings are
down" — hands an attacker the bypass for free: break the verifier, or simply wait for Cloudflare to
have a bad minute, and the only real gate in front of an unauthenticated write evaporates. Exactly
when it is under load, and in silence.

The trade is stated rather than smuggled: with Cloudflare unreachable, PUBLIC bookings stop. The
authenticated API, the admin, and the guest's own cancel/reschedule links keep working — the outage
is confined to the one surface whose safety depends on the thing that is down.

.. rubric:: A client per call

The verifier opens its own short-lived ``httpx.AsyncClient`` per verification rather than sharing
the
app's. A booking POST is not a hot path, and the alternative — reaching into ``app.state`` for a
client that only exists once the lifespan has run — would make the verifier behave differently under
a test transport (which never runs the lifespan) from the way it behaves in production. A verifier
that is quietly different under test is not a verifier. ``transport`` is the seam the tests inject.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

_logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
HTTP_OK = 200
DEFAULT_TIMEOUT_SECONDS = 10.0

TURNSTILE_SECRET_ENV = "AETHERCAL_TURNSTILE_SECRET"

#: Cloudflare's documented ALWAYS-PASSES test pair. Named here so a dev/CI environment is configured
#: with a real, working captcha instead of with a bypass flag — a bypass flag is the thing that
#: eventually finds its way into a production ``.env``.
TEST_SECRET_ALWAYS_PASSES = "1x0000000000000000000000000000000AA"
TEST_SITE_KEY_ALWAYS_PASSES = "1x00000000000000000000AA"


class TurnstileVerifier(Protocol):
    """Anything that can answer "did a human do this?" — the seam the public endpoint depends on."""

    async def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        """``True`` only for a token Cloudflare explicitly accepts; every other answer is
        ``False``."""
        ...


class CloudflareTurnstile:
    """The real verifier: a POST to ``siteverify``, and a ``False`` for everything else."""

    def __init__(
        self,
        *,
        secret: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._secret = secret
        self._timeout = timeout
        self._transport = transport

    async def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        """Verify a widget response against Cloudflare. FAIL-CLOSED on anything that is not a
        pass."""
        if token is None or not token.strip():
            # No round-trip for a request carrying no token: there is nothing to verify, and a bot
            # must not be able to spend one of our network calls simply by omitting a field.
            return False

        form = {"secret": self._secret, "response": token}
        if remote_ip is not None:
            form["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(SITEVERIFY_URL, data=form)
        except httpx.HTTPError:
            _logger.exception(
                "turnstile: the verification call to Cloudflare failed. The booking is REFUSED "
                "(fail-closed) — with the captcha unavailable, an unauthenticated write "
                "endpoint has "
                "no gate at all, and a pass-on-error is the bypass an attacker waits for."
            )
            return False

        return _passed(response)


def _passed(response: httpx.Response) -> bool:
    """Read Cloudflare's answer. ==Only an explicit ``{"success": true}`` is a pass.==

    A separate function, and not for tidiness: every arm here is a distinct way the verification can
    fail to be a YES — a non-200, a body that is not JSON, a body that is not the documented
    envelope, and an explicit refusal — and each has to answer ``False`` on its own. Folding them
    together to satisfy a return-count lint is exactly how one of them would eventually come to mean
    "well, probably fine".
    """
    if response.status_code != HTTP_OK:
        _logger.error(
            "turnstile: Cloudflare answered %s. The booking is REFUSED (fail-closed).",
            response.status_code,
        )
        return False

    try:
        payload = response.json()
    except ValueError:
        _logger.error("turnstile: Cloudflare's answer was not JSON. REFUSED (fail-closed).")
        return False

    if not isinstance(payload, dict):
        return False
    if payload.get("success") is not True:
        _logger.info("turnstile: challenge not passed (%s)", payload.get("error-codes"))
        return False
    return True


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SITEVERIFY_URL",
    "TEST_SECRET_ALWAYS_PASSES",
    "TEST_SITE_KEY_ALWAYS_PASSES",
    "TURNSTILE_SECRET_ENV",
    "CloudflareTurnstile",
    "TurnstileVerifier",
]
