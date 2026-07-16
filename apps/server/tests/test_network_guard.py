"""The network guard's own test: ==a test that skips its fake FAILS, it does not dial out.==

The guard lives in the repo-root ``conftest.py``; this proves it bites. It exists because B-06's
rewiring left ``test_payments_checkout_pg`` setting its fake gateway on a key nothing read any more,
so the REAL ``StripeGateway`` stayed wired and the suite opened a TLS connection to
api.stripe.com. It returned 401 only because that machine held no Stripe key. On a machine with LIVE
keys exported, the same mistake bills a real person.

==A guard nobody tests is a guard nobody has.== So the cases below are the incident, reproduced.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from email.message import EmailMessage
from unittest import mock
from urllib.error import URLError
from urllib.request import urlopen

import aiosmtplib
import httplib2
import httpx
import pytest
import sqlalchemy as sa
from pytest_network_guard import RealNetworkForbiddenError, _is_loopback

from aethercal.server.integrations.mercadopago import MercadoPagoGateway
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import SmtpEmailSender
from aethercal.server.integrations.stripe import StripeGateway


async def test_a_real_async_http_client_cannot_reach_the_network() -> None:
    """==The door itself.== A client given no stub reaches ``AsyncHTTPTransport``, and that is the
    only way out. It is disabled, so it raises instead of resolving a hostname."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(RealNetworkForbiddenError):
            await client.get("https://api.stripe.com/v1/checkout/sessions")


def test_a_real_sync_http_client_cannot_reach_the_network() -> None:
    """The synchronous half of the same door. Nothing in the server uses it today, which is exactly
    why it is closed: the guard must not depend on today's inventory of callers."""
    with httpx.Client() as client, pytest.raises(RealNetworkForbiddenError):
        client.get("https://api.mercadopago.com/v1/payments/1")


async def test_the_real_stripe_gateway_cannot_charge_during_a_test() -> None:
    """==The incident, reproduced.== This is precisely what happened: the fake did not take effect,
    so the REAL gateway was reached with a business's secrets and tried to open a live Checkout
    Session. It must be a red test naming the guard — never a request that leaves the machine.
    """
    gateway = StripeGateway()  # no transport: the real one, exactly as production builds it
    with pytest.raises(RealNetworkForbiddenError):
        await gateway.create_checkout_session(
            idempotency_key="booking:abc",
            amount_cents=5000,
            currency="usd",
            expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            return_url="https://book.example.com/t/acme",
            secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x"},
        )


async def test_the_real_mercado_pago_gateway_cannot_refund_during_a_test() -> None:
    """The same for the provider this cut added — covered on the day it was written, because the
    guard closes the door rather than listing the adapters that walk through it."""
    gateway = MercadoPagoGateway()
    with pytest.raises(RealNetworkForbiddenError):
        await gateway.refund(
            provider_ref="123456789",
            idempotency_key="refund:123456789",
            secrets={"access_token": "TEST-NOT-A-REAL-TOKEN"},
        )


async def test_a_stubbed_transport_still_works() -> None:
    """==The guard must not break the way tests are actually written.== A ``MockTransport`` answers
    before the real transport is reached, so every existing fake keeps working — the guard closes
    the door to the outside, not the door to the stub."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await client.get("https://api.stripe.com/v1/anything")
    assert response.json() == {"ok": True}


# --------------------------------------------------------------------------------------
# ==The other two doors: SMTP and Google Calendar.==
#
# Neither spends money, and that was never the test. The test is "can this process touch the
# world?" — and both can: `aiosmtplib` writes to a REAL PERSON's inbox, and googleapiclient
# writes or deletes an event on a REAL calendar. Leaving two of three doors shut is worse than
# admitting they are open, because the guard then LOOKS complete.
# --------------------------------------------------------------------------------------


async def test_the_real_smtp_sender_cannot_email_a_human_during_a_test() -> None:
    """==The incident, in the stack that writes to people.==

    This product exists to email real guests. Export ``AETHERCAL_SMTP_*`` to debug something
    else, let a fake miss its seam, and the suite writes to somebody's inbox — and unlike a
    charge, a sent email cannot be refunded.
    """
    sender = SmtpEmailSender(SmtpConfig(host="smtp.example.com", from_addr="a@example.com"))
    message = EmailMessage()
    message["To"] = "guest@example.com"
    message["Subject"] = "this must never be sent"
    message.set_content("nor this")

    with pytest.raises(RealNetworkForbiddenError):
        await sender.send(message)


async def test_the_smtp_door_is_the_connection_not_the_send_helper() -> None:
    """==The door, not the convenience function.==

    ``aiosmtplib.send()`` is a helper that builds an ``SMTP`` client and connects; a caller may
    equally construct ``SMTP`` itself. Guarding ``send()`` would cover today's one caller and
    miss tomorrow's. The socket is opened by ``connect``, so that is what is shut — and both
    ways in stop at the same place.
    """
    client = aiosmtplib.SMTP(hostname="smtp.example.com", port=587)
    with pytest.raises(RealNetworkForbiddenError):
        await client.connect()


def test_the_google_api_client_cannot_touch_a_real_calendar() -> None:
    """==The door every Google Calendar call goes through.==

    ``googleapiclient`` reaches the wire through ``httplib2`` — ``HttpRequest.execute()`` ends at
    ``Http.request``, and ``google_auth_httplib2.AuthorizedHttp`` (what ``build(credentials=...)``
    wraps it in) delegates to the same method. So one door covers the discovery fetch, an event
    insert, and an event DELETE on somebody's real calendar alike.
    """
    with pytest.raises(RealNetworkForbiddenError):
        httplib2.Http().request("https://www.googleapis.com/calendar/v3/users/me/calendarList")


# --------------------------------------------------------------------------------------
# ==The floor: a stack nobody has thought of yet.==
#
# Three doors is a list, and a list is a photograph. `requests`, `aiohttp`, a driver added next
# quarter — none of them are in the list above, and every one of them ends at a socket.
# --------------------------------------------------------------------------------------


def test_a_raw_socket_to_the_outside_world_is_refused() -> None:
    """==The floor itself.== Not httpx, not aiosmtplib, not httplib2 — the thing they all end at.
    A stack this guard has never heard of cannot get past it either."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with sock, pytest.raises(RealNetworkForbiddenError):
        sock.connect(("93.184.216.34", 80))


def test_a_brand_new_http_stack_is_covered_the_day_it_is_added() -> None:
    """==Anti-vacuity, and the whole reason the floor exists.==

    .. note::

       This test carries NO ``ResourceWarning`` filter, and that is a claim rather than an omission:
       ``urlopen`` does not close its socket when ``connect`` raises, so the guard closes it before
       refusing. If it did not, the leaked descriptor would surface at an arbitrary later garbage
       collection and — with this repo's ``filterwarnings = ["error"]`` — fail whichever unrelated
       test was running then. It did exactly that once, in ``test_notifications_service``.

    ``urllib`` is in the standard library and nothing in this codebase uses it — which is exactly
    the point. It is a stand-in for `requests`, `aiohttp`, or whatever a dependency drags in next
    quarter: NONE of them are named anywhere in this plugin, and every one of them is refused,
    because they all end at a socket. If this test ever passes for the wrong reason (an import
    error, say), it proves nothing — so it asserts the guard's own exception by type.
    """
    with pytest.raises((RealNetworkForbiddenError, URLError)) as excinfo:
        urlopen("http://93.184.216.34/", timeout=5)
    # urllib wraps the socket failure; the guard's exception must be the CAUSE, never a real
    # connection error — a DNS or timeout failure here would mean the socket really was attempted.
    raised = excinfo.value
    cause = raised if isinstance(raised, RealNetworkForbiddenError) else raised.reason
    assert isinstance(cause, RealNetworkForbiddenError), f"expected the guard, got {cause!r}"


def test_the_process_may_still_talk_to_itself() -> None:
    """==The other half of anti-vacuity: if EVERYTHING died, the guard would prove nothing.==

    A guard that refuses loopback is not a guard, it is a broken test suite — asyncio's own event
    loop opens a loopback socketpair for its self-pipe, and blocking that took the interpreter's
    plumbing down with it. So the rule lets the machine talk to itself, and this proves the
    allowance is real rather than asserted.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(("127.0.0.1", port))  # must NOT raise
            assert client.getpeername()[1] == port


def test_the_loopback_rule_is_derived_not_a_host_list() -> None:
    """The allowance is computed from the address, not looked up in a table — so IPv6 loopback and
    the whole 127.0.0.0/8 range are covered without anyone having listed them."""
    assert _is_loopback(("127.0.0.1", 5432))
    assert _is_loopback(("127.0.0.53", 80)), "the rule is the /8, not one address"
    assert _is_loopback(("::1", 443, 0, 0)), "IPv6 loopback, unlisted and covered"
    assert _is_loopback("/var/run/some.sock"), "a UNIX socket cannot leave the machine"
    assert not _is_loopback(("100.77.142.12", 5432)), "a private LAN/tailnet address is NOT local"
    assert not _is_loopback(("api.stripe.com", 443)), "a hostname means a real resolution was meant"


@pytest.mark.db
def test_the_database_reaches_its_tailnet_host_through_the_floor(pg_admin_url: str) -> None:
    """==Anti-vacuity, at the place everyone expected the guard to break.==

    The test database is at a TAILNET address, not localhost, so a loopback-only rule looks certain
    to block it — which is why the obvious design was an allowance derived from
    ``AETHERCAL_TEST_DATABASE_URL``. ==It was never written, because measurement says the DB does
    not reach this guard at all==: ``psycopg`` connects through ``libpq``, in C, and never touches
    Python's ``socket`` module.

    This test is that measurement, kept: it records every ``socket.socket.connect`` Python makes
    during a real query and asserts BOTH that the query worked and that the guard saw nothing. If a
    future driver were pure-Python, the recorded list would stop being empty and this would say so —
    which is the moment to derive the allowance, with a test that can prove it.
    """
    # ==The skip rides on the fixture, not on the marker.== `pytest.mark.db` only SELECTS; what
    # makes an offline run skip quietly is depending on `pg_admin_url`, which is where conftest
    # calls `pytest.skip`. Reading the environment directly instead made this fail on every laptop
    # without a database — the marker is not a guard.
    url = pg_admin_url
    assert "127.0.0.1" not in url and "localhost" not in url, (
        "this test is only meaningful while the DB is NOT on loopback — if it moved to localhost "
        "the loopback rule would let it through and prove nothing about libpq"
    )

    seen: list[object] = []
    real_connect = socket.socket.connect

    def recording(self: socket.socket, address: object) -> object:
        seen.append(address)
        return real_connect(self, address)  # type: ignore[arg-type]

    engine = sa.create_engine(url)
    try:
        with mock.patch.object(socket.socket, "connect", recording), engine.connect() as conn:
            assert conn.execute(sa.text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()

    assert seen == [], (
        "psycopg reached Python's socket layer — the floor now applies to the database, and the "
        f"allowance derived from AETHERCAL_TEST_DATABASE_URL must be written. Saw: {seen}"
    )
