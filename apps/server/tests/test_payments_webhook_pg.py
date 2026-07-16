"""The inbound payment webhook router, end-to-end on real PostgreSQL (B-05b, criterion 33).

``db``-marked: the router's whole point is the order slug → bind → read-secret → verify → write, and
that only means anything under row-level security with a real ``tenant_credentials`` row. It drives
the real FastAPI app over Postgres, seeding on the OWNER engine and POSTing over HTTP so the request
path binds the business exactly as production does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Payment,
    PaymentEvent,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential

pytestmark = pytest.mark.db


def _stripe_sig(raw: bytes, *, timestamp: int = 1_700_000_000) -> str:
    """A valid ``Stripe-Signature`` header over ``raw`` (the scheme the real adapter verifies)."""
    payload = f"{timestamp}.".encode() + raw
    mac = hmac.new(_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


_KEY = derive_fernet_key("test-app-secret")  # the app fixture's app_secret
_SECRET = "whsec_test_NOT_A_REAL_KEY_x"
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"
# ==Finding 1.== The Checkout Session id the row is created with (its ``provider_ref`` NULL) before
# the confirming webhook backfills the intent ``_REF``.
_SESSION = "cs_test_NOT_A_REAL_KEY_S"


async def _seed(
    owner_maker: async_sessionmaker[AsyncSession],
    *,
    provider_ref: str | None = _REF,
    checkout_session_id: str | None = None,
) -> tuple[str, uuid.UUID]:
    """A business with a Stripe webhook secret + a PENDING hold and its INTENT payment. OWNER.

    ``provider_ref``/``checkout_session_id`` model the payment's anchor state: the default
    (``provider_ref`` set, no session id) is a row whose intent is already known; passing
    ``provider_ref=None`` with a ``checkout_session_id`` models the real just-created row (finding
    1), whose intent does not exist yet and is backfilled by ``checkout.session.completed``.
    """
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=slug, name="Biz")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="paid",
            title="Paid",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 60,
            price_cents=_PRICE,
            currency=_CUR,
        )
        session.add(event_type)
        await session.flush()
        now = datetime.now(UTC)
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=30),
            status=BookingStatus.PENDING,
            confirmed_at=None,
            hold_expires_at=now + timedelta(minutes=30),
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        session.add(
            Payment(
                tenant_id=tenant.id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=provider_ref,
                checkout_session_id=checkout_session_id,
                status=PaymentStatus.INTENT,
                amount_cents=_PRICE,
                currency=_CUR,
            )
        )
        await store_credential(
            session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x", "webhook_secret": _SECRET},
            fernet_key=_KEY,
        )
        return slug, booking.id


def _paid_body(event_id: str = "evt_1") -> bytes:
    """A Stripe ``payment_intent.succeeded`` event, the shape the real adapter parses."""
    return json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": _REF, "amount": _PRICE, "currency": _CUR}},
        }
    ).encode("utf-8")


def _checkout_completed_body(event_id: str = "evt_cs") -> bytes:
    """A Stripe ``checkout.session.completed`` event — the FIRST event, carrying BOTH the session id
    (the creation-time anchor) and the now-real PaymentIntent (finding 1)."""
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": _SESSION,
                    "payment_intent": _REF,
                    "amount_total": _PRICE,
                    "currency": _CUR,
                }
            },
        }
    ).encode("utf-8")


async def _refund_count(owner_maker: async_sessionmaker[AsyncSession]) -> int:
    async with owner_maker() as session:
        rows = (
            await session.scalars(select(Outbox).where(Outbox.effect == OutboxEffect.REFUND.value))
        ).all()
        return len(list(rows))


async def _payment_event_count(owner_maker: async_sessionmaker[AsyncSession]) -> int:
    async with owner_maker() as session:
        return (await session.scalar(select(func.count()).select_from(PaymentEvent))) or 0


async def test_an_invalid_signature_is_401_with_zero_writes(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """==Criterion 33.== A bad signature is refused BEFORE anything is written — the payment_events
    table is untouched."""
    slug, _booking_id = await _seed(owner_maker)

    response = await client.post(
        f"/webhooks/stripe/{slug}",
        content=_paid_body(),
        headers={"Stripe-Signature": "t=1,v1=deadbeef", "content-type": "application/json"},
    )

    assert response.status_code == 401
    assert await _payment_event_count(owner_maker) == 0, "an unverified event must write NOTHING"


async def test_a_valid_signature_records_the_event_and_confirms_the_hold(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """A correctly-signed paid event is recorded and the arbiter confirms the hold."""
    slug, booking_id = await _seed(owner_maker)
    body = _paid_body()
    sig = _stripe_sig(body)

    response = await client.post(
        f"/webhooks/stripe/{slug}",
        content=body,
        headers={"Stripe-Signature": sig, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert await _payment_event_count(owner_maker) == 1
    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.CONFIRMED
        assert booking.confirmed_by_payment_id is not None


async def test_a_replayed_event_id_writes_only_one_row(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """The same ``event_id`` delivered twice (Stripe retries) records ONE row — the anti-replay
    UNIQUE."""
    slug, _booking_id = await _seed(owner_maker)
    body = _paid_body(event_id="evt_dup")
    sig = _stripe_sig(body)
    headers = {"Stripe-Signature": sig, "content-type": "application/json"}

    first = await client.post(f"/webhooks/stripe/{slug}", content=body, headers=headers)
    second = await client.post(f"/webhooks/stripe/{slug}", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json() == {"status": "ok"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert await _payment_event_count(owner_maker) == 1


async def test_a_signed_but_unsupported_event_is_acked_200_with_no_write(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """==Finding 4.== A VALID-SIGNED event of a type we do not handle is ACKed 200 (received, not
    interesting), NOT 400 — a non-2xx makes Stripe retry it for ever. Nothing is written; 400/401
    stay reserved for a body we cannot parse / a signature we cannot verify."""
    slug, _booking_id = await _seed(owner_maker)
    body = json.dumps(
        {"id": "evt_unsupported", "type": "customer.created", "data": {"object": {}}}
    ).encode("utf-8")
    sig = _stripe_sig(body)

    response = await client.post(
        f"/webhooks/stripe/{slug}",
        content=body,
        headers={"Stripe-Signature": sig, "content-type": "application/json"},
    )

    assert response.status_code == 200, response.text
    assert await _payment_event_count(owner_maker) == 0, "an event we ignore writes nothing"


async def test_an_unknown_business_is_401(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """An unknown slug is a 401, indistinguishable from a bad signature (no enumeration)."""
    body = _paid_body()
    sig = _stripe_sig(body)

    response = await client.post(
        f"/webhooks/stripe/does-not-exist-{uuid.uuid4().hex[:6]}",
        content=body,
        headers={"Stripe-Signature": sig, "content-type": "application/json"},
    )

    assert response.status_code == 401


async def test_an_oversized_body_is_413_before_any_verification_or_write(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """==Re-Crisol r4 finding 3.== The webhook is UNAUTHENTICATED, so a body over the size cap is a
    413 read under the cap — NOT buffered whole — before the signature is even checked or the
    database is touched. A giant POST cannot exhaust the process's memory or write a row."""
    slug, _booking_id = await _seed(owner_maker)
    # Over the 256 KiB cap. A valid signature is irrelevant: the size gate runs first.
    oversized = b'{"padding":"' + b"A" * (300 * 1024) + b'"}'

    response = await client.post(
        f"/webhooks/stripe/{slug}",
        content=oversized,
        headers={"Stripe-Signature": _stripe_sig(oversized), "content-type": "application/json"},
    )

    assert response.status_code == 413, response.text
    assert await _payment_event_count(owner_maker) == 0, "an oversized body writes NOTHING"


async def test_a_normal_sized_event_still_processes(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """The cap does not get in the way of a real event: an ordinary (KB-sized) payload is read and
    applied exactly as before (finding 3 must not break the happy path)."""
    slug, booking_id = await _seed(owner_maker)
    body = _paid_body(event_id="evt_normal")
    sig = _stripe_sig(body)

    response = await client.post(
        f"/webhooks/stripe/{slug}",
        content=body,
        headers={"Stripe-Signature": sig, "content-type": "application/json"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.CONFIRMED


async def test_both_stripe_events_resolve_to_one_row_from_the_session_anchor(
    client: AsyncClient, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """==Finding 1, end-to-end (criterion 24).== The row is created the way ``_start_paid_booking``
    creates it: anchored on the Checkout Session id, ``provider_ref`` NULL (no intent yet).
    ``checkout.session.completed`` — which carries the session id AND the now-real intent —
    resolves the row by the session id, BACKFILLS ``provider_ref``, and confirms. Then
    ``payment_intent.succeeded`` (which knows only the intent) resolves by the backfilled
    ``provider_ref`` and is an idempotent replay. TWO Stripe events, ONE confirmation, ZERO refunds.
    """
    slug, booking_id = await _seed(owner_maker, provider_ref=None, checkout_session_id=_SESSION)
    headers = {"content-type": "application/json"}

    # (1) checkout.session.completed → resolve by session id, backfill the intent, confirm.
    cs_body = _checkout_completed_body(event_id="evt_cs_1")
    first = await client.post(
        f"/webhooks/stripe/{slug}",
        content=cs_body,
        headers={**headers, "Stripe-Signature": _stripe_sig(cs_body)},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"status": "ok"}

    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.CONFIRMED
        confirming_payment_id = booking.confirmed_by_payment_id
        assert confirming_payment_id is not None
        payment = (
            await session.scalars(select(Payment).where(Payment.booking_id == booking_id))
        ).one()
        # ==The intent was backfilled from the confirming event== — the row is now findable by it.
        assert payment.provider_ref == _REF
        assert payment.checkout_session_id == _SESSION
        assert payment.status is PaymentStatus.PAID

    # (2) payment_intent.succeeded → resolves by the backfilled provider_ref → idempotent replay.
    pi_body = _paid_body(event_id="evt_pi_1")
    second = await client.post(
        f"/webhooks/stripe/{slug}",
        content=pi_body,
        headers={**headers, "Stripe-Signature": _stripe_sig(pi_body)},
    )
    assert second.status_code == 200, second.text

    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        assert booking.status is BookingStatus.CONFIRMED
        # ==ONE confirmation== — the same payment, never re-confirmed by "another".
        assert booking.confirmed_by_payment_id == confirming_payment_id

    # ==ZERO refunds.== The replay must not have been mistaken for a double payment.
    assert await _refund_count(owner_maker) == 0
    assert await _payment_event_count(owner_maker) == 2  # both events recorded, both applied
