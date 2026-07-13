"""API-key authentication for the request path (F1-17).

A request authenticates with ``Authorization: Bearer <full_key>``. ``require_api_key`` verifies the
key and returns an :class:`AuthContext` (the tenant + key identity every protected handler needs);
on any failure it raises :class:`AuthenticationError`, which the app maps to a generic ``401``.
The verifier and this dependency never reveal *why* a key was rejected (RF-16).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aethercal.server.deps import get_session
from aethercal.server.services.api_keys import verify_api_key


class AuthenticationError(Exception):
    """Raised when a request presents no valid API key. The caller is never told why (RF-16)."""


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The authenticated identity of a request: which tenant, via which key."""

    tenant_id: uuid.UUID
    api_key_id: uuid.UUID


def bearer_token(request: Request) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header, or ``None``.

    Public because ``GET /metrics`` guards itself with a different secret (the OPERATOR token, never
    a tenant API key) but parses the header exactly the same way. Two hand-rolled copies of this is
    how one of them ends up accepting a header the other rejects."""
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def require_api_key(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    """Authenticate the request by its API key, or raise :class:`AuthenticationError`."""
    presented = bearer_token(request)
    api_key = await verify_api_key(session, presented) if presented is not None else None
    if api_key is None:
        raise AuthenticationError
    return AuthContext(tenant_id=api_key.tenant_id, api_key_id=api_key.id)
