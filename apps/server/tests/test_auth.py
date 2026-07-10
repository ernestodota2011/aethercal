"""Tests for the API-key auth dependency and its AuthContext (F1-17)."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aethercal.server.api.auth import AuthContext, AuthenticationError, require_api_key
from aethercal.server.db.models import Tenant
from aethercal.server.services.api_keys import issue_api_key

TenantFactory = Callable[..., Awaitable[Tenant]]


def _request(authorization: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


async def test_valid_key_yields_auth_context(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    api_key, full_key = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")

    ctx = await require_api_key(_request(f"Bearer {full_key}"), sqlite_session)
    assert ctx == AuthContext(tenant_id=tenant.id, api_key_id=api_key.id)


async def test_missing_header_is_rejected(sqlite_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError):
        await require_api_key(_request(None), sqlite_session)


async def test_non_bearer_scheme_is_rejected(sqlite_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError):
        await require_api_key(_request("Basic abc123"), sqlite_session)


async def test_invalid_key_is_rejected(sqlite_session: AsyncSession) -> None:
    with pytest.raises(AuthenticationError):
        await require_api_key(_request("Bearer ack_ZZZZZZZZ_nope-nope-nope-nope"), sqlite_session)


def test_auth_context_is_frozen() -> None:
    ctx = AuthContext(tenant_id=uuid.uuid4(), api_key_id=uuid.uuid4())
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.tenant_id = uuid.uuid4()  # type: ignore[misc]
