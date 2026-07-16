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
# ==Finding 1.== The gateway hands back the Checkout Session id (the creation-time anchor), NOT the
# PaymentIntent — the intent does not exist yet when the session is opened.
_SESSION_ID = "cs_test_NOT_A_REAL_KEY_x"


class _StubTurnstile:
    VALID = "a-human-solved-this"

    async def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        return token == self.VALID


class _FakeGateway:
    """Records the checkout it was asked to open. NO real Stripe — the money call is a seam.

    ``fail`` makes the provider call RAISE (finding 2), to exercise the 502-with-booking-id path and
    the resume endpoint that recovers from it."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.fail = False

    async def create_checkout_session(  # noqa: PLR0913 - mirrors the gateway contract
        self,
        *,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime,
        return_url: str,
        secrets: Any,
    ) -> CheckoutSession:
        if self.fail:
            # A provider I/O failure — raised BEFORE recording, so a failed attempt opens nothing.
            raise RuntimeError("stripe is unreachable")
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
        return CheckoutSession(checkout_url=_CHECKOUT_URL, checkout_session_id=_SESSION_ID)

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
    idempotency key is the booking id, and the Stripe expiry is ≥ 30 min with buffer while the hold
    OUTLIVES the session (finding 2)."""
    seeded = await _seed(owner_maker)
    _app, gateway = paid_app

    before = datetime.now(UTC)
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
    # ==Finding 1.== The row is anchored on the Checkout Session id and ``provider_ref`` is NULL
    # (the intent does not exist yet), never the string "None" that once made the arbiter lose it.
    assert payment.checkout_session_id == _SESSION_ID
    assert payment.provider_ref is None
    assert payment.amount_cents == _PRICE
    assert await _has_expire_hold(owner_maker, booking_id)

    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.PENDING
        assert booking.hold_expires_at is not None
        hold_expires_at = booking.hold_expires_at

    # The checkout was opened with the booking id as the idempotency key.
    assert len(gateway.sessions) == 1
    assert gateway.sessions[0]["idempotency_key"] == str(booking_id)

    # ==Finding 2.== The Stripe expiry is ≥ 30 min out even measured from BEFORE the request (so it
    # survives network latency), and the hold OUTLIVES the session (no pay-against-freed-slot gap).
    stripe_expires_at = gateway.sessions[0]["expires_at"]
    assert stripe_expires_at >= before + timedelta(minutes=30), (
        "Stripe's 30-min minimum, with buffer"
    )
    assert hold_expires_at > stripe_expires_at, "the hold must outlive the checkout session"
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


async def _payments_for(owner_maker: Sessionmaker, booking_id: uuid.UUID) -> list[Payment]:
    async with owner_maker() as session:
        return list(
            (await session.scalars(select(Payment).where(Payment.booking_id == booking_id))).all()
        )


async def test_a_failed_checkout_returns_502_with_booking_id_and_keeps_the_hold(
    paid_client: AsyncClient, paid_app: tuple[FastAPI, _FakeGateway], owner_maker: Sessionmaker
) -> None:
    """==Finding 2.== When the provider call fails, the guest gets a 502 carrying the ``booking_id``
    (to resume) and the hold stays PENDING with its EXPIRE_HOLD queued — never an opaque 500 that
    locks the slot for the whole TTL. No Payment row is written: the checkout never opened."""
    seeded = await _seed(owner_maker)
    _app, gateway = paid_app
    gateway.fail = True

    response = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "checkout_unavailable"
    booking_id = uuid.UUID(detail["booking_id"])

    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.PENDING
    assert await _has_expire_hold(owner_maker, booking_id)
    assert await _payments_for(owner_maker, booking_id) == [], "the checkout never opened"


async def test_a_resumed_checkout_reopens_the_same_session_and_records_the_payment(
    paid_client: AsyncClient, paid_app: tuple[FastAPI, _FakeGateway], owner_maker: Sessionmaker
) -> None:
    """==Finding 2.== After a failed create leaves a PENDING hold, ``POST
    .../bookings/{id}/checkout`` re-opens the checkout (SAME booking-id Idempotency-Key) and returns
    the checkout_url; the INTENT Payment row is written. A SECOND resume is idempotent — same
    session, still ONE Payment row."""
    seeded = await _seed(owner_maker)
    _app, gateway = paid_app
    gateway.fail = True
    failed = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )
    booking_id = uuid.UUID(failed.json()["detail"]["booking_id"])
    assert gateway.sessions == [], "the failed attempt opened nothing"

    gateway.fail = False
    resumed = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/bookings/{booking_id}/checkout"
    )

    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["checkout_url"] == _CHECKOUT_URL
    assert body["status"] == "pending"
    # The resume used the booking id as the idempotency key → the SAME session, never a 2nd charge.
    assert gateway.sessions[-1]["idempotency_key"] == str(booking_id)
    payments = await _payments_for(owner_maker, booking_id)
    assert len(payments) == 1
    assert payments[0].status is PaymentStatus.INTENT
    assert payments[0].checkout_session_id is not None
    assert payments[0].provider_ref is None

    # A second resume is idempotent: same session returned, no duplicate Payment row.
    again = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/bookings/{booking_id}/checkout"
    )
    assert again.status_code == 200, again.text
    assert again.json()["checkout_url"] == _CHECKOUT_URL
    assert len(await _payments_for(owner_maker, booking_id)) == 1, "resume must not double-insert"


async def test_resume_is_refused_for_a_non_live_hold(
    paid_client: AsyncClient, paid_app: tuple[FastAPI, _FakeGateway], owner_maker: Sessionmaker
) -> None:
    """A hold that is no longer live cannot be resumed: a CONFIRMED booking is 409 (no second
    charge), an unknown booking is the shared 404 — and neither opens a checkout."""
    seeded = await _seed(owner_maker)
    _app, gateway = paid_app

    # A confirmed booking: create the hold, then mark it CONFIRMED out of band.
    created = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/intro/bookings", json=_payload(seeded)
    )
    booking_id = uuid.UUID(created.json()["id"])
    async with owner_maker() as session, session.begin():
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        booking.status = BookingStatus.CONFIRMED
    opened_before = len(gateway.sessions)

    confirmed_resume = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/bookings/{booking_id}/checkout"
    )
    assert confirmed_resume.status_code == 409, confirmed_resume.text
    assert confirmed_resume.json()["detail"]["error"] == "hold_not_resumable"

    # An unknown booking id → the shared 404.
    unknown = await paid_client.post(
        f"/api/v1/public/{seeded['slug']}/bookings/{uuid.uuid4()}/checkout"
    )
    assert unknown.status_code == 404

    assert len(gateway.sessions) == opened_before, "a refused resume opens no checkout"
