"""Contract tests for the outbound-webhook schemas (RF-17).

These pin AetherCal's OWN v1 envelope shape and the request/response models — the secret is
write-only (never surfaced on a read) and events are constrained to the three booking events.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethercal.schemas.webhooks import (
    WEBHOOK_API_VERSION,
    WEBHOOK_EVENTS,
    WebhookCreate,
    WebhookCreated,
    WebhookEnvelope,
    WebhookRead,
    WebhookUpdate,
)


def test_webhook_events_are_the_four_booking_events() -> None:
    """``booking.no_show`` (RF-25) is part of the contract, not an afterthought.

    Everything else derives from this ``Literal``: the subscription validator, the read model, the
    envelope and the OpenAPI schema. ``Webhook.events`` is a JSON column, so widening the vocabulary
    needs no data migration — an existing subscriber cannot have asked for an event that until now
    did not exist."""
    assert set(WEBHOOK_EVENTS) == {
        "booking.created",
        "booking.cancelled",
        "booking.rescheduled",
        "booking.no_show",
    }


def test_a_subscription_can_be_created_for_the_no_show_event() -> None:
    created = WebhookCreate(url="https://consumer.test/hook", events=["booking.no_show"])

    assert created.events == ["booking.no_show"]


def test_the_envelope_carries_the_no_show_event() -> None:
    envelope = WebhookEnvelope(
        event="booking.no_show",
        timestamp="2026-07-09T12:00:00+00:00",
        data={"id": "bk_1", "status": "no_show"},
    )

    assert envelope.model_dump()["event"] == "booking.no_show"


def test_envelope_shape_matches_contract() -> None:
    envelope = WebhookEnvelope(
        event="booking.created",
        timestamp="2026-07-09T12:00:00+00:00",
        data={"booking_id": "bk_1"},
    )
    assert envelope.api_version == WEBHOOK_API_VERSION == "1"
    assert envelope.model_dump() == {
        "event": "booking.created",
        "api_version": "1",
        "timestamp": "2026-07-09T12:00:00+00:00",
        "data": {"booking_id": "bk_1"},
    }


def test_envelope_rejects_unknown_event() -> None:
    with pytest.raises(ValidationError):
        WebhookEnvelope(
            event="booking.exploded",  # type: ignore[arg-type]
            timestamp="2026-07-09T12:00:00+00:00",
            data={},
        )


def test_create_rejects_unknown_event() -> None:
    with pytest.raises(ValidationError):
        WebhookCreate(
            url="https://consumer.test/hook",
            events=["booking.exploded"],  # type: ignore[list-item]
        )


def test_create_requires_at_least_one_event() -> None:
    with pytest.raises(ValidationError):
        WebhookCreate(url="https://consumer.test/hook", events=[])


def test_create_secret_is_optional() -> None:
    created = WebhookCreate(url="https://consumer.test/hook", events=["booking.created"])
    assert created.secret is None


def test_create_accepts_a_supplied_secret() -> None:
    created = WebhookCreate(
        url="https://consumer.test/hook",
        events=["booking.created"],
        secret="my-own-secret",
    )
    assert created.secret == "my-own-secret"


def test_read_never_exposes_the_secret() -> None:
    assert "secret" not in WebhookRead.model_fields


def test_created_response_carries_the_secret_once() -> None:
    assert "secret" in WebhookCreated.model_fields
    # WebhookCreated is a WebhookRead plus the one-time secret.
    assert issubclass(WebhookCreated, WebhookRead)


def test_update_is_all_optional() -> None:
    update = WebhookUpdate()
    assert update.url is None
    assert update.events is None
    assert update.active is None


@pytest.mark.parametrize(
    "bad_url", ["ftp://consumer.test/hook", "file:///etc/passwd", "ws://consumer.test/hook"]
)
def test_create_rejects_non_http_scheme(bad_url: str) -> None:
    # Scheme is validated at registration (fast fail); the authoritative IP check is send-time.
    with pytest.raises(ValidationError):
        WebhookCreate(url=bad_url, events=["booking.created"])


def test_create_accepts_http_and_https() -> None:
    assert (
        WebhookCreate(url="http://consumer.test/hook", events=["booking.created"]).url
        == "http://consumer.test/hook"
    )
    assert (
        WebhookCreate(url="https://consumer.test/hook", events=["booking.created"]).url
        == "https://consumer.test/hook"
    )


def test_update_rejects_non_http_scheme() -> None:
    with pytest.raises(ValidationError):
        WebhookUpdate(url="ftp://consumer.test/hook")


def test_update_url_none_is_still_allowed() -> None:
    assert WebhookUpdate(url=None).url is None
