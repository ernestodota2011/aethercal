"""The public PAID-booking checkout flow, end-to-end on real PostgreSQL (B-05b, §4.4).

``db``-marked. Only the PUBLIC path creates a hold: a paid event type comes back ``pending`` with a
``checkout_url`` and a PENDING hold + an INTENT payment + an EXPIRE_HOLD queued (so an unpaid hold
self-cancels). A business with no BYOK credential is fail-closed (402, never the instance's own).
A free type still confirms on the spot with no checkout. The provider call is a spy — no real
Stripe.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.app import create_app
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Payment,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.db.roles import DbRole
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import CheckoutSession
from aethercal.server.services.slots import compute_slots
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential
from aethercal.server.settings import Settings

pytestmark = pytest.mark.db

Sessionmaker = async_sessionmaker[AsyncSession]

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}
_TURNSTILE_SECRET = "1x0000000000000000000000000000000AA"
_LOOPBACK_CIDR = "127.0.0.0/8"
_KEY = derive_fernet_key("test-app-secret")
_PRICE = 5000
_CUR = "usd"
_CHECKOUT_URL = "https://checkout.test/cs_test_NOT_A_REAL_KEY"
_PROVIDER_REF = "pi_test_NOT_A_REAL_KEY_x"


class _StubTurnstile:
    VALID = "a-human-solved-this"

    async def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        return token == self.VALID


class _FakeGateway:
    """Records the checkout it was asked to open. NO real Stripe — the money call is a seam."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []

    async def create_checkout_session(
        self,
        *,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime,
        return_url: str,
        secrets: Any,
    ) -> CheckoutSession:
        # The BYOK secret must be the BUSINESS's own.
        assert secrets.get("secret_key", "").startswith("sk_test_")
        # ==Finding 3.== The return URL must be a real base, never a dead placeholder.
        assert return_url and "example.invalid" not in return_url
        self.sessions.append(
            {
                "idempotency_key": idempotency_key,
                "amount_cents": amount_cents,
                "currency": currency,
                "expires_at": expires_at,
                "return_url": return_url,
            }
        )
        return CheckoutSession(checkout_url=_CHECKOUT_URL, provider_ref=_PROVIDER_REF)

    async def refund(
        self,
        *,
        provider: str,
        provider_ref: str,
        amount_cents: int,
        idempotency_key: str,
        secrets: Any,
    ) -> None:
        raise AssertionError("refund is not part of the checkout flow")


@pytest_asyncio.fixture
async def paid_app(
    pg_role_urls: dict[DbRole, str], pg_clean: None
) -> AsyncIterator[tuple[FastAPI, _FakeGateway]]:
    del pg_clean
    settings = Settings(
        database_url=pg_role_urls[DbRole.APP],
        owner_database_url=pg_role_urls[DbRole.OWNER],
        worker_database_url=pg_role_urls[DbRole.WORKER],
        app_secret="test-app-secret",
        public_api_enabled=True,
        turnstile_secret=_TURNSTILE_SECRET,
        trusted_proxies=_LOOPBACK_CIDR,
    )
    application = create_app(settings)
    application.state.turnstile = _StubTurnstile()
    gateway = _FakeGateway()
    application.state.payment_gateway = gateway
    try:
        yield application, gateway
    finally:
        await application.state.engine.dispose()


@pytest_asyncio.fixture
async def paid_client(paid_app: tuple[FastAPI, _FakeGateway]) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=paid_app[0])
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def _seed(
    owner_maker: Sessionmaker,
    *,
    price_cents: int | None = _PRICE,
    with_credential: bool = True,
) -> dict[str, Any]:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=slug, name="Biz")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules=_ALWAYS_OPEN)
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
            price_cents=price_cents,
            currency=_CUR if price_cents is not None else None,
        )
        session.add(event_type)
        await session.flush()
        if with_credential:
            await store_credential(
                session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x", "webhook_secret": "whsec_x"},
                fernet_key=_KEY,
            )
        now = datetime.now(UTC)
        target = (now + timedelta(days=2)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=target,
            window_to=target,
            now=now,
        )
        assert result is not None and result.slots
        return {"slug": slug, "tenant_id": tenant.id, "start": result.slots[0].start.isoformat()}


def _payload(seeded: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": seeded["start"],
        "guest_name": "Ada Lovelace",
        "guest_email": f"ada+{uuid.uuid4().hex[:6]}@example.com",
        "guest_timezone": "UTC",
        "turnstile_token": _StubTurnstile.VALID,
    }


async def _one_payment(owner_maker: Sessionmaker, booking_id: uuid.UUID) -> Payment:
    async with owner_maker() as session:
        return (
            await session.scalars(select(Payment).where(Payment.booking_id == booking_id))
        ).one()


async def _has_expire_hold(owner_maker: Sessionmaker, booking_id: uuid.UUID) -> bool:
    async with owner_maker() as session:
        rows = (
            await session.scalars(
                select(Outbox).where(
                    Outbox.booking_id == booking_id,
                    Outbox.effect == OutboxEffect.EXPIRE_HOLD.value,
                )
            )
        ).all()
        return len(list(rows)) == 1


async def test_a_paid_booking_holds_and_returns_a_checkout_url(
    paid_client: AsyncClient,
    paid_app: tuple[FastAPI, _FakeGateway],
    owner_maker: Sessionmaker,
) -> None:
    """A priced type: PENDING hold + INTENT payment + EXPIRE_HOLD queued + checkout_url; the
    idempotency key is the booking id and the checkout expires with the hold (30 min)."""
    seeded = await _seed(owner_maker)
    _app, gateway = paid_app

    response = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["checkout_url"] == _CHECKOUT_URL
    booking_id = uuid.UUID(body["id"])

    payment = await _one_payment(owner_maker, booking_id)
    assert payment.status is PaymentStatus.INTENT
    assert payment.provider_ref == _PROVIDER_REF
    assert payment.amount_cents == _PRICE
    assert await _has_expire_hold(owner_maker, booking_id)

    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.PENDING
        assert booking.hold_expires_at is not None

    # The checkout was opened with the booking id as the idempotency key + a 30-minute expiry.
    assert len(gateway.sessions) == 1
    assert gateway.sessions[0]["idempotency_key"] == str(booking_id)
    assert gateway.sessions[0]["amount_cents"] == _PRICE


async def test_a_paid_booking_without_a_business_credential_is_fail_closed(
    paid_client: AsyncClient, owner_maker: Sessionmaker
) -> None:
    """==Fail-closed (criterion 41).== No BYOK credential → 402, no hold, no charge — never the
    instance's account."""
    seeded = await _seed(owner_maker, with_credential=False)

    response = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )

    assert response.status_code == 402
    async with owner_maker() as session:
        bookings = (
            await session.scalars(select(Booking).where(Booking.tenant_id == seeded["tenant_id"]))
        ).all()
        assert list(bookings) == [], "no hold is opened when the business cannot charge"


async def test_a_free_booking_confirms_directly_with_no_checkout(
    paid_client: AsyncClient, owner_maker: Sessionmaker
) -> None:
    """A free type (``price_cents`` NULL) still confirms on the spot — no hold, no checkout_url."""
    seeded = await _seed(owner_maker, price_cents=None, with_credential=False)

    response = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["checkout_url"] is None
