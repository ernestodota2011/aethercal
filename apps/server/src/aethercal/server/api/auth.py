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

from aethercal.server.db.guc import bind_tenant
from aethercal.server.deps import get_session
from aethercal.server.services.api_keys import parse_key, verify_api_key
from aethercal.server.services.tenant_resolution import tenant_by_api_key_prefix


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
    """Authenticate the request by its API key, ==and BIND its business to the session.==

    This dependency is where the isolation belt is fastened for the whole request path. The order is
    forced by the bootstrap paradox, and it is not negotiable:

    1. **resolve** the business from the key's prefix, through the ``SECURITY DEFINER`` resolver.
       ``api_keys`` is itself a tenant-scoped table, so under RLS with no GUC that read returns zero
       rows — and every authenticated request in the product would 401. The resolver runs as its
       owner, sees the row, and hands back a bare ``uuid``: nothing else, so nothing leaks.
    2. **bind** the GUC (:func:`~aethercal.server.db.guc.bind_tenant`). From here on, every
       transaction of this request — including the ones a mid-request commit or a post-commit lazy
       load will open later — carries that business, stamped by the ``after_begin`` listener.
    3. **verify** the key by RE-READING the row, now under RLS. The hash comparison and the
       revocation check therefore happen against a row this business is genuinely allowed to see: a
       prefix that somehow resolved to one business while its row belongs to another cannot
       authenticate, because the second read finds nothing.

    Any step failing raises :class:`AuthenticationError`, and the caller is never told which
    (RF-16).
    Binding BEFORE the verification is deliberate: the verification query is itself tenant-scoped,
    so
    without the GUC it would see nothing at all.
    """
    presented = bearer_token(request)
    if presented is None:
        raise AuthenticationError

    parsed = parse_key(presented)
    if parsed is None:
        raise AuthenticationError
    prefix, _secret = parsed

    tenant_id = await tenant_by_api_key_prefix(session, prefix)
    if tenant_id is None:
        raise AuthenticationError

    await bind_tenant(session, tenant_id)

    api_key = await verify_api_key(session, presented)
    if api_key is None:
        raise AuthenticationError
    return AuthContext(tenant_id=api_key.tenant_id, api_key_id=api_key.id)
