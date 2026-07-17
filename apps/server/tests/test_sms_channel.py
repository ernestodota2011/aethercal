"""Contract tests for the SMS channel (Twilio), and its fail-closed configuration.

.. warning::

   ==**UNVERIFIED LIVE.**== There is no Twilio account for this project. Not one message below has
   ever left the machine, and none of these assertions is evidence that Twilio accepts what we send.
   What they DO prove is that the request we build matches Twilio's *documented* Messages API — the
   URL, HTTP Basic auth, form encoding, the ``To``/``From``/``Body`` fields — and that each class of
   response is classified correctly.

   That is the honest limit of this suite, and it is stated here rather than discovered later. The
   first live send should be treated as a first integration, not as a regression.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
import respx

from aethercal.server.channels import Channel
from aethercal.server.integrations.messaging.guard import (
    ChannelUnavailable,
    DailyCaps,
    PermanentSendError,
    PhoneChannelSender,
    SendOutcomeUnknown,
)
from aethercal.server.integrations.sms.config import TwilioConfig
from aethercal.server.integrations.sms.sender import TwilioSmsSender

_ACCOUNT_SID = "AC0123456789abcdef"
_AUTH_TOKEN = "twilio-secret-token"
_FROM = "+13055559999"
_BASE_URL = "https://api.twilio.test"
_SEND_URL = f"{_BASE_URL}/2010-04-01/Accounts/{_ACCOUNT_SID}/Messages.json"

_CAPS = DailyCaps(per_phone=2, per_ip=40)

_FULL_ENV = {
    "AETHERCAL_SMS_ACCOUNT_SID": _ACCOUNT_SID,
    "AETHERCAL_SMS_AUTH_TOKEN": _AUTH_TOKEN,
    "AETHERCAL_SMS_FROM_NUMBER": _FROM,
    "AETHERCAL_SMS_BASE_URL": _BASE_URL,
    "AETHERCAL_SMS_DAILY_CAP_PER_PHONE": "2",
    "AETHERCAL_SMS_DAILY_CAP_PER_IP": "40",
}


@pytest_asyncio.fixture
async def sender() -> AsyncIterator[TwilioSmsSender]:
    config = TwilioConfig(
        account_sid=_ACCOUNT_SID,
        auth_token=_AUTH_TOKEN,
        from_number=_FROM,
        caps=_CAPS,
        base_url=_BASE_URL,
    )
    async with httpx.AsyncClient() as client:
        yield TwilioSmsSender(config, client)


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


# --------------------------------------------------------------------------------------
# Configuration.
# --------------------------------------------------------------------------------------


def test_an_entirely_unconfigured_channel_is_off_not_broken() -> None:
    assert TwilioConfig.from_env({}) is None


def test_a_fully_configured_channel_builds() -> None:
    config = TwilioConfig.from_env(_FULL_ENV)

    assert config is not None
    assert config.account_sid == _ACCOUNT_SID
    assert config.from_number == _FROM
    assert config.caps == _CAPS
    assert config.messages_url == _SEND_URL


def test_the_base_url_defaults_to_twilios_public_api() -> None:
    config = TwilioConfig.from_env({k: v for k, v in _FULL_ENV.items() if "BASE_URL" not in k})

    assert config is not None
    assert config.messages_url.startswith("https://api.twilio.com/2010-04-01/Accounts/")


def test_credentials_without_caps_refuse_to_activate() -> None:
    """==The fail-closed one.== See the WhatsApp suite: an uncapped phone channel is an unbounded
    weapon pointed at whatever number somebody typed into the public booking form."""
    environ = {
        "AETHERCAL_SMS_ACCOUNT_SID": _ACCOUNT_SID,
        "AETHERCAL_SMS_AUTH_TOKEN": _AUTH_TOKEN,
        "AETHERCAL_SMS_FROM_NUMBER": _FROM,
    }

    with pytest.raises(RuntimeError, match="DAILY_CAP_PER_PHONE"):
        TwilioConfig.from_env(environ)


def test_partial_credentials_refuse_to_activate() -> None:
    with pytest.raises(RuntimeError, match="half-configured"):
        TwilioConfig.from_env({"AETHERCAL_SMS_ACCOUNT_SID": _ACCOUNT_SID})


async def test_the_sender_satisfies_the_phone_channel_protocol(sender: TwilioSmsSender) -> None:
    assert isinstance(sender, PhoneChannelSender)
    assert sender.channel is Channel.SMS
    assert sender.caps == _CAPS


# --------------------------------------------------------------------------------------
# The documented request (Twilio Messages API).
# --------------------------------------------------------------------------------------


@respx.mock
async def test_it_posts_the_documented_form_encoded_request(sender: TwilioSmsSender) -> None:
    route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(201, json={"sid": "SM123", "status": "queued"})
    )

    await sender.send(to="+13055550123", subject=None, body="See you tomorrow at 10:00.")

    assert route.called
    request = route.calls.last.request
    # Form-encoded, NOT JSON: Twilio's Messages endpoint does not accept a JSON body.
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = _form(request)
    assert form["To"] == ["+13055550123"]
    assert form["From"] == [_FROM]
    assert form["Body"] == ["See you tomorrow at 10:00."]


@respx.mock
async def test_it_authenticates_with_http_basic(sender: TwilioSmsSender) -> None:
    route = respx.post(_SEND_URL).mock(return_value=httpx.Response(201, json={}))

    await sender.send(to="+13055550123", subject=None, body="hi")

    header = route.calls.last.request.headers["authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == f"{_ACCOUNT_SID}:{_AUTH_TOKEN}"


@respx.mock
async def test_the_plus_is_preserved_unlike_whatsapp(sender: TwilioSmsSender) -> None:
    """Twilio wants E.164 WITH the ``+``; Evolution wants bare digits. Two providers, two rules —
    which is exactly why each gets its own adapter rather than one helper with an if in it."""
    route = respx.post(_SEND_URL).mock(return_value=httpx.Response(201, json={}))

    await sender.send(to="+13055550123", subject=None, body="hi")

    assert _form(route.calls.last.request)["To"] == ["+13055550123"]


@respx.mock
async def test_an_empty_recipient_is_permanently_rejected(sender: TwilioSmsSender) -> None:
    route = respx.post(_SEND_URL)

    with pytest.raises(PermanentSendError):
        await sender.send(to="   ", subject=None, body="hi")

    assert not route.called


# --------------------------------------------------------------------------------------
# Status classification.
# --------------------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_a_4xx_is_permanent(sender: TwilioSmsSender, status: int) -> None:
    respx.post(_SEND_URL).mock(
        return_value=httpx.Response(status, json={"code": 21211, "message": "invalid To"})
    )

    with pytest.raises(PermanentSendError, match="provider-rejected"):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_429_or_5xx_is_transient(sender: TwilioSmsSender, status: int) -> None:
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="try later"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
async def test_a_network_failure_is_transient(sender: TwilioSmsSender) -> None:
    respx.post(_SEND_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
async def test_the_auth_token_never_reaches_the_exception_message(sender: TwilioSmsSender) -> None:
    """An exception message lands in the log and in the outbox row. A credential must not."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(401, text="authenticate"))

    with pytest.raises(PermanentSendError) as excinfo:
        await sender.send(to="+13055550123", subject=None, body="hi")

    assert _AUTH_TOKEN not in str(excinfo.value)


# --------------------------------------------------------------------------------------
# The default is RETRY. A needless retry costs a duplicate; a needless retirement costs
# THE MESSAGE — and a silently missing message is exactly the one nobody notices.
# --------------------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [408, 425, 429], ids=["timeout", "too-early", "rate-limited"])
async def test_a_transient_4xx_retries_instead_of_retiring_the_step(
    sender: TwilioSmsSender, status: int
) -> None:
    """==408 / 425 / 429 are the most common failures a messaging provider produces.==

    The old rule retired every 4xx it had not special-cased, so a traffic spike (429), a slow
    provider (408) or an explicit "come back in a moment" (425) killed the step FOREVER: the
    guest's reminder was never sent, and nothing errored — just a ``skipped`` row carrying a reason
    nobody reads.
    """
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="slow down"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
@pytest.mark.parametrize("status", [409, 418, 451, 499])
async def test_an_UNCLASSIFIED_status_retries_rather_than_retiring(
    sender: TwilioSmsSender, status: int
) -> None:
    """The status nobody thought of. It must RETRY, not die.

    This is the test that makes the allow-list an allow-list: a provider that invents a status
    tomorrow, or a proxy that injects one, must not be able to silently destroy a message."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="who knows"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_a_provably_permanent_status_still_retires_the_step(
    sender: TwilioSmsSender, status: int
) -> None:
    """The other half: an invalid number or bad credentials must NOT burn six backoff attempts."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="invalid number"))

    with pytest.raises(PermanentSendError, match="provider-rejected"):
        await sender.send(to="+13055550123", subject=None, body="hi")


# --------------------------------------------------------------------------------------
# The third outcome: we wrote the request and lost the answer.
# --------------------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("the answer never came"),
        httpx.RemoteProtocolError("the connection dropped mid-response"),
        httpx.ReadError("reset by peer"),
    ],
    ids=["read-timeout", "protocol-error", "read-error"],
)
async def test_a_LOST_ANSWER_is_unknown_and_is_neither_retried_nor_retired(
    sender: TwilioSmsSender, error: httpx.HTTPError
) -> None:
    """==The request LEFT this machine and the answer was lost.== The provider may have sent it.

    Retrying could message a real person twice — and worse, under-count the daily cap that
    protects them, since the cap is derived from the ledger. Retiring writes off a message the guest
    may never have received. Both are guesses, and neither is acceptable: this escalates."""
    respx.post(_SEND_URL).mock(side_effect=error)

    with pytest.raises(SendOutcomeUnknown, match="UNKNOWN"):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("no route")],
    ids=["connect-refused", "connect-timeout"],
)
async def test_a_failure_to_CONNECT_is_a_plain_retry_not_an_unknown(
    sender: TwilioSmsSender, error: httpx.HTTPError
) -> None:
    """The other side of the same coin: we never connected, so the request was never transmitted.

    Nothing was delivered, so a retry is provably safe — and treating this as "unknown" would park a
    step (and page a human) every time the network hiccups."""
    respx.post(_SEND_URL).mock(side_effect=error)

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")
