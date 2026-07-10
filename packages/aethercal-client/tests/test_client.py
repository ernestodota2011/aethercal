"""Tests for the AetherCal SDK client (offline via httpx.MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from aethercal.client import AetherCalAPIError, AetherCalClient, AsyncAetherCalClient


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok"})


def _unauthorized_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        401, json={"error": "unauthorized", "message": "Invalid or missing API key"}
    )


def test_sync_health_parses_payload() -> None:
    with AetherCalClient("http://testserver", transport=httpx.MockTransport(_ok_handler)) as client:
        assert client.health() == {"status": "ok"}


def test_sync_sends_bearer_auth_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok"})

    with AetherCalClient(
        "http://testserver", api_key="ack_abcd1234_secret", transport=httpx.MockTransport(handler)
    ) as client:
        client.health()

    assert seen["authorization"] == "Bearer ack_abcd1234_secret"


def test_error_response_is_mapped_to_api_error() -> None:
    with (
        AetherCalClient(
            "http://testserver", transport=httpx.MockTransport(_unauthorized_handler)
        ) as client,
        pytest.raises(AetherCalAPIError) as exc_info,
    ):
        client.health()

    assert exc_info.value.status_code == 401
    assert exc_info.value.error == "unauthorized"
    assert exc_info.value.message == "Invalid or missing API key"


def test_ping_true_on_success_false_on_error() -> None:
    with AetherCalClient("http://testserver", transport=httpx.MockTransport(_ok_handler)) as client:
        assert client.ping() is True

    with AetherCalClient(
        "http://testserver", transport=httpx.MockTransport(_unauthorized_handler)
    ) as client:
        assert client.ping() is False


async def test_async_health_parses_payload() -> None:
    async with AsyncAetherCalClient(
        "http://testserver", transport=httpx.MockTransport(_ok_handler)
    ) as client:
        assert await client.health() == {"status": "ok"}


async def test_async_ping_false_on_error() -> None:
    async with AsyncAetherCalClient(
        "http://testserver", transport=httpx.MockTransport(_unauthorized_handler)
    ) as client:
        assert await client.ping() is False
