"""Tests for the SDK's v1 resource methods (offline via httpx.MockTransport).

Each test drives a real ``AetherCalClient`` against a canned API response and asserts BOTH the wire
contract the client emits (method, path, query, JSON body, ``Authorization`` header) AND the typed
value it parses back. No network: every request is intercepted by ``httpx.MockTransport``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from aethercal.client import AetherCalAPIError, AetherCalClient
from aethercal.schemas.bookings import BookingCreate

API_KEY = "ack_test_secret"


def _event_type_json(*, slug: str = "intro-call", title: str = "Intro Call") -> dict[str, Any]:
    """A full EventType as the API serializes it (RF-14 read shape)."""
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "host_id": str(uuid.uuid4()),
        "schedule_id": str(uuid.uuid4()),
        "slug": slug,
        "title": title,
        "description": "A short intro.",
        "location": "Google Meet",
        "duration_seconds": 1800,
        "buffer_before_seconds": 0,
        "buffer_after_seconds": 0,
        "min_notice_seconds": 0,
        "max_advance_seconds": 2592000,
        "increment_seconds": None,
        "max_per_day": None,
        "questions": [],
        "active": True,
    }


def _slots_json(event_type_id: str, tz: str) -> dict[str, Any]:
    """A SlotsResponse as the API serializes it (UTC bounds, echoed tz)."""
    return {
        "event_type_id": event_type_id,
        "timezone": tz,
        "availability": "ok",
        "slots": [
            {"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"},
            {"start": "2026-07-14T14:00:00Z", "end": "2026-07-14T14:30:00Z"},
        ],
    }


def _booking_json(*, status: str = "confirmed") -> dict[str, Any]:
    """A BookingRead as the API serializes it — note the WIRE names ``start`` / ``end``."""
    return {
        "id": str(uuid.uuid4()),
        "event_type_id": str(uuid.uuid4()),
        "start": "2026-07-14T13:00:00Z",
        "end": "2026-07-14T13:30:00Z",
        "status": status,
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "America/New_York",
        "guest_notes": None,
        "answers": {},
        "meeting_url": "https://meet.example/xyz",
        "rescheduled_from_id": None,
        "cancelled_at": None,
        "created_at": "2026-07-01T00:00:00Z",
    }


def _client(handler: httpx.MockTransport) -> AetherCalClient:
    return AetherCalClient("http://api.test", api_key=API_KEY, transport=handler)


def test_list_event_types_hits_path_and_parses() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=[_event_type_json(slug="a"), _event_type_json(slug="b")])

    with _client(httpx.MockTransport(handler)) as client:
        rows = client.list_event_types()

    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v1/event-types/"
    assert seen["auth"] == f"Bearer {API_KEY}"
    assert [row.slug for row in rows] == ["a", "b"]


def test_get_slots_sends_query_and_parses() -> None:
    seen: dict[str, Any] = {}
    event_type_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_slots_json(str(event_type_id), "America/New_York"))

    with _client(httpx.MockTransport(handler)) as client:
        result = client.get_slots(
            event_type_id,
            window_from=date(2026, 7, 14),
            window_to=date(2026, 7, 20),
            tz="America/New_York",
        )

    assert seen["path"] == "/api/v1/slots/"
    assert seen["params"] == {
        "event_type": str(event_type_id),
        "from": "2026-07-14",
        "to": "2026-07-20",
        "tz": "America/New_York",
    }
    assert result.availability == "ok"
    assert result.timezone == "America/New_York"
    assert len(result.slots) == 2
    assert result.slots[0].start == datetime(2026, 7, 14, 13, 0, tzinfo=UTC)


def test_create_booking_posts_body_and_parses_wire_names() -> None:
    seen: dict[str, Any] = {}
    event_type_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(201, json=_booking_json())

    booking = BookingCreate(
        event_type_id=event_type_id,
        start=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="America/New_York",
        locale="es",
    )
    with _client(httpx.MockTransport(handler)) as client:
        result = client.create_booking(booking)

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/bookings/"
    assert str(event_type_id) in seen["body"]
    assert "ada@example.com" in seen["body"]
    # The API returns wire ``start`` / ``end``; the client must still parse them onto BookingRead.
    assert result.start == datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    assert result.end == datetime(2026, 7, 14, 13, 30, tzinfo=UTC)
    assert result.guest_email == "ada@example.com"


def test_create_booking_maps_slot_conflict_to_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": "slot_unavailable", "message": "That time is no longer available"}
        )

    booking = BookingCreate(
        event_type_id=uuid.uuid4(),
        start=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="America/New_York",
    )
    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(AetherCalAPIError) as exc_info,
    ):
        client.create_booking(booking)

    assert exc_info.value.status_code == 409
    assert exc_info.value.error == "slot_unavailable"


def test_cancel_booking_posts_to_cancel_path_with_token() -> None:
    seen: dict[str, Any] = {}
    booking_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_booking_json(status="cancelled"))

    with _client(httpx.MockTransport(handler)) as client:
        result = client.cancel_booking(booking_id, token="signed.guest.token")

    assert seen["method"] == "POST"
    assert seen["path"] == f"/api/v1/bookings/{booking_id}/cancel"
    assert seen["params"] == {"token": "signed.guest.token"}
    assert result.status.value == "cancelled"


def test_reschedule_booking_sends_new_start_and_token() -> None:
    seen: dict[str, Any] = {}
    booking_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json=_booking_json())

    with _client(httpx.MockTransport(handler)) as client:
        client.reschedule_booking(
            booking_id,
            new_start=datetime(2026, 7, 21, 15, 0, tzinfo=UTC),
            token="signed.guest.token",
        )

    assert seen["method"] == "POST"
    assert seen["path"] == f"/api/v1/bookings/{booking_id}/reschedule"
    assert seen["params"] == {"token": "signed.guest.token"}
    assert "2026-07-21T15:00:00" in seen["body"]


def test_expired_token_maps_to_forbidden_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": "forbidden", "message": "Invalid or expired link"}
        )

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(AetherCalAPIError) as exc_info,
    ):
        client.cancel_booking(uuid.uuid4(), token="dead.token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "forbidden"
