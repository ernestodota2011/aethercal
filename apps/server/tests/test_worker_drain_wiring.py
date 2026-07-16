"""The worker's drain wiring arms a FUNCTIONAL refund runner (B-05b, re-Crisol #1).

The drain tick that builds the live executor is ``# pragma: no cover`` — so nobody proved the worker
actually arms an invocable REFUND runner from its ``app.state`` gateway + keys. A money path
nobody exercises is not acceptable. This drives a REFUND through ``build_drain_executor`` (the
exact wiring the tick uses) and asserts the provider was called — the evidence that the wiring is
correct (the static read said it was; this proves it).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.guc import tenant_scope
from aethercal.server.db.models import Booking, Payment, PaymentStatus, Schedule, Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.scheduler import build_drain_executor
from aethercal.server.services.outbox import OutboxEffect, OutboxWork, refund_dedupe_key
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_KEY = derive_fernet_key("test-app-secret")
_REF = "pi_test_NOT_A_REAL_KEY_A"


class _GatewaySpy:
    def __init__(self) -> None:
        self.refunds: list[str] = []

    async def refund(
        self,
        *,
        provider: str,
        provider_ref: str,
        amount_cents: int,
        idempotency_key: str,
        secrets: Mapping[str, str],
    ) -> None:
        assert secrets.get("secret_key", "").startswith("sk_test_")
        self.refunds.append(idempotency_key)

    async def create_checkout_session(
        self, **_: object
    ) -> object:  # pragma: no cover - unused here
        raise AssertionError("checkout is not part of the drain")


def _fake_worker_app(sqlite_maker: async_sessionmaker[AsyncSession], gateway: object) -> object:
    """An app whose ``state`` mirrors exactly what ``create_worker_app`` sets — pools + the rotation
    keys + the BYOK gateway + the (absent) senders. ``build_drain_executor`` reads only this."""
    return SimpleNamespace(
        state=SimpleNamespace(
            pools=WorkerPools.for_offline_tests(sqlite_maker),
            fernet_keys=[_KEY],
            payment_gateway=gateway,
            email_sender=None,
            channel_senders={},
        )
    )


async def test_the_worker_drain_arms_a_functional_invocable_refund_runner(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Drive a REFUND through the exact executor the worker builds — the provider is called, so the
    refund runner is genuinely armed (not None, not a raise)."""
    async with sqlite_maker() as s, s.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
        s.add(tenant)
        await s.flush()
        s.add(User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC"))
        s.add(Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={}))
        await s.flush()
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=uuid.uuid4(),
            start_at=_SLOT,
            end_at=_SLOT + timedelta(minutes=30),
            status=BookingStatus.CANCELLED,
            confirmed_at=NOW,
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        s.add(booking)
        await s.flush()
        s.add(
            Payment(
                tenant_id=tenant.id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=_REF,
                status=PaymentStatus.PAID,
                amount_cents=5000,
                currency="usd",
            )
        )
        await store_credential(
            s,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x", "webhook_secret": "whsec_x"},
            fernet_key=_KEY,
        )
        tenant_id, booking_id = tenant.id, booking.id

    gateway = _GatewaySpy()
    execute = build_drain_executor(_fake_worker_app(sqlite_maker, gateway))

    work = OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.REFUND,
        dedupe_key=refund_dedupe_key(_REF),
        payload={"provider": "stripe", "provider_ref": _REF},
        attempts=0,
        claimed_by="worker-1",
    )
    with tenant_scope(tenant_id):
        await execute(work, NOW)

    assert gateway.refunds == [f"refund:{_REF}"], (
        "the worker's drain executor arms a functional, invocable refund runner"
    )
