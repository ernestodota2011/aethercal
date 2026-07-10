"""Foundational, feature-agnostic API schemas shared by every wave.

Two primitives every endpoint reuses:

* :class:`ErrorResponse` — the single error envelope the API returns for every failure, so clients
  parse one shape. It deliberately carries only a machine ``error`` code and a safe human
  ``message`` (RF-16: an auth failure never leaks *why* it failed).
* :class:`Page` — a generic pagination envelope for list endpoints.

Feature waves add their request/response models in ``schemas/<feature>.py`` and re-export them; this
module stays stable so those additions never collide here.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """The uniform error envelope for every API failure.

    ``error`` is a stable machine-readable code (e.g. ``"unauthorized"``); ``message`` is a short,
    safe, human-readable description that must not disclose internal detail.
    """

    error: str
    message: str


class Page(BaseModel, Generic[T]):
    """A generic page of ``items`` plus the pagination metadata to fetch the next one."""

    items: list[T]
    total: int
    limit: int
    offset: int
