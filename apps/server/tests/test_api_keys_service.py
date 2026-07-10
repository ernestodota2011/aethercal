"""Async service tests for API-key issue / verify / revoke against an in-memory session (F1-17)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import Tenant
from aethercal.server.services.api_keys import (
    issue_api_key,
    list_api_keys,
    parse_key,
    revoke_api_key,
    verify_api_key,
)

TenantFactory = Callable[..., Awaitable[Tenant]]


async def test_issue_then_verify_roundtrip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    api_key, full_key = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")

    verified = await verify_api_key(sqlite_session, full_key)
    assert verified is not None
    assert verified.id == api_key.id
    assert verified.tenant_id == tenant.id


async def test_verify_stamps_last_used_at(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    api_key, full_key = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")
    assert api_key.last_used_at is None

    verified = await verify_api_key(sqlite_session, full_key)
    assert verified is not None
    assert verified.last_used_at is not None


async def test_wrong_secret_is_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    _, full_key = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")
    parsed = parse_key(full_key)
    assert parsed is not None
    prefix, _secret = parsed

    tampered = f"ack_{prefix}_thisisnotthesecretbutlongenoughxxxxxxxxxxxx"
    assert await verify_api_key(sqlite_session, tampered) is None


async def test_unknown_prefix_is_rejected(sqlite_session: AsyncSession) -> None:
    well_formed_but_unknown = "ack_ZZZZZZZZ_secretsecretsecretsecretsecretsecret42"
    assert await verify_api_key(sqlite_session, well_formed_but_unknown) is None


async def test_malformed_key_is_rejected(sqlite_session: AsyncSession) -> None:
    assert await verify_api_key(sqlite_session, "not-a-key") is None
    assert await verify_api_key(sqlite_session, "") is None


async def test_revoked_key_is_rejected(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    api_key, full_key = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")

    revoked = await revoke_api_key(sqlite_session, api_key_id=api_key.id, tenant_id=tenant.id)
    assert revoked is True
    assert await verify_api_key(sqlite_session, full_key) is None


async def test_revoke_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    other = await tenant_factory(sqlite_session, slug="other")
    api_key, full_key = await issue_api_key(sqlite_session, tenant_id=owner.id, name="ci")

    # A different tenant cannot revoke this key.
    revoked = await revoke_api_key(sqlite_session, api_key_id=api_key.id, tenant_id=other.id)
    assert revoked is False
    assert await verify_api_key(sqlite_session, full_key) is not None


async def test_list_api_keys_returns_all_of_a_tenants_keys(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    first, _ = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")
    second, _ = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="cli")

    keys = await list_api_keys(sqlite_session, tenant_id=tenant.id)

    assert {key.id for key in keys} == {first.id, second.id}


async def test_list_api_keys_includes_revoked_keys(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    api_key, _ = await issue_api_key(sqlite_session, tenant_id=tenant.id, name="ci")
    await revoke_api_key(sqlite_session, api_key_id=api_key.id, tenant_id=tenant.id)

    keys = await list_api_keys(sqlite_session, tenant_id=tenant.id)

    assert len(keys) == 1
    assert keys[0].revoked_at is not None


async def test_list_api_keys_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    other = await tenant_factory(sqlite_session, slug="other")
    await issue_api_key(sqlite_session, tenant_id=owner.id, name="ci")

    assert await list_api_keys(sqlite_session, tenant_id=other.id) == []


async def test_list_api_keys_empty_for_tenant_with_no_keys(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    assert await list_api_keys(sqlite_session, tenant_id=tenant.id) == []
