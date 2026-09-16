"""Live and offline client layer for Microsoft 365 / Graph API calendar integration.

Implements busy queries, event insertion with Microsoft Teams meeting generation,
and idempotent event deletion (treating 404/410 as already-gone successes).

.. rubric:: What this client does NOT do, and why

* **No token refresh.** The access token arrives from the caller (the stored credential); a 401 is
  a real error that the outbox retries, and refreshing here would need the tenant's OAuth client
  secret — a responsibility of the connection layer, not of a per-call transport.
* **No 429 backoff.** A rate limit is a TRANSIENT failure, and the outbox already owns retry
  policy (backoff + dead-letter) for every outbound effect. Retrying inside the call would nest
  one backoff inside another and hide the pressure from the operator.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from aethercal.core.model import TimeInterval

from .parse import (
    MicrosoftEventRequest,
    build_graph_event_body,
    build_schedule_request_body,
    extract_teams_join_url,
    parse_graph_schedule,
)

_logger = logging.getLogger(__name__)

# Bounded transport timeout (mirrors Google and CalDAV bounded timeouts).
# Must stay comfortably below the outbox worker lease (5 minutes) so stalled
# calls fail rather than hanging indefinitely.
MICROSOFT_HTTP_TIMEOUT_SECONDS = 20.0

GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0"
_ALREADY_GONE_STATUSES = frozenset({404, 410})


def query_busy(service: Any, schedule_id: str, window: TimeInterval) -> list[TimeInterval]:
    """Query freebusy for `schedule_id` over `window` using Graph API `getSchedule`."""
    payload = service.get_schedule(schedule_id, window)
    return parse_graph_schedule(payload, schedule_id)


def insert_event(
    service: Any,
    schedule_id: str,
    request: MicrosoftEventRequest,
) -> tuple[str, str | None]:
    """Insert a calendar event for `schedule_id`; returns `(event_id, teams_join_url)`."""
    body = build_graph_event_body(request)
    created = service.create_event(schedule_id, body)
    event_id = str(created.get("id", ""))
    if not event_id:
        raise RuntimeError("Microsoft Graph event creation did not return an event id.")
    teams_join_url = extract_teams_join_url(created)
    return event_id, teams_join_url


def delete_event(service: Any, schedule_id: str, event_id: str) -> None:
    """Delete a calendar event idempotently (treating 404 and 410 as success)."""
    try:
        service.delete_event(schedule_id, event_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in _ALREADY_GONE_STATUSES:
            _logger.info(
                "Microsoft event %s for %s was already deleted (HTTP %d); treating as success.",
                event_id,
                schedule_id,
                exc.response.status_code,
            )
            return
        raise
    except Exception as exc:
        # The already-gone signal can arrive in three shapes: an ``httpx`` error (caught above),
        # an SDK-style exception carrying ``status_code``/``code``, or an exception whose RESPONSE
        # carries it (``exc.response.status_code``). All three mean the same thing here.
        status = (
            getattr(exc, "status_code", None)
            or getattr(exc, "code", None)
            or getattr(getattr(exc, "response", None), "status_code", None)
        )
        if status in _ALREADY_GONE_STATUSES:
            _logger.info(
                "Microsoft event %s for %s was already deleted (status %s); treating as success.",
                event_id,
                schedule_id,
                status,
            )
            return
        raise


class _MicrosoftGraphHttpClient:
    """HTTP client for Microsoft Graph API with Bearer auth and bounded timeout.

    Can be injected with an `httpx.MockTransport` in tests to verify real HTTP wire formats offline.
    """

    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = GRAPH_API_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=MICROSOFT_HTTP_TIMEOUT_SECONDS,
            transport=self._transport,
        )

    def get_schedule(self, schedule_id: str, window: TimeInterval) -> dict[str, Any]:
        """Call `POST /users/{id}/calendar/getSchedule` or `/me/calendar/getSchedule`."""
        endpoint = (
            "/me/calendar/getSchedule"
            if schedule_id.lower() in ("primary", "me")
            else f"/users/{schedule_id}/calendar/getSchedule"
        )
        body = build_schedule_request_body([schedule_id], window)
        with self._client() as client:
            resp = client.post(endpoint, json=body)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    def create_event(self, schedule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Call `POST /users/{id}/events` or `/me/events`."""
        endpoint = (
            "/me/events"
            if schedule_id.lower() in ("primary", "me")
            else f"/users/{schedule_id}/events"
        )
        with self._client() as client:
            resp = client.post(endpoint, json=body)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    def delete_event(self, schedule_id: str, event_id: str) -> None:
        """Call `DELETE /users/{id}/events/{event_id}` or `/me/events/{event_id}`."""
        endpoint = (
            f"/me/events/{event_id}"
            if schedule_id.lower() in ("primary", "me")
            else f"/users/{schedule_id}/events/{event_id}"
        )
        with self._client() as client:
            resp = client.delete(endpoint)
            resp.raise_for_status()


def build_service(
    *,
    access_token: str,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    """Build a Microsoft Graph service client from OAuth access token."""
    return _MicrosoftGraphHttpClient(access_token=access_token, transport=transport)
