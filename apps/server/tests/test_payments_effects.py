"""Offline tests for the money EFFECT runners — REFUND and EXPIRE_HOLD (B-05b).

The runners are what turn the queued intents into real actions: the refund calls the provider on the
business's OWN account (BYOK, fail-closed) and is idempotent by a status re-check; the hold-expiry
cancels an unpaid hold with a single conditional UPDATE and no external I/O. These run on in-memory
SQLite with a spy gateway.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Booking, Payment, PaymentStatus, Schedule, Tenant, User
from aethercal.server.services.outbox import OutboxEffect, OutboxWork, refund_dedupe_key
from aethercal.server.services.payments import (
    build_money_runners,
    make_expire_hold_runner,
    make_refund_runner,
)
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    MissingCredentialError,
    store_credential,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_KEY = derive_fernet_key("test-app-secret")
_REF = "pi_test_NOT_A_REAL_KEY_A"


class _GatewaySpy:
    """A refund gateway that models the PROVIDER's own idempotency (Stripe's ``Idempotency-Key``).

    Every invocation is recorded, but a repeat of an ``idempotency_key`` already seen is a NO-OP at
    the provider — the money moved once. So ``calls`` counts invocations (what the runner did) and
    ``net_refunds`` counts DISTINCT keys (what the provider actually paid back). That gap is the
    point of finding 1: the runner may fire twice after a lost commit, the provider refunds once.
    """

    def __init__(self) -> None:
        self.refunds: list[str] = []
        self.keys: list[str] = []

    @property
    def checkout_session_floor(self) -> timedelta:
        return timedelta(minutes=30)

    async def refund(
        self, *, provider_ref: str, idempotency_key: str, secrets: Mapping[str, str]
    ) -> None:
        # The BYOK secret must be the BUSINESS's own, never the instance's.
        assert secrets.get("secret_key", "").startswith("sk_test_")
        self.keys.append(idempotency_key)
        self.refunds.append(provider_ref)

    @property
    def calls(self) -> int:
        """How many times the runner invoked ``refund`` (idempotent repeats included)."""
        return len(self.keys)

    @property
    def net_refunds(self) -> int:
        """DISTINCT idempotency keys — what the provider actually paid back (Stripe dedupes)."""
        return len(set(self.keys))


async def _tenant(session: AsyncSession) -> uuid.UUID:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    session.add(User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC"))
    session.add(Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={}))
    await session.flush()
    return tenant.id


async def _booking(
    session: AsyncSession, tenant_id: uuid.UUID, *, status: BookingStatus
) -> Booking:
    # A minimal booking (no event type needed for these effect tests).
    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=uuid.uuid4(),
        start_at=_SLOT,
        end_at=_SLOT + timedelta(minutes=30),
        status=status,
        confirmed_at=None if status is BookingStatus.PENDING else NOW,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return booking


async def _stripe_credential(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await store_credential(
        session,
        tenant_id=tenant_id,
        provider=CredentialProvider.STRIPE,
        secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x", "webhook_secret": "whsec_test_x"},
        fernet_key=_KEY,
    )


def _refund_work(tenant_id: uuid.UUID, booking_id: uuid.UUID) -> OutboxWork:
    return OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.REFUND,
        dedupe_key=refund_dedupe_key(_REF),
        payload={"provider": "stripe", "provider_ref": _REF},
        attempts=0,
        claimed_by="worker-1",
    )


def _expire_work(tenant_id: uuid.UUID, booking_id: uuid.UUID) -> OutboxWork:
    return OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.EXPIRE_HOLD,
        dedupe_key=f"expire_hold:{booking_id}",
        payload={"booking_id": str(booking_id)},
        attempts=0,
        claimed_by="worker-1",
    )


async def test_the_refund_runner_refunds_on_the_business_account_and_marks_refunded(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Happy path: the provider is called with the BUSINESS's own secret, and the row flips to
    refunded."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        payment = Payment(
            tenant_id=tenant_id,
            booking_id=booking.id,
            provider="stripe",
            provider_ref=_REF,
            status=PaymentStatus.PAID,
            amount_cents=5000,
            currency="usd",
        )
        s.add(payment)
        await _stripe_credential(s, tenant_id)
        booking_id, payment_id = booking.id, payment.id

    gateway = _GatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[_KEY]
    )
    await runner(_refund_work(tenant_id, booking_id), NOW)

    # ==The charge alone.== The gateway is no longer TOLD its provider or the amount: it is SELECTED
    # by the intent's provider (the map key), and it only ever refunds in full. Both parameters used
    # to be passed and immediately discarded by every implementation — see PaymentGateway.refund.
    assert gateway.refunds == [_REF]
    async with sqlite_maker() as s:
        refreshed = await s.get(Payment, payment_id)
        assert refreshed is not None
        assert refreshed.status is PaymentStatus.REFUNDED


async def test_the_refund_runner_is_idempotent_on_an_already_refunded_payment(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Criterion 30 belt.== A second run (a duplicate row, an at-least-once re-drain) re-reads the
    status and does NOT call the provider again — the money goes back exactly once."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        s.add(
            Payment(
                tenant_id=tenant_id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=_REF,
                status=PaymentStatus.REFUNDED,  # already done
                amount_cents=5000,
                currency="usd",
            )
        )
        await _stripe_credential(s, tenant_id)
        booking_id = booking.id

    gateway = _GatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[_KEY]
    )
    await runner(_refund_work(tenant_id, booking_id), NOW)

    assert gateway.refunds == [], "an already-refunded payment must not be refunded again"


async def test_the_refund_runner_is_fail_closed_without_a_business_credential(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==BYOK, criterion 41.== With no business credential the refund RAISES rather than falling
    back to the instance's account — the drain then retries/dead-letters it, loudly."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        s.add(
            Payment(
                tenant_id=tenant_id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=_REF,
                status=PaymentStatus.PAID,
                amount_cents=5000,
                currency="usd",
            )
        )
        # NO credential stored.
        booking_id = booking.id

    gateway = _GatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[_KEY]
    )
    with pytest.raises(MissingCredentialError):
        await runner(_refund_work(tenant_id, booking_id), NOW)
    assert gateway.refunds == [], "no charge is refunded without the business's own account"


async def test_the_refund_is_provider_idempotent_across_a_lost_commit(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Finding 1 (the double-refund window).== If the process dies AFTER Stripe refunds but BEFORE
    the ``status = refunded`` commit lands, the next drain re-runs the REFUND — the status re-check
    (1st line of defence) does NOT help, because it never committed. The real guarantee lives at the
    PROVIDER: the refund call carries a deterministic ``Idempotency-Key`` (refund:provider_ref),
    so a re-run hits the SAME key and Stripe returns the SAME refund, not a second one.

    Here the runner fires TWICE (a PAID payment both times — the commit was lost), and the provider
    nets ONE refund because both calls carried the same key."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        s.add(
            Payment(
                tenant_id=tenant_id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=_REF,
                status=PaymentStatus.PAID,
                amount_cents=5000,
                currency="usd",
            )
        )
        await _stripe_credential(s, tenant_id)
        booking_id, payment_id = (
            booking.id,
            (await s.scalars(select(Payment).where(Payment.booking_id == booking.id))).one().id,
        )

    gateway = _GatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[_KEY]
    )

    # First run: the provider refunds, and the runner marks the payment refunded (committed).
    await runner(_refund_work(tenant_id, booking_id), NOW)
    # ==Simulate the LOST COMMIT== — the status write never landed, so the row is still PAID.
    async with sqlite_maker() as s, s.begin():
        payment = await s.get(Payment, payment_id)
        assert payment is not None
        payment.status = PaymentStatus.PAID
    # Second run: the status re-check does NOT save us (it reads PAID), so the runner calls the
    # provider again — but with the SAME idempotency key, so the provider nets one refund.
    await runner(_refund_work(tenant_id, booking_id), NOW)

    assert gateway.calls == 2, (
        "the runner fired twice (the lost commit defeated the status re-check)"
    )
    assert gateway.net_refunds == 1, "the provider refunded ONCE — idempotent on the stable key"
    assert set(gateway.keys) == {f"refund:{_REF}"}, "the key is deterministic across retries"


async def test_the_expire_hold_runner_cancels_a_pending_hold_and_frees_the_slot(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A hold whose TTL passed is cancelled — the slot re-opens (status <> cancelled index) and,
    because it was never confirmed, nothing is announced."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.PENDING)
        booking_id = booking.id

    runner = make_expire_hold_runner(sessionmaker=sqlite_maker)
    await runner(_expire_work(tenant_id, booking_id), NOW)

    async with sqlite_maker() as s:
        refreshed = await s.get(Booking, booking_id)
        assert refreshed is not None
        assert refreshed.status is BookingStatus.CANCELLED
        assert refreshed.cancelled_at is not None
        assert refreshed.confirmed_at is None, "an unpaid hold is never confirmed on the way out"


async def test_the_expire_hold_runner_is_a_no_op_once_the_payment_won(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The payment confirmed the booking first: the conditional cancel matches zero rows and leaves
    the confirmed booking untouched (the hold/confirm race, resolved by the row lock)."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CONFIRMED)
        booking_id = booking.id

    runner = make_expire_hold_runner(sessionmaker=sqlite_maker)
    await runner(_expire_work(tenant_id, booking_id), NOW)

    async with sqlite_maker() as s:
        refreshed = await s.get(Booking, booking_id)
        assert refreshed is not None
        assert refreshed.status is BookingStatus.CONFIRMED, "a confirmed booking is not expired"


def test_the_money_runners_are_fail_closed_without_keys_or_gateway(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Finding 2.== The drain's money-runner wiring reads ``fernet_keys``/``payment_gateway`` off
    app state; a missing one must FAIL-CLOSED, not crash. The REFUND runner needs BOTH the BYOK
    gateway and the rotation keys — without either it is ``None`` (a REFUND intent then raises
    at dispatch, never a None-key decrypt or an AttributeError). EXPIRE_HOLD needs neither, so it is
    always built."""
    gateway = _GatewaySpy()

    # Both present → the refund runner is wired.
    refund, expire = build_money_runners(
        exec_maker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[_KEY]
    )
    assert refund is not None
    assert expire is not None

    # No rotation keys → no refund runner (fail-closed), but EXPIRE_HOLD still runs.
    refund_no_keys, expire_no_keys = build_money_runners(
        exec_maker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=None
    )
    assert refund_no_keys is None
    assert expire_no_keys is not None

    # Empty key tuple is also fail-closed.
    refund_empty, _ = build_money_runners(
        exec_maker=sqlite_maker, gateways={"stripe": gateway}, fernet_keys=[]
    )
    assert refund_empty is None

    # No gateway → no refund runner.
    refund_no_gw, expire_no_gw = build_money_runners(
        exec_maker=sqlite_maker, gateways=None, fernet_keys=[_KEY]
    )
    assert refund_no_gw is None
    assert expire_no_gw is not None


# --------------------------------------------------------------------------------------
# ==The refund runner ROUTES by the intent's provider== (B-06).
# --------------------------------------------------------------------------------------


class _MercadoPagoGatewaySpy:
    """A Mercado Pago gateway. ==Its secret is an ``access_token``, and it has no ``secret_key``.==

    That asymmetry is the whole defect being regressed: hand this business's credential to Stripe's
    gateway and it reads ``secrets["secret_key"]`` and raises ``KeyError``.
    """

    def __init__(self) -> None:
        self.refunds: list[str] = []

    @property
    def checkout_session_floor(self) -> timedelta:
        return timedelta(0)

    async def refund(
        self, *, provider_ref: str, idempotency_key: str, secrets: Mapping[str, str]
    ) -> None:
        assert "secret_key" not in secrets, "a Mercado Pago credential has no Stripe key in it"
        assert secrets["access_token"].startswith("TEST-")
        self.refunds.append(provider_ref)

    async def create_checkout_session(
        self, **_: object
    ) -> object:  # pragma: no cover - unused here
        raise AssertionError("checkout is not part of the drain")


async def _mercado_pago_credential(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    await store_credential(
        session,
        tenant_id=tenant_id,
        provider=CredentialProvider.MERCADO_PAGO,
        secrets={"access_token": "TEST-NOT-A-REAL-TOKEN", "webhook_secret": "mp_whsec_x"},
        fernet_key=_KEY,
    )


def _mercado_pago_refund_work(tenant_id: uuid.UUID, booking_id: uuid.UUID) -> OutboxWork:
    return OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.REFUND,
        dedupe_key=refund_dedupe_key("mp_1"),
        payload={"provider": "mercado_pago", "provider_ref": "mp_1"},
        attempts=0,
        claimed_by="worker-1",
    )


async def test_the_refund_runner_routes_to_the_gateway_for_the_intents_provider(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==The B-06 routing defect, closed.==

    The runner used to take ONE gateway and pass it the intent's ``provider`` — which every gateway
    ignored (``del provider``). So a Mercado Pago refund resolved the business's Mercado Pago
    credential, handed it to ``StripeGateway``, and died on ``secrets["secret_key"]`` with a
    ``KeyError``, retrying until the attempts ran out: a refund queued, drained, and never sent.

    The provider now SELECTS the gateway. Stripe's is present here and must NOT be touched.
    """
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        s.add(
            Payment(
                tenant_id=tenant_id,
                booking_id=booking.id,
                provider="mercado_pago",
                provider_ref="mp_1",
                status=PaymentStatus.PAID,
                amount_cents=5000,
                currency="usd",
            )
        )
        await _mercado_pago_credential(s, tenant_id)
        booking_id = booking.id

    stripe_gateway = _GatewaySpy()
    mp_gateway = _MercadoPagoGatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker,
        gateways={"stripe": stripe_gateway, "mercado_pago": mp_gateway},
        fernet_keys=[_KEY],
    )

    await runner(_mercado_pago_refund_work(tenant_id, booking_id), NOW)

    assert mp_gateway.refunds == ["mp_1"], "the Mercado Pago charge went to Mercado Pago"
    assert stripe_gateway.refunds == [], "Stripe was not asked to refund another provider's charge"


async def test_a_refund_for_a_provider_with_no_gateway_fails_loudly(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Fail-closed, not fail-wrong.== A provider with no gateway raises, so the intent stays
    queued and a human sees it — rather than reaching for whichever gateway happens to be at hand
    and refunding a charge through an account that never took it."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        booking = await _booking(s, tenant_id, status=BookingStatus.CANCELLED)
        s.add(
            Payment(
                tenant_id=tenant_id,
                booking_id=booking.id,
                provider="mercado_pago",
                provider_ref="mp_1",
                status=PaymentStatus.PAID,
                amount_cents=5000,
                currency="usd",
            )
        )
        await _mercado_pago_credential(s, tenant_id)
        booking_id = booking.id

    stripe_only = _GatewaySpy()
    runner = make_refund_runner(
        sessionmaker=sqlite_maker, gateways={"stripe": stripe_only}, fernet_keys=[_KEY]
    )

    with pytest.raises(LookupError, match="mercado_pago"):
        await runner(_mercado_pago_refund_work(tenant_id, booking_id), NOW)
    assert stripe_only.refunds == [], "the wrong gateway is never a fallback"
