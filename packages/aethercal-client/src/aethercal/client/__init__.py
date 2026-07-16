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
from aethercal.schemas.event_types import EventTypeRead
from aethercal.schemas.public import (
    PublicBookingCreate,
    PublicBookingRead,
    PublicEventTypeRead,
    PublicSlotsResponse,
)
from aethercal.schemas.slots import SlotsResponse

DEFAULT_TIMEOUT = 10.0
_HEALTH_PATH = "/api/v1/health"
_EVENT_TYPES_PATH = "/api/v1/event-types/"
_SLOTS_PATH = "/api/v1/slots/"
_BOOKINGS_PATH = "/api/v1/bookings/"
_PUBLIC_PATH = "/api/v1/public/"
_FORWARDED_FOR = "X-Forwarded-For"


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
        token: str | None = None,
    ) -> SlotsResponse:
        """Fetch bookable slots for an event type over ``[window_from, window_to]`` in ``tz``.

        ``tz`` is the IANA display zone echoed back on the response; the slot bounds are UTC.

        ``token`` is a guest's signed RESCHEDULE token, and it is what keeps the emailed reschedule
        links working now that the booking page holds no API key. Those links carry a token, a
        booking id and an event-type ID — no business, no slug — so the picker cannot be rendered
        through the public (slug-keyed) route. The token is verified, never consumed.
        """
        params = {
            "event_type": str(event_type),
            "from": window_from.isoformat(),
            "to": window_to.isoformat(),
            "tz": tz,
        }
        if token is not None:
            params["token"] = token
        response = self._send("GET", _SLOTS_PATH, params=params)
        _raise_for_status(response)
        return SlotsResponse.model_validate(response.json())

    def create_booking(self, booking: BookingCreate) -> BookingRead:
        """Book a slot (``POST /api/v1/bookings``); raises 409 if the time is no longer free."""
        response = self._send("POST", _BOOKINGS_PATH, json=booking.model_dump(mode="json"))
        _raise_for_status(response)
        return _booking_from_api(response.json())

    # -- the PUBLIC surface: no API key, and the business named in the ROUTE -----------------
    #
    # ==This is what the booking page uses now, and the page holds no key at all.== It used to carry
    # one with the tenant's FULL permissions, in the most exposed process in the system, and it
    # could
    # therefore serve exactly one business — because a key names exactly one. The key is not
    # mitigated here: it is DELETED, and the business travels in the path instead.
    #
    # `forwarded_for` is the GUEST's real address, which the page has already resolved through its
    # own trusted-proxy contract. The API believes it only when the page's own address sits inside
    # the API's `AETHERCAL_TRUSTED_PROXIES` — a hop in a declared chain, never a header anybody may
    # assert. Without it the API would see the PAGE's address for every guest on earth: one
    # rate-limit bucket for all of them, one address stamped on every booking, and service denied to
    # everybody the moment the per-IP cap was reached. A silent, self-inflicted outage.

    def list_public_event_types(self, tenant_slug: str) -> list[PublicEventTypeRead]:
        """The business's bookable services — ``GET /api/v1/public/{tenant_slug}/event-types``."""
        response = self._send("GET", f"{_PUBLIC_PATH}{tenant_slug}/event-types")
        _raise_for_status(response)
        return [PublicEventTypeRead.model_validate(item) for item in response.json()]

    def get_public_slots(
        self,
        tenant_slug: str,
        event_slug: str,
        *,
        window_from: date,
        window_to: date,
        tz: str,
    ) -> PublicSlotsResponse:
        """Bookable slots for one public event type over ``[window_from, window_to]`` in ``tz``."""
        response = self._send(
            "GET",
            f"{_PUBLIC_PATH}{tenant_slug}/{event_slug}/slots",
            params={"from": window_from.isoformat(), "to": window_to.isoformat(), "tz": tz},
        )
        _raise_for_status(response)
        return PublicSlotsResponse.model_validate(response.json())

    def create_public_booking(
        self,
        tenant_slug: str,
        event_slug: str,
        booking: PublicBookingCreate,
        *,
        forwarded_for: str | None = None,
    ) -> PublicBookingRead:
        """Book a slot with NO API key (409 if the time is taken; 403 if the captcha was not
        passed).

        The answer is four fields — ``{id, start, end, status}`` — and deliberately NOT
        ``BookingRead``: that model carries the guest's name, e-mail, notes and answers, and echoing
        it out of an endpoint that asked for no credentials would turn a booking id into an oracle
        for a stranger's personal data.
        """
        headers = {_FORWARDED_FOR: forwarded_for} if forwarded_for else None
        response = self._send(
            "POST",
            f"{_PUBLIC_PATH}{tenant_slug}/{event_slug}/bookings",
            json=booking.model_dump(mode="json"),
            headers=headers,
        )
        _raise_for_status(response)
        return PublicBookingRead.model_validate(response.json())

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
