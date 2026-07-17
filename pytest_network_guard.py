"""==No test may reach the real network.== A repo-wide, fail-closed pytest plugin.

The sibling of the db gate in ``conftest.py``, aimed at the other way a suite can pass while proving
nothing — or worse, while spending real money. Registered from the root ``conftest.py`` via
``pytest_plugins``, so it applies to every test in the tree and a test can import
:class:`RealNetworkForbiddenError` by a name that does not collide with the several ``conftest``
modules this repo has.
"""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
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
    """
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _forbidden, raising=True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _forbidden, raising=True)
    monkeypatch.setattr(aiosmtplib.SMTP, "connect", _forbidden, raising=True)
    monkeypatch.setattr(httplib2.Http, "request", _forbidden, raising=True)
    # ==And the floor beneath all three.== The three above are known doors; this is the rule that
    # makes a door nobody has built yet unusable too. See the class docstring.
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect, raising=True)
