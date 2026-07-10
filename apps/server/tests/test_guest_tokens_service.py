"""Async service tests for guest-token issue / verify / consume on an in-memory session (RF-09).

Two-layer validity (signature + DB row), single-use, purpose-binding, expiry, and
no-leak-on-failure are all exercised here. A minimal real :class:`Booking` backs each token so
``booking_id`` points at an actual row.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import Booking, Tenant
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    consume_guest_token,
    hash_token,
    issue_guest_token,
    verify_guest_token,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

_SECRET = "test-app-secret-for-guest-tokens"


async def _make_booking(session: AsyncSession, tenant_id: uuid.UUID) -> Booking:
    """Insert a minimal confirmed booking and return it (its id backs the token's booking_id)."""
    start = datetime(2030, 1, 1, 15, 0, tzinfo=UTC)
    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(minutes=30),
        guest_name="Ada Guest",
        guest_email="guest@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return booking


async def test_issue_then_verify_roundtrip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(hours=1),
    )

    row = await verify_guest_token(
        sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.CANCEL
    )
    assert row is not None
    assert row.booking_id == booking.id
    assert row.tenant_id == tenant.id
    assert row.purpose == GuestTokenPurpose.CANCEL.value
    assert row.used_at is None


async def test_issue_stores_only_the_hash(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.RESCHEDULE,
        ttl=timedelta(hours=1),
    )
    row = await verify_guest_token(
        sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
    )
    assert row is not None
    # The plaintext token is never stored; only its sha256.
    assert row.token_hash == hash_token(token)
    assert row.token_hash != token


async def test_verify_wrong_purpose_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(hours=1),
    )
    # Issued for CANCEL, presented as RESCHEDULE → rejected.
    assert (
        await verify_guest_token(
            sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
        )
        is None
    )


async def test_verify_expired_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    # Negative ttl → the row's expires_at is already in the past.
    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(seconds=-1),
    )
    assert (
        await verify_guest_token(
            sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.CANCEL
        )
        is None
    )


async def test_verify_tampered_token_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(hours=1),
    )
    middle = len(token) // 2
    tampered = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1 :]
    assert (
        await verify_guest_token(
            sqlite_session, signer, tampered, expected_purpose=GuestTokenPurpose.CANCEL
        )
        is None
    )


async def test_verify_unknown_token_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    # A validly-signed token that was never issued → signature ok, but no DB row exists.
    orphan = signer.sign(booking_id=booking.id, purpose=GuestTokenPurpose.CANCEL, nonce="never")
    assert (
        await verify_guest_token(
            sqlite_session, signer, orphan, expected_purpose=GuestTokenPurpose.CANCEL
        )
        is None
    )


async def test_verify_with_different_secret_returns_none(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    minter = GuestTokenSigner(_SECRET)
    attacker = GuestTokenSigner("a-different-secret")

    token = await issue_guest_token(
        sqlite_session,
        minter,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(hours=1),
    )
    # The row exists, but a signer with the wrong secret cannot validate the signature.
    assert (
        await verify_guest_token(
            sqlite_session, attacker, token, expected_purpose=GuestTokenPurpose.CANCEL
        )
        is None
    )


async def test_consume_marks_used_and_is_single_use(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.RESCHEDULE,
        ttl=timedelta(hours=1),
    )

    consumed = await consume_guest_token(
        sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
    )
    assert consumed is not None
    assert consumed.used_at is not None

    # Single use: after consuming, both verify and a second consume refuse the token.
    assert (
        await verify_guest_token(
            sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
        )
        is None
    )
    assert (
        await consume_guest_token(
            sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
        )
        is None
    )


async def test_consume_rejects_wrong_purpose(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _make_booking(sqlite_session, tenant.id)
    signer = GuestTokenSigner(_SECRET)

    token = await issue_guest_token(
        sqlite_session,
        signer,
        booking_id=booking.id,
        tenant_id=tenant.id,
        purpose=GuestTokenPurpose.CANCEL,
        ttl=timedelta(hours=1),
    )
    # Wrong purpose must not consume the token (it stays usable for its real purpose).
    assert (
        await consume_guest_token(
            sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
        )
        is None
    )
    still_valid = await verify_guest_token(
        sqlite_session, signer, token, expected_purpose=GuestTokenPurpose.CANCEL
    )
    assert still_valid is not None
    assert still_valid.used_at is None
