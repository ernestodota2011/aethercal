"""Pricing on the EventType contract + service (B-05b, re-Crisol #4).

An event type carries what it costs and how it refunds: ``price_cents`` (NULL = FREE), ``currency``,
``refund_window_minutes`` and ``refund_kind`` (``full`` | ``none``). These are the fields the
arbiter later validates a payment against, so they must be reachable end-to-end — set on create,
read
back, patched on update — and a business must not be able to configure a type that CANNOT take
payment (a price with no currency, or a currency with no price). That last invariant is refused at
the edge (422), never persisted: a priced-but-uncurrencied type would hold a slot and then have no
way to charge for it.

Offline (in-memory) TDD, mirroring ``test_event_types_service.py``: the contract validation runs in
Pydantic and the round-trip runs through the real service against SQLite — no Postgres, no Stripe.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.event_types import EventTypeCreate, EventTypeRead, EventTypeUpdate
from aethercal.server.db.models import Schedule, Tenant, User
from aethercal.server.db.models.payments import RefundKind
from aethercal.server.services.event_types import (
    create_event_type,
    get_event_type,
    update_event_type,
)

TenantFactory = Callable[..., Awaitable[Tenant]]


async def _host_id(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).first()
    assert host is not None
    return host.id


async def _schedule_id(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    schedule = Schedule(
        tenant_id=tenant.id, name=f"sched-{uuid.uuid4().hex[:12]}", timezone="UTC", rules={}
    )
    session.add(schedule)
    await session.flush()
    return schedule.id


async def _make_payload(
    session: AsyncSession, tenant: Tenant, *, slug: str = "paid-intro", **overrides: object
) -> EventTypeCreate:
    data: dict[str, object] = {
        "host_id": await _host_id(session, tenant),
        "schedule_id": await _schedule_id(session, tenant),
        "slug": slug,
        "title": "Paid Intro",
        "duration_seconds": 1800,
        "max_advance_seconds": 60 * 60 * 24 * 30,
    }
    data.update(overrides)
    return EventTypeCreate(**data)


# --- the contract edge (Pydantic, no DB) --------------------------------------------------------


def test_a_price_without_a_currency_is_refused_at_the_edge() -> None:
    """==Re-Crisol #4.== A priced type with no currency could hold a slot it can never charge for.
    Both-or-neither is on the contract, so the 422 lands before any handler or hold runs."""
    with pytest.raises(ValidationError, match="set together"):
        EventTypeCreate(
            host_id=uuid.uuid4(),
            schedule_id=uuid.uuid4(),
            slug="x",
            title="X",
            duration_seconds=1800,
            max_advance_seconds=60,
            price_cents=5000,
        )


def test_a_currency_without_a_price_is_refused_at_the_edge() -> None:
    """The mirror: a currency with no price is just as incoherent — refused symmetrically."""
    with pytest.raises(ValidationError, match="set together"):
        EventTypeCreate(
            host_id=uuid.uuid4(),
            schedule_id=uuid.uuid4(),
            slug="x",
            title="X",
            duration_seconds=1800,
            max_advance_seconds=60,
            currency="usd",
        )


def test_a_free_type_needs_neither_price_nor_currency() -> None:
    """The default: no price, no currency, a zero refund window and ``none`` — a free type is valid
    and unchanged from every event type that existed before payments."""
    free = EventTypeCreate(
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="free",
        title="Free",
        duration_seconds=1800,
        max_advance_seconds=60,
    )
    assert free.price_cents is None
    assert free.currency is None
    assert free.refund_window_minutes == 0
    assert free.refund_kind == "none"


def test_currency_is_normalised_to_a_lower_case_iso_code() -> None:
    """Stripe wants a lower-cased 3-letter code; ``USD`` and ``  Usd `` both land as ``usd``."""
    paid = EventTypeCreate(
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="p",
        title="P",
        duration_seconds=1800,
        max_advance_seconds=60,
        price_cents=5000,
        currency="  USD ",
    )
    assert paid.currency == "usd"


def test_a_malformed_currency_is_refused() -> None:
    """Two letters, digits, four letters — none is a valid ISO 4217 code, all are refused."""
    for bad in ("us", "us1", "dollars"):
        with pytest.raises(ValidationError, match="ISO 4217"):
            EventTypeCreate(
                host_id=uuid.uuid4(),
                schedule_id=uuid.uuid4(),
                slug="p",
                title="P",
                duration_seconds=1800,
                max_advance_seconds=60,
                price_cents=5000,
                currency=bad,
            )


def test_refund_kind_only_accepts_full_or_none() -> None:
    """Partial/tiered refunds are F5 — the contract admits exactly ``full`` and ``none`` today."""
    with pytest.raises(ValidationError):
        EventTypeCreate(
            host_id=uuid.uuid4(),
            schedule_id=uuid.uuid4(),
            slug="p",
            title="P",
            duration_seconds=1800,
            max_advance_seconds=60,
            refund_kind="partial",  # type: ignore[arg-type]
        )


# --- the round-trip through the service (SQLite) ------------------------------------------------


async def test_a_priced_type_round_trips_through_the_service(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Create a priced type, read it back: price, currency, refund window and refund kind all land
    on the row — and ``refund_kind`` is coerced from the ``full`` string to the ``RefundKind`` enum
    the arbiter compares against."""
    tenant = await tenant_factory(sqlite_session)
    payload = await _make_payload(
        sqlite_session,
        tenant,
        price_cents=5000,
        currency="usd",
        refund_window_minutes=1440,
        refund_kind="full",
    )

    created = await create_event_type(sqlite_session, tenant_id=tenant.id, data=payload)
    assert created.price_cents == 5000
    assert created.currency == "usd"
    assert created.refund_window_minutes == 1440
    # Reloading applies the Enum's result processor — the exact coercion the arbiter relies on when
    # it reads the event type on a fresh session to validate a payment's refund policy.
    await sqlite_session.refresh(created)
    assert created.refund_kind is RefundKind.FULL

    fetched = await get_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=created.id)
    assert fetched is not None
    read = EventTypeRead.model_validate(fetched)
    assert read.price_cents == 5000
    assert read.currency == "usd"
    assert read.refund_window_minutes == 1440
    assert read.refund_kind == "full"


async def test_a_free_type_round_trips_with_a_null_price(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """A free type keeps a NULL price and the ``none`` default — the shape the public path reads to
    decide it confirms directly with no hold and no checkout."""
    tenant = await tenant_factory(sqlite_session)
    created = await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="free"),
    )
    assert created.price_cents is None
    assert created.currency is None

    read = EventTypeRead.model_validate(created)
    assert read.price_cents is None
    assert read.refund_kind == "none"


async def test_update_can_set_and_change_the_price(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """A free type is later put on sale, then re-priced — each patch touches only what it sends, and
    the price it lands on is the one the arbiter will hold against."""
    tenant = await tenant_factory(sqlite_session)
    created = await create_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        data=await _make_payload(sqlite_session, tenant, slug="grow"),
    )
    assert created.price_cents is None

    priced = await update_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=created.id,
        data=EventTypeUpdate(price_cents=2500, currency="USD", refund_window_minutes=60),
    )
    assert priced is not None
    assert priced.price_cents == 2500
    assert priced.currency == "usd"
    assert priced.refund_window_minutes == 60

    reraised = await update_event_type(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=created.id,
        data=EventTypeUpdate(price_cents=3000),
    )
    assert reraised is not None
    assert reraised.price_cents == 3000
    assert reraised.currency == "usd", "an untouched field is left alone"
