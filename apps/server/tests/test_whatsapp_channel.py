"""Contract tests for the WhatsApp channel (Evolution API), and its fail-closed configuration.

``respx`` intercepts the httpx transport, so what is asserted is the request we ACTUALLY build —
method, path, auth header, body shape — against Evolution's documented ``/message/sendText``
endpoint. A hand-written mock object would only prove that our code agrees with itself.

The status classification gets as much attention as the happy path, because collapsing "the number
is malformed" into "the provider is having a bad minute" is what fills a dead-letter with noise, or
throws away a message that would have gone out on the next try.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

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
)
from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender

_BASE_URL = "https://wa.example.test"
_INSTANCE = "aethercal"
_API_KEY = "evolution-key"
_SEND_URL = f"{_BASE_URL}/message/sendText/{_INSTANCE}"

_CAPS = DailyCaps(per_phone=3, per_ip=50)

_FULL_ENV = {
    "AETHERCAL_WHATSAPP_BASE_URL": _BASE_URL,
    "AETHERCAL_WHATSAPP_INSTANCE": _INSTANCE,
    "AETHERCAL_WHATSAPP_API_KEY": _API_KEY,
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "3",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "50",
}


@pytest_asyncio.fixture
async def sender() -> AsyncIterator[EvolutionWhatsAppSender]:
    config = EvolutionConfig(base_url=_BASE_URL, instance=_INSTANCE, api_key=_API_KEY, caps=_CAPS)
    async with httpx.AsyncClient() as client:
        yield EvolutionWhatsAppSender(config, client)


# --------------------------------------------------------------------------------------
# Configuration: off, half-configured, or on. The middle one must be loud.
# --------------------------------------------------------------------------------------


def test_an_entirely_unconfigured_channel_is_off_not_broken() -> None:
    """No credentials at all = the operator does not want WhatsApp. That is a decision, not an
    error: the channel is absent from the registry and its steps skip with a reason."""
    assert EvolutionConfig.from_env({}) is None


def test_a_fully_configured_channel_builds() -> None:
    config = EvolutionConfig.from_env(_FULL_ENV)

    assert config is not None
    assert config.base_url == _BASE_URL
    assert config.instance == _INSTANCE
    assert config.api_key == _API_KEY
    assert config.caps == _CAPS


def test_credentials_without_caps_refuse_to_activate() -> None:
    """==The fail-closed one.== Credentials present, caps absent: the channel would come up SENDING
    and UNCAPPED. The recipient comes from a public form, so that is an unbounded weapon pointed at
    strangers on the operator's own messaging account — and its only symptom would be the bill."""
    environ = {
        "AETHERCAL_WHATSAPP_BASE_URL": _BASE_URL,
        "AETHERCAL_WHATSAPP_INSTANCE": _INSTANCE,
        "AETHERCAL_WHATSAPP_API_KEY": _API_KEY,
    }

    with pytest.raises(RuntimeError, match="DAILY_CAP_PER_PHONE"):
        EvolutionConfig.from_env(environ)


def test_partial_credentials_refuse_to_activate() -> None:
    """Half a set of credentials is a typo, not a configuration. Booting "off" would hide it."""
    with pytest.raises(RuntimeError, match="half-configured"):
        EvolutionConfig.from_env({"AETHERCAL_WHATSAPP_BASE_URL": _BASE_URL})


def test_a_trailing_slash_on_the_base_url_does_not_double_up() -> None:
    config = EvolutionConfig.from_env({**_FULL_ENV, "AETHERCAL_WHATSAPP_BASE_URL": f"{_BASE_URL}/"})

    assert config is not None
    assert config.base_url == _BASE_URL


# --------------------------------------------------------------------------------------
# The sender IS a phone channel: it carries its caps, structurally.
# --------------------------------------------------------------------------------------


async def test_the_sender_satisfies_the_phone_channel_protocol(
    sender: EvolutionWhatsAppSender,
) -> None:
    """The registry only accepts senders that carry caps, so an uncapped phone sender is not merely
    discouraged — it is unrepresentable."""
    assert isinstance(sender, PhoneChannelSender)
    assert sender.channel is Channel.WHATSAPP
    assert sender.caps == _CAPS


# --------------------------------------------------------------------------------------
# The documented request.
# --------------------------------------------------------------------------------------


@respx.mock
async def test_it_posts_the_documented_request(sender: EvolutionWhatsAppSender) -> None:
    route = respx.post(_SEND_URL).mock(
        return_value=httpx.Response(201, json={"key": {"id": "abc"}, "status": "PENDING"})
    )

    await sender.send(to="+13055550123", subject=None, body="See you tomorrow at 10:00.")

    assert route.called
    request = route.calls.last.request
    assert request.headers["apikey"] == _API_KEY
    payload = json.loads(request.content)
    assert payload["text"] == "See you tomorrow at 10:00."
    assert payload["linkPreview"] is False


@respx.mock
async def test_the_number_is_sent_as_bare_digits(sender: EvolutionWhatsAppSender) -> None:
    """Evolution addresses a chat by its JID, whose user part carries no ``+``. Passing E.164
    straight through addresses the message to nobody — silently, with a success back."""
    route = respx.post(_SEND_URL).mock(return_value=httpx.Response(201, json={}))

    await sender.send(to="+1 (305) 555-0123", subject=None, body="hi")

    assert json.loads(route.calls.last.request.content)["number"] == "13055550123"


@respx.mock
async def test_a_recipient_with_no_digits_is_permanently_rejected(
    sender: EvolutionWhatsAppSender,
) -> None:
    route = respx.post(_SEND_URL)

    with pytest.raises(PermanentSendError):
        await sender.send(to="not-a-number", subject=None, body="hi")

    assert not route.called, "a send was attempted against a recipient that is not a phone number"


# --------------------------------------------------------------------------------------
# Status classification: "will never work" vs "try again shortly".
# --------------------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_a_4xx_is_permanent_and_retires_the_step(
    sender: EvolutionWhatsAppSender, status: int
) -> None:
    """A malformed number or an unknown instance cannot be fixed by waiting. Retrying it burns six
    attempts of backoff and dead-letters — noise in the queue, and still no message."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="bad request"))

    with pytest.raises(PermanentSendError, match="provider-rejected"):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_429_or_5xx_is_transient_and_retries(
    sender: EvolutionWhatsAppSender, status: int
) -> None:
    """A provider having a bad minute must not lose the message."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(status, text="slow down"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
async def test_a_network_failure_is_transient(sender: EvolutionWhatsAppSender) -> None:
    respx.post(_SEND_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    with pytest.raises(ChannelUnavailable):
        await sender.send(to="+13055550123", subject=None, body="hi")


@respx.mock
async def test_the_api_key_never_reaches_the_exception_message(
    sender: EvolutionWhatsAppSender,
) -> None:
    """An exception message ends up in the log and in the outbox row. A credential must not."""
    respx.post(_SEND_URL).mock(return_value=httpx.Response(401, text="unauthorized"))

    with pytest.raises(PermanentSendError) as excinfo:
        await sender.send(to="+13055550123", subject=None, body="hi")

    assert _API_KEY not in str(excinfo.value)
