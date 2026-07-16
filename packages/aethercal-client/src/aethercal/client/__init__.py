"""AetherCal SDK: a thin sync + async httpx client for API v1.

Ola 0 ships only the base: construction (``base_url`` + optional ``api_key`` → ``Authorization:
Bearer`` header), a ``health()`` / ``ping()`` method, and error mapping onto
:class:`AetherCalError`. Feature waves add typed methods (event types, schedules, bookings) as they
land; the error envelope they all share is :class:`aethercal.schemas.ErrorResponse`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from types import TracebackType
from typing import Any

import httpx
from pydantic import ValidationError

from aethercal.schemas import ErrorResponse
from aethercal.schemas.bookings import BookingCreate, BookingRead, BookingReschedule
from aethercal.schemas.branding import TenantBrandingRead
from aethercal.schemas.event_types import EventTypeRead
from aethercal.schemas.slots import SlotsResponse

DEFAULT_TIMEOUT = 10.0
_HEALTH_PATH = "/api/v1/health"
_BRANDING_PATH = "/api/v1/branding"
_EVENT_TYPES_PATH = "/api/v1/event-types/"
_SLOTS_PATH = "/api/v1/slots/"
_BOOKINGS_PATH = "/api/v1/bookings/"


class AetherCalError(Exception):
    """Base class for every error raised by the client."""


class AetherCalAPIError(AetherCalError):
    """The API returned a non-2xx response. Carries the parsed error envelope."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"{status_code} {error}: {message}")


class AetherCalTransportError(AetherCalError):
    """The request never produced an HTTP response.

    Raised when the underlying connection fails before any status line arrives — DNS failure,
    connection refused, TLS error, or a read/connect/pool timeout. Callers that only catch
    :class:`AetherCalError` (or this subclass) no longer have raw ``httpx.RequestError`` leaking
    through the SDK boundary. The originating httpx exception is chained as ``__cause__``.
    """


def _auth_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _booking_from_api(payload: dict[str, Any]) -> BookingRead:
    """Parse a booking JSON body onto :class:`BookingRead`.

    The API emits the booking's instants under the wire names ``start`` / ``end`` (the schema's
    field names), but :class:`BookingRead` validates them through the ``start_at`` / ``end_at``
    validation aliases (they map from the ORM columns) and does not enable ``populate_by_name``. So
    a naive ``model_validate`` of the wire body would miss both fields; we remap those two keys back
    onto the aliases the model expects. Every other field name matches the wire body verbatim.
    """
    remapped = dict(payload)
    if "start" in remapped:
        remapped["start_at"] = remapped.pop("start")
    if "end" in remapped:
        remapped["end_at"] = remapped.pop("end")
    return BookingRead.model_validate(remapped)


def _raise_for_status(response: httpx.Response) -> None:
    """Map a non-2xx response onto :class:`AetherCalAPIError` (parsing the error envelope)."""
    if response.is_success:
        return
    error_code = "http_error"
    message = response.text
    try:
        payload = ErrorResponse.model_validate(response.json())
    except (ValueError, ValidationError):
        pass  # non-JSON or non-envelope body → fall back to the raw text.
    else:
        error_code = payload.error
        message = payload.message
    raise AetherCalAPIError(response.status_code, error_code, message)


class AetherCalClient:
    """Synchronous client. Use as a context manager to close the underlying connection pool."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=_auth_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> AetherCalClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, translating transport failures into :class:`AetherCalTransportError`.

        Every request the client makes goes through here so a connection/timeout error (which
        ``httpx`` raises as ``httpx.RequestError`` before any response) surfaces as a documented
        SDK error instead of a leaking httpx exception.
        """
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            msg = f"{method} {path} failed before a response: {exc!r}"
            raise AetherCalTransportError(msg) from exc

    def health(self) -> dict[str, Any]:
        """Return the ``/api/v1/health`` payload, or raise :class:`AetherCalAPIError`."""
        response = self._send("GET", _HEALTH_PATH)
        _raise_for_status(response)
        return response.json()

    def ping(self) -> bool:
        """``True`` if the API answers health successfully, ``False`` on any API error."""
        try:
            self.health()
        except AetherCalError:
            return False
        return True

    # -- v1 resource methods (public booking page + admin) ---------------------------------

    def get_branding(self) -> TenantBrandingRead:
        """The authenticated business's branding (``GET /api/v1/branding``) — B-07 / RF-27.

        It takes no argument, and that is the contract: the business is the one the API key belongs
        to, resolved server-side. There is nothing to pass, so there is nothing to point at somebody
        else's brand.
        """
        response = self._send("GET", _BRANDING_PATH)
        _raise_for_status(response)
        return TenantBrandingRead.model_validate(response.json())

    def list_event_types(self) -> list[EventTypeRead]:
        """List the authenticated tenant's bookable event types (``GET /api/v1/event-types``)."""
        response = self._send("GET", _EVENT_TYPES_PATH)
        _raise_for_status(response)
        return [EventTypeRead.model_validate(item) for item in response.json()]

    def get_slots(
        self,
        event_type: uuid.UUID,
        *,
        window_from: date,
        window_to: date,
        tz: str,
    ) -> SlotsResponse:
        """Fetch bookable slots for an event type over ``[window_from, window_to]`` in ``tz``.

        ``tz`` is the IANA display zone echoed back on the response; the slot bounds are UTC.
        """
        response = self._send(
            "GET",
            _SLOTS_PATH,
            params={
                "event_type": str(event_type),
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "tz": tz,
            },
        )
        _raise_for_status(response)
        return SlotsResponse.model_validate(response.json())

    def create_booking(self, booking: BookingCreate) -> BookingRead:
        """Book a slot (``POST /api/v1/bookings``); raises 409 if the time is no longer free."""
        response = self._send("POST", _BOOKINGS_PATH, json=booking.model_dump(mode="json"))
        _raise_for_status(response)
        return _booking_from_api(response.json())

    def cancel_booking(self, booking_id: uuid.UUID, *, token: str) -> BookingRead:
        """Cancel a booking with a signed guest token (``POST /api/v1/bookings/{id}/cancel``)."""
        response = self._send(
            "POST", f"{_BOOKINGS_PATH}{booking_id}/cancel", params={"token": token}
        )
        _raise_for_status(response)
        return _booking_from_api(response.json())

    def reschedule_booking(
        self, booking_id: uuid.UUID, *, new_start: datetime, token: str
    ) -> BookingRead:
        """Reschedule a booking to ``new_start`` with a guest token (409 if the slot is taken)."""
        response = self._send(
            "POST",
            f"{_BOOKINGS_PATH}{booking_id}/reschedule",
            params={"token": token},
            json=BookingReschedule(new_start=new_start).model_dump(mode="json"),
        )
        _raise_for_status(response)
        return _booking_from_api(response.json())


class AsyncAetherCalClient:
    """Asynchronous client. Use as an async context manager to close the connection pool."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=_auth_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> AsyncAetherCalClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _asend(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Async counterpart of :meth:`AetherCalClient._send` — wraps transport failures."""
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            msg = f"{method} {path} failed before a response: {exc!r}"
            raise AetherCalTransportError(msg) from exc

    async def health(self) -> dict[str, Any]:
        """Return the ``/api/v1/health`` payload, or raise :class:`AetherCalAPIError`."""
        response = await self._asend("GET", _HEALTH_PATH)
        _raise_for_status(response)
        return response.json()

    async def ping(self) -> bool:
        """``True`` if the API answers health successfully, ``False`` on any API error."""
        try:
            await self.health()
        except AetherCalError:
            return False
        return True


__all__ = [
    "AetherCalAPIError",
    "AetherCalClient",
    "AetherCalError",
    "AetherCalTransportError",
    "AsyncAetherCalClient",
]
