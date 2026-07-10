"""Tests for the admin CLI command logic (offline, aiosqlite) — F1-11."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.cli import run_connect_google, run_create_tenant, run_issue_key
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import ExternalConnection
from aethercal.server.services.api_keys import verify_api_key
from aethercal.server.services.calendars import GoogleCredential, load_credentials


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_create_tenant_then_issue_key_that_verifies(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id = await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="America/New_York"
    )
    assert tenant_id is not None
    assert user_id is not None

    full_key = await run_issue_key(maker, tenant_slug="acme", name="cli-key")

    # The key issued through the CLI verifies through the service (same hashing path).
    async with maker() as session:
        verified = await verify_api_key(session, full_key)
        assert verified is not None
        assert verified.tenant_id == tenant_id


async def test_issue_key_for_unknown_slug_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(LookupError, match="ghost"):
        await run_issue_key(maker, tenant_slug="ghost", name="x")


async def test_connect_google_stores_an_encrypted_connection(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id = await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    fernet = Fernet(derive_fernet_key("cli-app-secret"))
    token_json = '{"token": "at", "refresh_token": "rt"}'

    connection_id = await run_connect_google(
        maker,
        tenant_slug="acme",
        user_email="host@acme.test",
        credential=GoogleCredential(account_email="host@gmail.com", token_json=token_json),
        fernet=fernet,
    )

    async with maker() as session:
        connection = (
            await session.scalars(
                select(ExternalConnection).where(ExternalConnection.id == connection_id)
            )
        ).one()
        assert connection.tenant_id == tenant_id
        assert connection.user_id == user_id
        assert connection.provider == "google"
        assert connection.encrypted_credentials != token_json.encode("utf-8")
        assert load_credentials(connection, fernet=fernet) == token_json


async def test_connect_google_unknown_tenant_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    fernet = Fernet(derive_fernet_key("cli-app-secret"))
    with pytest.raises(LookupError, match="ghost"):
        await run_connect_google(
            maker,
            tenant_slug="ghost",
            user_email="host@acme.test",
            credential=GoogleCredential(account_email="host@gmail.com", token_json="{}"),
            fernet=fernet,
        )


async def test_connect_google_unknown_user_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    fernet = Fernet(derive_fernet_key("cli-app-secret"))
    with pytest.raises(LookupError, match="nobody@acme"):
        await run_connect_google(
            maker,
            tenant_slug="acme",
            user_email="nobody@acme.test",
            credential=GoogleCredential(account_email="host@gmail.com", token_json="{}"),
            fernet=fernet,
        )
