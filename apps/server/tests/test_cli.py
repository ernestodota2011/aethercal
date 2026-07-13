"""Tests for the admin CLI command logic (offline, aiosqlite) — F1-11."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.cli import (
    RevokeKeyOutcome,
    run_connect_google,
    run_create_tenant,
    run_issue_key,
    run_list_keys,
    run_revoke_key,
)
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import ExternalCalendarLink, ExternalConnection
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


async def test_connect_google_can_designate_a_dedicated_booking_calendar(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """The operator surface for the credential rule: bookings land in a DEDICATED secondary
    calendar, never in the connected account's ``primary``. Without ``--calendar-id`` the account
    default is used (zero-config); with it, the link row is written AND flagged as the booking
    target — the row ``resolve_calendar_target`` actually reads."""
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    fernet = Fernet(derive_fernet_key("cli-app-secret"))

    connection_id = await run_connect_google(
        maker,
        tenant_slug="acme",
        user_email="host@acme.test",
        credential=GoogleCredential(account_email="agency@agency.test", token_json='{"t": "a"}'),
        fernet=fernet,
        calendar_id="bookings@group.calendar.google.com",
    )

    async with maker() as session:
        link = (
            await session.scalars(
                select(ExternalCalendarLink).where(
                    ExternalCalendarLink.connection_id == connection_id
                )
            )
        ).one()
        assert link.external_calendar_id == "bookings@group.calendar.google.com"
        assert link.is_booking_target  # the event is written HERE, not into "primary"
        assert link.busy  # and its freebusy blocks slots


async def test_re_running_connect_google_with_the_same_calendar_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Re-connecting (a token refresh, a re-run of the same command) must not pile up link rows or
    trip the one-target-per-connection constraint."""
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    fernet = Fernet(derive_fernet_key("cli-app-secret"))

    for _ in range(2):
        connection_id = await run_connect_google(
            maker,
            tenant_slug="acme",
            user_email="host@acme.test",
            credential=GoogleCredential(
                account_email="agency@agency.test", token_json='{"t": "a"}'
            ),
            fernet=fernet,
            calendar_id="bookings@group.calendar.google.com",
        )

    async with maker() as session:
        links = (
            await session.scalars(
                select(ExternalCalendarLink).where(
                    ExternalCalendarLink.connection_id == connection_id
                )
            )
        ).all()
        assert len(links) == 1


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


# --------------------------------------------------------------------------------------
# `keys list` / `keys revoke` (C7b).
# --------------------------------------------------------------------------------------


async def test_list_keys_returns_the_tenants_keys(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    await run_issue_key(maker, tenant_slug="acme", name="ci-key")
    await run_issue_key(maker, tenant_slug="acme", name="cli-key")

    keys = await run_list_keys(maker, tenant_slug="acme")

    assert {key.name for key in keys} == {"ci-key", "cli-key"}
    # Never leaks the hashed secret through the listing seam — only prefix/id/name/status.
    assert all(not hasattr(key, "full_key") for key in keys)


async def test_list_keys_empty_for_tenant_with_no_keys(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    assert await run_list_keys(maker, tenant_slug="acme") == []


async def test_list_keys_for_unknown_slug_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(LookupError, match="ghost"):
        await run_list_keys(maker, tenant_slug="ghost")


async def test_list_keys_is_tenant_scoped(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    await run_create_tenant(
        maker, slug="other", name="Other Inc", email="host@other.test", timezone="UTC"
    )
    await run_issue_key(maker, tenant_slug="acme", name="ci-key")

    assert await run_list_keys(maker, tenant_slug="other") == []


async def test_revoke_key_marks_it_revoked(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    full_key = await run_issue_key(maker, tenant_slug="acme", name="ci-key")
    (issued,) = await run_list_keys(maker, tenant_slug="acme")

    outcome, prefix = await run_revoke_key(maker, tenant_slug="acme", api_key_id=issued.id)

    assert outcome is RevokeKeyOutcome.REVOKED
    assert prefix == issued.prefix
    async with maker() as session:
        assert await verify_api_key(session, full_key) is None


async def test_revoke_key_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    await run_issue_key(maker, tenant_slug="acme", name="ci-key")
    (issued,) = await run_list_keys(maker, tenant_slug="acme")

    first = await run_revoke_key(maker, tenant_slug="acme", api_key_id=issued.id)
    second = await run_revoke_key(maker, tenant_slug="acme", api_key_id=issued.id)

    assert first == (RevokeKeyOutcome.REVOKED, issued.prefix)
    # Revoking twice does not raise and reports the already-revoked state, not a fresh revoke.
    assert second == (RevokeKeyOutcome.ALREADY_REVOKED, issued.prefix)


async def test_revoke_key_unknown_id_is_reported_not_raised(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    outcome, prefix = await run_revoke_key(maker, tenant_slug="acme", api_key_id=uuid.uuid4())
    assert outcome is RevokeKeyOutcome.NOT_FOUND
    assert prefix is None


async def test_revoke_key_is_tenant_scoped(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="UTC"
    )
    await run_create_tenant(
        maker, slug="other", name="Other Inc", email="host@other.test", timezone="UTC"
    )
    await run_issue_key(maker, tenant_slug="acme", name="ci-key")
    (issued,) = await run_list_keys(maker, tenant_slug="acme")

    # A different tenant's slug cannot revoke acme's key — reported as not-found, not leaked.
    outcome, prefix = await run_revoke_key(maker, tenant_slug="other", api_key_id=issued.id)

    assert outcome is RevokeKeyOutcome.NOT_FOUND
    assert prefix is None


async def test_revoke_key_for_unknown_slug_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(LookupError, match="ghost"):
        await run_revoke_key(maker, tenant_slug="ghost", api_key_id=uuid.uuid4())
