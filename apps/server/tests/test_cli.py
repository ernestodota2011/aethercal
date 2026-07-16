"""Tests for the admin CLI command logic (offline, aiosqlite) — F1-11."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.server.cli import (
    ReplayOutcome,
    RevokeKeyOutcome,
    run_connect_google,
    run_create_tenant,
    run_issue_key,
    run_list_dead_intents,
    run_list_keys,
    run_replay_intent,
    run_revoke_key,
)
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import (
    Booking,
    EventType,
    ExternalCalendarLink,
    ExternalConnection,
    Outbox,
    OutboxStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.api_keys import verify_api_key
from aethercal.server.services.calendars import GoogleCredential, load_credentials
from aethercal.server.services.outbox import OutboxEffect


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


# --------------------------------------------------------------------------------------
# `aethercal-admin outbox replay` (R9) — reviving a dead intent without opening psql.
#
# A dead intent is a message that was never delivered: six attempts, then parked. Until now the
# only way to give it another chance was to UPDATE the table by hand — which is also the only way
# to accidentally "replay" a DELIVERED one and mail a guest twice. So the command is a CONDITIONAL
# update gated on `status = 'dead'`, arbitrated by rowcount, and it says which of the three things
# happened.
# --------------------------------------------------------------------------------------


async def _seed_intent(
    maker: async_sessionmaker[AsyncSession], *, status: OutboxStatus, attempts: int = 6
) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant with one booking and one outbox intent in ``status``. Returns (tenant, intent)."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Acme")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@acme.test", name="H", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()
        start = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=BookingStatus.CONFIRMED,
            # Confirmed ⇒ stamped: after B-05a a confirmed booking always carries when it became so.
            confirmed_at=start - timedelta(days=1),
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        intent = Outbox(
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=OutboxEffect.EMAIL.value,
            dedupe_key="email:confirmation",
            payload={"kind": "confirmation"},
            status=status.value,
            attempts=attempts,
            next_retry_at=None,
            claimed_by="worker-1" if status is OutboxStatus.CLAIMED else None,
        )
        session.add(intent)
        await session.flush()
        return tenant.id, intent.id


async def test_replay_revives_a_dead_intent_and_clears_its_attempts(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Back to ``pending`` and DUE, with the attempt count reset.

    Reviving it with ``attempts`` still at six would put it one failure from the dead-letter again,
    so the very next transient blip would re-park it — a replay that looks like a replay and buys
    the operator nothing."""
    _, intent_id = await _seed_intent(maker, status=OutboxStatus.DEAD)

    outcome = await run_replay_intent(maker, intent_id=intent_id)

    assert outcome is ReplayOutcome.REVIVED
    async with maker() as session:
        row = await session.get(Outbox, intent_id)
        assert row is not None
        assert row.status == OutboxStatus.PENDING.value
        assert row.attempts == 0
        assert row.next_retry_at is None  # due at the next drain
        assert row.claimed_by is None
        assert row.lease_expires_at is None


@pytest.mark.parametrize(
    "status",
    [
        OutboxStatus.DELIVERED,
        OutboxStatus.PENDING,
        OutboxStatus.CLAIMED,
        OutboxStatus.SKIPPED,
        OutboxStatus.VOIDED,
        OutboxStatus.FAILED,
    ],
)
async def test_replay_refuses_anything_that_is_not_dead(
    maker: async_sessionmaker[AsyncSession], status: OutboxStatus
) -> None:
    """==The guard that makes this command safe to hand to a tired operator at 3am.==

    "Replaying" a DELIVERED intent re-sends a message the guest already has. "Replaying" a CLAIMED
    one yanks a row out from under the worker that is sending it right now. A `failed` one is not
    stuck at all — it is already scheduled to retry. Only `dead` is genuinely parked, so only `dead`
    is revivable, and the refusal is a rowcount, not a read-then-write that a concurrent drain could
    slip between."""
    _, intent_id = await _seed_intent(maker, status=status)

    outcome = await run_replay_intent(maker, intent_id=intent_id)

    assert outcome is ReplayOutcome.NOT_DEAD
    async with maker() as session:
        row = await session.get(Outbox, intent_id)
        assert row is not None
        assert row.status == status.value  # untouched


async def test_replay_of_an_unknown_intent_reports_not_found(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    outcome = await run_replay_intent(maker, intent_id=uuid.uuid4())

    assert outcome is ReplayOutcome.NOT_FOUND


async def test_replaying_twice_is_refused_the_second_time(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """The first replay made it ``pending``; a second must not reset a row that is now live and may
    already be in a worker's hands."""
    _, intent_id = await _seed_intent(maker, status=OutboxStatus.DEAD)
    await run_replay_intent(maker, intent_id=intent_id)

    again = await run_replay_intent(maker, intent_id=intent_id)

    assert again is ReplayOutcome.NOT_DEAD


async def test_listing_dead_intents_finds_them_without_touching_the_database_by_hand(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """A replay command you cannot feed an id to is half a fix: finding the id was the very thing
    that meant opening psql. The listing carries no guest data — id, effect, attempts, booking."""
    _, dead_id = await _seed_intent(maker, status=OutboxStatus.DEAD)
    await _seed_intent(maker, status=OutboxStatus.DELIVERED)

    rows = await run_list_dead_intents(maker)

    assert [row.id for row in rows] == [dead_id]
