"""==No test may reach the real network.== A repo-wide, fail-closed pytest plugin.

The sibling of the db gate in ``conftest.py``, aimed at the other way a suite can pass while proving
nothing — or worse, while spending real money. Registered from the root ``conftest.py`` via
``pytest_plugins``, so it applies to every test in the tree and a test can import
:class:`RealNetworkForbiddenError` by a name that does not collide with the several ``conftest``
modules this repo has.
"""

from __future__ import annotations

import errno
import ipaddress
import socket
from typing import Any, NoReturn

import aiosmtplib
import httplib2
import httpx
import pytest
import respx.mocks

respx.mocks.DEFAULT_MOCKER = "httpx"
"""==Tell ``respx`` to mock one storey UP, so it and the guard are not standing on the same step.==

``respx`` mocks by default at ``httpcore`` — *below* ``httpx``'s transport, which is where the guard
must live. Two libraries patching adjacent rungs of the same ladder is a coin-flip decided by import
order: the first attempt at this guard answered before respx's mocks and turned 58 green tests red.

``"httpx"`` is a first-class, documented respx mode: it patches ``AsyncClient._transport_for_url``
and hands back its own mock transport. That is strictly ABOVE this guard, so a respx test never
reaches the door at all — while anything respx is NOT mocking still walks straight into it. Set
once, at import, before any test builds a client.
"""


class RealNetworkForbiddenError(RuntimeError):
    """A test tried to reach the REAL outside world. ==It never gets to.==

    Raised in place of the socket, so the failure is a red test naming this module rather than a
    request — or an email — leaving the machine.
    """


def _is_loopback(address: Any) -> bool:
    """Whether ``address`` is this machine talking to itself. ==The ONLY thing let through.==

    The rule is one sentence — *a test may talk to itself, and to nothing else* — and it is a rule,
    not an allow-list: there is no host to keep current, and a service nobody has thought of yet is
    covered by it the day it appears.

    A non-network family (a UNIX socket, a socketpair) is not an address tuple at all and is let
    through: it cannot leave the machine by construction. An address whose host will not parse as an
    IP is a HOSTNAME, which means a real resolution was intended — so it is refused. ==Unparseable
    is refused, not excused==: this is the door, and a door that guesses is a hallway.
    """
    if not isinstance(address, tuple) or len(address) < 2:
        # AF_UNIX or a socketpair: no network involved.
        return True
    host = address[0]
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


def _guarded_connect_ex(sock: socket.socket, address: Any) -> int:
    """``connect_ex`` is the OTHER door in the socket, and it was standing open.

    ==Guarding ``connect`` alone was a hole with a well-known name.== ``connect_ex`` does the same
    thing and reports failure as an errno instead of raising, so any caller that prefers it — and
    asyncio's selector loop is one — walked straight past the floor and out into the world.

    .. rubric:: ==It returns an errno; it does NOT raise, and that is not a softening==

    Raising here would be the louder refusal, and it would break the event loop's own plumbing:
    ``loop.sock_connect`` calls ``connect_ex`` and reads the code. A guard that takes the
    interpreter down with it is a guard that gets switched off. ``EACCES`` is what the kernel itself
    returns for a destination policy forbids, so the caller meets a refusal it already handles.

    The socket is left for its owner to close: a ``connect_ex`` caller reads a return code and
    cleans up, unlike the ``connect`` caller below, which may raise straight past its descriptor.

    ==What matters is identical either way: nothing leaves the machine.== The real ``connect_ex`` is
    never reached, so no packet is sent — the refusal happens before the syscall, not after it.
    """
    if _is_loopback(address):
        return _REAL_SOCKET_CONNECT_EX(sock, address)
    return errno.EACCES


def _guarded_live_connect_ex(sock: socket.socket, address: Any) -> int:
    """The same door, for a live-provider test: the allowlist decides, and nothing else."""
    if _is_allowed_live_address(address):
        return _REAL_SOCKET_CONNECT_EX(sock, address)
    return errno.EACCES


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    """Refuse anything that is not this machine talking to itself.

    ==The socket is CLOSED before the refusal, and that detail was earned.== A caller whose
    ``connect`` raises is under no obligation to clean up, and ``urllib`` does not: the file
    descriptor survives until an arbitrary later garbage collection, which then emits a
    ``ResourceWarning`` — and with this repo's ``filterwarnings = ["error"]`` that lands as a hard
    ERROR on whichever unrelated test is running at that moment. It cost one confusing failure in
    ``test_notifications_service`` to find, and a per-test warning filter cannot fix it because the
    GC does not happen during the test that leaked. A refused socket has no future, so closing it is
    both safe and the only way the refusal stays local to the test that caused it.
    """
    if _is_loopback(address):
        return _REAL_SOCKET_CONNECT(sock, address)
    sock.close()
    _forbidden()


_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex


def _forbidden(*_args: object, **_kwargs: object) -> NoReturn:
    raise RealNetworkForbiddenError(
        "a test tried to reach the REAL outside world (HTTP, SMTP or the Google API).\n"
        "\n"
        "Nothing under test is allowed to leave this machine. If you are seeing this, a fake did "
        "not take effect and the REAL adapter was reached — check that the test injected its stub "
        "where the code actually READS it (`httpx.MockTransport`, `respx`, the `EmailSender` "
        "seam, an injected Google service, or the `app.state` key the router looks up), not "
        "merely somewhere adjacent.\n"
        "\n"
        "This is not a lint. Whatever credentials the environment happens to hold, this process "
        "can act on: a payment adapter charges a real account, SMTP writes to a real person's "
        "inbox, and the Google client edits or DELETES an event on a real calendar. A charge can "
        "at least be refunded; an email cannot be unsent.\n"
        "\n"
        "See pytest_network_guard.py."
    )


LIVE_PROVIDER_ALLOWED_DESTINATIONS = frozenset({("api.stripe.com", 443)})
"""==Where a ``live_provider`` test may go. Everything else is refused, marker or no marker.==

The first cut of the live exception simply stopped patching ``httpx`` and the socket floor, which
opened ==arbitrary egress==: any host, any port, for any test carrying the marker. That is a much
larger hole than the one being asked for. What the verification harness actually needs is *one host
on one port*, so that is what it gets.

==Named hosts, not addresses.== The allowlist is the thing a reader can check against the harness's
purpose (*"why does a payment test need `api.stripe.com`?"* answers itself; *"why does it need
`104.18.…`?"* does not). Stripe sits behind a CDN whose addresses rotate, so a pinned IP list would
be wrong within the week; the socket floor resolves these names at connect time instead.

Widening this is a deliberate edit with a reviewer attached — and ``test_live_suite_gate`` pins the
contents, so growing it silently is not one of the available moves.
"""

LIVE_PROVIDER_MARKER = "live_provider"
"""==The ONE marker that may reach a provider's real API, and it is not merely "an exception".==

.. rubric:: Why a hole in a fail-closed guard is the honest answer here

This plugin closes the door because an unverified payment adapter must never reach a real provider
BY ACCIDENT. But the product's money guard
(:func:`~aethercal.server.services.tenant_credentials.live_verifications`) refuses a live credential
until somebody has DELIBERATELY exercised that adapter against the real API — so with no way through
this door at all, the evidence could never be gathered and ==the money guard would be permanent by
construction==: not fail-closed, merely stuck. A guard that can never be discharged is one that gets
deleted in a hurry by whoever needs to ship, which is a worse outcome than the one it prevents.

.. rubric:: What the hole is NOT

* it is not "the guard is off", and it is not even "HTTP is open". SMTP and the Google API stay
  shut — those write to a real person's inbox and to somebody's real calendar, and wanting to talk
  to Stripe is no reason at all to open them. The HTTP doors and the socket floor are not removed
  either: they are ==re-pointed at an ALLOWLIST== of one host on one port
  (:data:`LIVE_PROVIDER_ALLOWED_DESTINATIONS`). A live-marked test that reaches for any other
  destination is refused exactly as an ordinary test would be;
* it is not reachable by inattention, which is the property that actually matters. It takes the
  marker on the test, that marker registered in ``pyproject.toml`` under ``--strict-markers``, the
  provider's key exported into the environment, and (for ``-m live_provider``) the root
  ``conftest.py`` gate agreeing the key is really there. An ordinary run, a forgotten fake, a
  mis-wired fixture — ==the incident this plugin was written for== — carries none of those and still
  walks straight into the door;
* and it is not a licence to spend. The live suite makes zero-cost calls only, and it neuters
  ``StripeGateway.refund`` on itself so the one operation that moves money cannot be reached even by
  mistake. That belt lives with the tests, because it is a fact about them, not about the network.
"""


_REAL_ASYNC_HANDLE = httpx.AsyncHTTPTransport.handle_async_request
_REAL_SYNC_HANDLE = httpx.HTTPTransport.handle_request
"""Captured at IMPORT, before any test can patch them: the only honest way back to the real door."""


def _forbidden_destination(destination: str) -> NoReturn:
    raise RealNetworkForbiddenError(
        f"a live-provider test tried to reach {destination}, which is NOT on the allowlist.\n"
        "\n"
        "The `live_provider` marker does not open the network — it re-points the guard at "
        f"{sorted(LIVE_PROVIDER_ALLOWED_DESTINATIONS)}. Everything else is refused exactly as it "
        "would be for an ordinary test, because a marker that granted arbitrary egress would be a "
        "far larger hole than the one the verification harness needs.\n"
        "\n"
        "If a harness genuinely needs another destination, add it to "
        "LIVE_PROVIDER_ALLOWED_DESTINATIONS deliberately — `test_live_suite_gate` pins the "
        "contents, so it cannot grow by accident.\n"
        "\n"
        "See pytest_network_guard.py."
    )


def _require_allowed_url(url: httpx.URL) -> None:
    """The allowlist check at the layer where the destination still has a NAME."""
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    if (url.host, port) not in LIVE_PROVIDER_ALLOWED_DESTINATIONS:
        _forbidden_destination(f"{url.scheme}://{url.host}:{port}")


async def _live_guarded_async_request(
    transport: httpx.AsyncHTTPTransport, request: httpx.Request
) -> httpx.Response:
    _require_allowed_url(request.url)
    return await _REAL_ASYNC_HANDLE(transport, request)


def _live_guarded_request(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
    _require_allowed_url(request.url)
    return _REAL_SYNC_HANDLE(transport, request)


def _is_allowed_live_address(address: Any) -> bool:
    """Whether a raw socket address belongs to an allowlisted destination. ==Resolved, not pinned.==

    The floor beneath the HTTP filter, and it has to answer the same question one layer lower, where
    the destination is an ADDRESS and the allowlist is a set of NAMES. So each allowed host is
    resolved *at connect time* — the same moment the client resolved it, through the same OS
    resolver and cache — and the address is matched against the answer. A pinned IP list would be
    wrong within the week (Stripe is behind a CDN), and a name recorded once at session start can go
    stale mid-run.

    ==A host that will not resolve is refused, not excused==: `getaddrinfo` failing means we cannot
    show this address belongs to an allowed destination, and "cannot show" is a refusal at a door.
    """
    if _is_loopback(address):
        return True
    if not isinstance(address, tuple) or len(address) < 2:
        return True  # AF_UNIX or a socketpair: it cannot leave the machine by construction
    host, port = str(address[0]), address[1]
    for allowed_host, allowed_port in LIVE_PROVIDER_ALLOWED_DESTINATIONS:
        if port != allowed_port:
            continue
        try:
            infos = socket.getaddrinfo(allowed_host, allowed_port, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            continue
        if host in {str(info[4][0]) for info in infos}:
            return True
    return False


def _guarded_live_connect(sock: socket.socket, address: Any) -> Any:
    """The socket floor for a live-provider test: loopback, the allowlist, and nothing else.

    ==Without this, the HTTP filter above would be a fence with no posts.== A test could reach any
    host in the world through ``urllib``, a raw socket, or any stack that does not go through
    ``httpx`` — which is the whole reason this plugin has a floor in the first place.
    """
    if _is_allowed_live_address(address):
        return _REAL_SOCKET_CONNECT(sock, address)
    sock.close()
    _forbidden_destination(f"the raw address {address!r}")


@pytest.fixture(autouse=True)
def _forbid_real_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """==Make reaching the real network IMPOSSIBLE, rather than remembering not to.==

    .. rubric:: The incident this exists for

    B-06 changed how the payment gateway is wired, and ``test_payments_checkout_pg`` kept setting
    its fake on the OLD ``app.state`` key. The fake therefore did nothing, the REAL
    ``StripeGateway`` stayed wired, and the suite **opened a TLS connection to api.stripe.com**. It
    came back 401 — because that machine happened to hold no Stripe key — and the test failed on the
    status code, so it read like an ordinary wiring break.

    ==The 401 was luck, and luck is not a control.== Run the same suite on a machine with LIVE keys
    exported (to debug something else, on a laptop, at any point in the next year) and the identical
    mistake bills a real person. The rule this product runs on is that a business's money moves only
    on that business's own account, by its own decision — and a test suite is not a decision.

    .. rubric:: ==Three stacks, because the question is not "what does it cost?"==

    ``httpx`` was shut first because a payment adapter reaching a real API spends money. But cost
    was never the test — ==**"can this process touch the world?"** is==, and three stacks can:

    * ``httpx`` — the payment gateways and every other provider adapter. It charges;
    * ``aiosmtplib`` — SMTP. ==It writes to a REAL PERSON'S INBOX.== This product exists to email
      guests; that is not a side effect, it is the job. Export ``AETHERCAL_SMTP_*`` to debug
      something else, let a fake miss its seam, and the suite mails somebody. ==And unlike a
      charge, a sent email cannot be refunded==;
    * ``httplib2`` — what ``googleapiclient`` reaches the wire through. It writes and DELETES
      events on somebody's real calendar.

    Leaving two of the three shut would have been worse than admitting they were open, because the
    guard would LOOK complete. It was not a hypothetical: before this, ``SmtpEmailSender.send()``
    under test resolved DNS for its configured host and only failed because ``smtp.example.com``
    does not exist — the same luck as the 401 from api.stripe.com, wearing a different name.

    Each stack is shut at ITS door, chosen the same way: ==the narrowest place every caller must
    pass through==, so callers are never enumerated. There is no allow-list of hosts to keep
    current and no adapter that can be forgotten — one added tomorrow is covered the day it is
    written, because it cannot get out either.

    * ``httpx``: its two real transport classes. Whatever a client is asked to fetch — Stripe,
      Mercado Pago, Twilio, Evolution/WhatsApp, Turnstile, cal.com, the outbound webhook
      delivery — it leaves through one of them.
    * ``aiosmtplib``: ``SMTP.connect``. ==Not ``aiosmtplib.send()``== — that is a convenience
      helper which builds an ``SMTP`` and connects, so guarding it would cover today's one caller
      and miss a future one that constructs ``SMTP`` itself. ``connect`` is where the socket is
      opened, and both ways in stop there.
    * ``googleapiclient``: ``httplib2.Http.request``. ``HttpRequest.execute()`` ends there, and
      ``google_auth_httplib2.AuthorizedHttp`` — what ``build(credentials=…)`` wraps the client in —
      delegates to the same method. One door covers the discovery fetch, an event insert and an
      event delete alike.

    ``raising=True`` is part of the guarantee. If any of these libraries renames a method this
    fixture fails LOUDLY at setup, instead of silently patching nothing and quietly re-opening the
    door — the same failure mode the db gate exists to prevent, one layer down.

    .. rubric:: ==And a FLOOR, because three doors is still a list==

    Three named stacks is a photograph of what this repo imports today. ``requests``, ``aiohttp``, a
    driver a dependency drags in next quarter — none of them are named above, and every one of them
    would walk straight out. So underneath the three sits the rule they all obey:
    ``socket.socket.connect``. ==A stack this plugin has never heard of is covered on the day it
    arrives==, because it cannot open a socket either.

    The rule is one sentence — *a test may talk to itself, and to nothing else* — and it lives in
    :func:`_is_loopback`. Loopback is not an exception carved out for convenience: asyncio's own
    event loop opens a loopback socketpair for its self-pipe, and refusing it takes the
    interpreter's plumbing down with it.

    .. rubric:: ==Why the database needs no exception, which is not what anyone expected==

    The obvious design was to derive an allowance from ``AETHERCAL_TEST_DATABASE_URL`` — the test
    database is on a tailnet address, not localhost, so a loopback-only rule looked certain to block
    it. ==Measurement says otherwise: it never reaches this guard at all.== ``psycopg`` connects
    through ``libpq``, in C, and never touches Python's ``socket`` module. Instrumenting
    ``socket.socket.connect`` around a real ``SELECT 1`` records **zero** calls, and the ``-m db``
    suite is green with this floor in place.

    So the allowance was not written. ==A derived allowance nothing exercises is not robustness, it
    is an untested claim== — precisely the kind of decoration this suite exists to catch. The DB's
    isolation from the guard is a fact about its driver, and if that driver ever changes to a
    pure-Python one (``asyncpg``), the ``-m db`` suite fails LOUDLY and immediately rather than
    silently — which is the correct moment to derive the allowance, with a test that can prove it.

    What still passes: ``httpx.MockTransport`` (a different class entirely — the stub answers and
    the real transport is never built), ``ASGITransport`` (in-process, no socket), and ``respx``,
    which this module reconfigures to mock above the transport rather than below it (see
    :data:`respx.mocks.DEFAULT_MOCKER` at the top). Nothing lives at the ``aiosmtplib`` or
    ``httplib2`` layer: the suite fakes those at their own seams (the ``EmailSender`` protocol and
    an injected Google service), so this sits below both fakes and races neither. And the database,
    per the rubric above. That asymmetry is deliberate: this closes the doors that TOUCH THE WORLD,
    not the one that stores.

    .. rubric:: ==And the one way through, for the one suite whose job is to walk through it==

    A ``live_provider``-marked test (see :data:`LIVE_PROVIDER_MARKER`) keeps the two doors that
    write to PEOPLE — SMTP and the Google API — shut, and gets the HTTP doors and the socket floor
    ==re-pointed at an allowlist rather than removed== (:data:`LIVE_PROVIDER_ALLOWED_DESTINATIONS`).
    Every door is still patched for every test; what differs is what the patch permits.

    ==The order below is what makes the narrowing real rather than described:== the two human-facing
    doors are shut FIRST, unconditionally, so no later edit to the branching can take them with it.
    """
    monkeypatch.setattr(aiosmtplib.SMTP, "connect", _forbidden, raising=True)
    monkeypatch.setattr(httplib2.Http, "request", _forbidden, raising=True)
    if request.node.get_closest_marker(LIVE_PROVIDER_MARKER) is not None:
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", _live_guarded_async_request
        )
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _live_guarded_request)
        monkeypatch.setattr(socket.socket, "connect", _guarded_live_connect, raising=True)
        monkeypatch.setattr(socket.socket, "connect_ex", _guarded_live_connect_ex, raising=True)
        return
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _forbidden, raising=True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _forbidden, raising=True)
    # ==And the floor beneath all three.== The three above are known doors; this is the rule that
    # makes a door nobody has built yet unusable too. See the class docstring.
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex, raising=True)
