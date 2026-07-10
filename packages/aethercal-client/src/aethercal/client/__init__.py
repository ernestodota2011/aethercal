"""AetherCal SDK: a thin sync + async httpx client for API v1.

Ola 0 ships only the base: construction (``base_url`` + optional ``api_key`` → ``Authorization:
Bearer`` header), a ``health()`` / ``ping()`` method, and error mapping onto
:class:`AetherCalError`. Feature waves add typed methods (event types, schedules, bookings) as they
land; the error envelope they all share is :class:`aethercal.schemas.ErrorResponse`.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from pydantic import ValidationError

from aethercal.schemas import ErrorResponse

DEFAULT_TIMEOUT = 10.0
_HEALTH_PATH = "/api/v1/health"


class AetherCalError(Exception):
    """Base class for every error raised by the client."""


class AetherCalAPIError(AetherCalError):
    """The API returned a non-2xx response. Carries the parsed error envelope."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"{status_code} {error}: {message}")


def _auth_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


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

    def health(self) -> dict[str, Any]:
        """Return the ``/api/v1/health`` payload, or raise :class:`AetherCalAPIError`."""
        response = self._client.get(_HEALTH_PATH)
        _raise_for_status(response)
        return response.json()

    def ping(self) -> bool:
        """``True`` if the API answers health successfully, ``False`` on any API error."""
        try:
            self.health()
        except AetherCalError:
            return False
        return True


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

    async def health(self) -> dict[str, Any]:
        """Return the ``/api/v1/health`` payload, or raise :class:`AetherCalAPIError`."""
        response = await self._client.get(_HEALTH_PATH)
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
    "AsyncAetherCalClient",
]
