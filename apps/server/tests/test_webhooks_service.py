"""Service tests: tenant-scoped CRUD, secret-at-rest encryption, and event fan-out (RF-17).

All run against the offline in-memory ``sqlite_session`` (no Postgres).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.webhooks import WebhookCreate, WebhookUpdate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.services.webhooks import (
    create_webhook,
    decrypt_webhook_secret,
    delete_webhook,
    enqueue_event,
    get_webhook,
    list_webhooks,
    update_webhook,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("test-app-secret")
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def _create(url: str, events: list[str], secret: str | None = None) -> WebhookCreate:
    return WebhookCreate.model_validate({"url": url, "events": events, "secret": secret})


async def test_create_returns_webhook_and_plaintext_secret(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    webhook, secret = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://consumer.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    assert webhook.tenant_id == tenant.id
    assert webhook.url == "https://consumer.test/hook"
    assert webhook.active is True
    assert secret  # a generated, non-empty secret is returned to the caller


async def test_supplied_secret_is_honored(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    _, secret = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://consumer.test/hook", ["booking.created"], "my-own-secret"),
        fernet_key=KEY,
    )
    assert secret == "my-own-secret"


async def test_secret_is_encrypted_at_rest_and_round_trips(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    webhook, secret = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://consumer.test/hook", ["booking.created"], "plaintext-secret"),
        fernet_key=KEY,
    )
    # Stored bytes must NOT be the plaintext.
    assert webhook.secret != b"plaintext-secret"
    assert b"plaintext-secret" not in webhook.secret
    # But they decrypt back to the plaintext.
    assert decrypt_webhook_secret(webhook, KEY) == secret.encode("utf-8")


async def test_list_and_get_are_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    other = await tenant_factory(sqlite_session, slug="other")
    webhook, _ = await create_webhook(
        sqlite_session,
        tenant_id=owner.id,
        params=_create("https://consumer.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    assert [w.id for w in await list_webhooks(sqlite_session, tenant_id=owner.id)] == [webhook.id]
    assert await list_webhooks(sqlite_session, tenant_id=other.id) == []
    assert await get_webhook(sqlite_session, tenant_id=owner.id, webhook_id=webhook.id) is not None
    # The other tenant cannot read it.
    assert await get_webhook(sqlite_session, tenant_id=other.id, webhook_id=webhook.id) is None


async def test_update_toggles_active_and_changes_events_and_url(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    webhook, _ = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://old.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    updated = await update_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        webhook_id=webhook.id,
        changes=WebhookUpdate(
            url="https://new.test/hook",
            events=["booking.cancelled", "booking.rescheduled"],
            active=False,
        ),
    )
    assert updated is not None
    assert updated.url == "https://new.test/hook"
    assert updated.events == ["booking.cancelled", "booking.rescheduled"]
    assert updated.active is False


async def test_update_leaves_unset_fields_unchanged(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    webhook, _ = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://keep.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    updated = await update_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        webhook_id=webhook.id,
        changes=WebhookUpdate(active=False),
    )
    assert updated is not None
    assert updated.active is False
    assert updated.url == "https://keep.test/hook"  # untouched
    assert updated.events == ["booking.created"]  # untouched


async def test_update_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    other = await tenant_factory(sqlite_session, slug="other")
    webhook, _ = await create_webhook(
        sqlite_session,
        tenant_id=owner.id,
        params=_create("https://consumer.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    assert (
        await update_webhook(
            sqlite_session,
            tenant_id=other.id,
            webhook_id=webhook.id,
            changes=WebhookUpdate(active=False),
        )
        is None
    )


async def test_delete_is_tenant_scoped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    other = await tenant_factory(sqlite_session, slug="other")
    webhook, _ = await create_webhook(
        sqlite_session,
        tenant_id=owner.id,
        params=_create("https://consumer.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    assert await delete_webhook(sqlite_session, tenant_id=other.id, webhook_id=webhook.id) is False
    assert await delete_webhook(sqlite_session, tenant_id=owner.id, webhook_id=webhook.id) is True
    assert await get_webhook(sqlite_session, tenant_id=owner.id, webhook_id=webhook.id) is None


async def test_enqueue_fans_out_only_to_matching_active_subscribers(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session, slug="t1")
    other_tenant = await tenant_factory(sqlite_session, slug="t2")

    matching, _ = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://a.test/hook", ["booking.created", "booking.cancelled"]),
        fernet_key=KEY,
    )
    # Subscribed to a different event → no delivery.
    await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://b.test/hook", ["booking.cancelled"]),
        fernet_key=KEY,
    )
    # Subscribed but inactive → no delivery.
    inactive, _ = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://c.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )
    await update_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        webhook_id=inactive.id,
        changes=WebhookUpdate(active=False),
    )
    # Another tenant subscribed to the same event → isolation, no delivery.
    await create_webhook(
        sqlite_session,
        tenant_id=other_tenant.id,
        params=_create("https://d.test/hook", ["booking.created"]),
        fernet_key=KEY,
    )

    deliveries = await enqueue_event(
        sqlite_session,
        tenant_id=tenant.id,
        event="booking.created",
        data={"booking_id": "bk_1"},
        now=NOW,
    )

    assert [d.webhook_id for d in deliveries] == [matching.id]
    delivery = deliveries[0]
    assert delivery.status == "pending"
    assert delivery.attempts == 0
    assert delivery.tenant_id == tenant.id
    assert delivery.payload == {
        "event": "booking.created",
        "api_version": "1",
        "timestamp": NOW.isoformat(),
        "data": {"booking_id": "bk_1"},
    }


async def test_enqueue_with_no_matching_subscribers_returns_empty(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=_create("https://a.test/hook", ["booking.cancelled"]),
        fernet_key=KEY,
    )
    deliveries = await enqueue_event(
        sqlite_session,
        tenant_id=tenant.id,
        event="booking.created",
        data={},
        now=NOW,
    )
    assert deliveries == []
