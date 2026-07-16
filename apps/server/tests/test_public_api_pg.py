"""The PUBLIC router, over real PostgreSQL — the endpoint that has had its authentication removed.

==Taking the key off a WRITE endpoint is the most dangerous change in this project.== So the tests
that matter here are not "does it return 201": they are the ones that say what the endpoint refuses
to do, and what it refuses to say.

* **it lands in the RIGHT business** (criterion 15). The event-type slug is unique *per business*
  (``scheduling.py``: ``UniqueConstraint("tenant_id", "slug")``), so ``intro`` exists in every
  business on the instance. Only ``tenants.slug`` is globally unique, so ``(tenant_slug,
  event_slug)`` is the resolver — and it FAILS CLOSED on anything but exactly one row. Get this
  wrong and a guest's booking is written into a stranger's diary; tomorrow, under B-05b, their money
  lands in a stranger's Stripe account;
* **it says almost nothing back.** ``BookingRead`` is a PII dump — name, e-mail, notes, answers. The
  public POST answers ``{id, start, end, status}`` and not one field more;
* **the private list stays private, and it PAGINATES** (criterion 16). ``GET /api/v1/bookings``
  still needs the key, is never mounted under ``/public``, and no longer streams every booking a
  business ever took in one unbounded response;
* **the captcha is not optional** (criterion 14 — proved at boot in ``test_turnstile.py``, and here
  at the endpoint);
* **the address is recorded, and it is the GUEST's** — resolved through the declared proxy contract,
  never from a header any caller may set;
* **the per-IP cap DENIES A REAL SEND** (criterion 17b) — with a configured WhatsApp channel sitting
  right there, able and willing to message a real phone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.api import bookings as bookings_api
from aethercal.server.app import create_app
from aethercal.server.channels import Channel
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Schedule,
    Tenant,
    User,
    Workflow,
    WorkflowStep,
    WorkflowTemplate,
)
from aethercal.server.db.models.outbox import OutboxStatus
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.db.pools import WorkerPools
from aethercal.server.db.roles import DbRole
from aethercal.server.integrations.messaging.guard import DailyCaps
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.outbox import (
    OutboxEffect,
    drain_outbox,
    make_booking_effect_executor,
)
from aethercal.server.services.slots import compute_slots
from aethercal.server.settings import Settings

pytestmark = pytest.mark.db

Sessionmaker = async_sessionmaker[AsyncSession]

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}
_TURNSTILE_SECRET = "1x0000000000000000000000000000000AA"
_GUEST_IP = "203.0.113.9"
_OTHER_GUEST_IP = "198.51.100.4"
# The ASGI transport's peer address. Declaring it trusted is what lets these tests speak as a guest
# from a chosen address — exactly as the booking page does in production, and by the same contract.
_LOOPBACK_CIDR = "127.0.0.0/8"

_RATE_LIMIT = 8


class _StubTurnstile:
    """Stands in for Cloudflare — and deliberately NOT a pass-through.

    A stub that said yes to everything would let the endpoint ship with the verification wired to
    nothing at all, which is the exact failure this cut exists to prevent.
    """

    VALID = "a-human-solved-this"

    def __init__(self) -> None:
        self.seen: list[tuple[str | None, str | None]] = []

    async def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        self.seen.append((token, remote_ip))
        return token == self.VALID


class _RecordingEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class _RecordingWhatsAppSender:
    """A CONFIGURED WhatsApp channel — so "nobody could have sent it" is never why a test passes."""

    channel = Channel.WHATSAPP

    def __init__(self, *, per_ip: int) -> None:
        self.sent: list[tuple[str, str]] = []
        self.caps = DailyCaps(per_phone=100, per_ip=per_ip)

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        del subject
        self.sent.append((to, body))


@pytest.fixture
def turnstile() -> _StubTurnstile:
    return _StubTurnstile()


@pytest_asyncio.fixture
async def public_app(
    pg_role_urls: dict[DbRole, str], pg_clean: None, turnstile: _StubTurnstile
) -> AsyncIterator[FastAPI]:
    """The real app with the PUBLIC router enabled — the shape the booking page now talks to."""
    del pg_clean
    settings = Settings(
        database_url=pg_role_urls[DbRole.APP],
        owner_database_url=pg_role_urls[DbRole.OWNER],
        worker_database_url=pg_role_urls[DbRole.WORKER],
        app_secret="test-app-secret",
        public_api_enabled=True,
        turnstile_secret=_TURNSTILE_SECRET,
        trusted_proxies=_LOOPBACK_CIDR,
        public_rate_limit_per_minute=_RATE_LIMIT,
    )
    application = create_app(settings)
    application.state.turnstile = turnstile  # stand in for Cloudflare; the WIRING is under test
    try:
        yield application
    finally:
        await application.state.engine.dispose()


@pytest_asyncio.fixture
async def public_client(public_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=public_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def _seed_business(
    owner_maker: Sessionmaker,
    *,
    slug: str,
    event_slug: str = "intro",
    with_whatsapp_rule: bool = False,
) -> dict[str, Any]:
    """One business, one open schedule, one event type — seeded on the OWNER engine (arrangement).

    ==Two of these IS criterion 15==, and a fixture that needs two businesses cannot run on the app
    role at all: ``bind_tenant`` refuses to re-bind a scope to a second business.
    """
    sessionmaker: Sessionmaker = owner_maker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug)
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_ALWAYS_OPEN)
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug=event_slug,
            title="Intro Call",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()

        if with_whatsapp_rule:
            session.add(
                WorkflowTemplate(
                    tenant_id=tenant.id,
                    channel=Channel.WHATSAPP.value,
                    kind="reminder",
                    locale="es",
                    body="Hola {{guest_name}}, te esperamos.",
                )
            )
            workflow = Workflow(
                tenant_id=tenant.id,
                event_type_id=None,
                name="whatsapp reminder",
                trigger=WorkflowTrigger.BEFORE_START.value,
                offset_minutes=-1440,
                active=True,
            )
            session.add(workflow)
            await session.flush()
            session.add(
                WorkflowStep(
                    tenant_id=tenant.id,
                    workflow_id=workflow.id,
                    channel=Channel.WHATSAPP.value,
                    kind="reminder",
                    position=0,
                )
            )
            await session.flush()

        _, full_key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")

        now = datetime.now(UTC)
        # Two days out, so the BEFORE_START (-1440 min) step's send time is still in the FUTURE and
        # it is a real pending row, not one retired for arriving after the fact.
        target = (now + timedelta(days=2)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=target,
            window_to=target,
            now=now,
        )
        assert result is not None and len(result.slots) >= 3
        return {
            "tenant_id": tenant.id,
            "tenant_slug": slug,
            "event_slug": event_slug,
            "event_type_id": event_type.id,
            "headers": {"Authorization": f"Bearer {full_key}"},
            "slots": [slot.start.isoformat() for slot in result.slots[:3]],
            # The actual instants, so a cap test can drain at a moment when the reminders of ALL the
            # bookings it made are due at once — the bookings sit on DIFFERENT slots, so their
            # (start - 24h) reminders come due at different times, and a drain at the earliest would
            # only ever see one of them.
            "starts": [slot.start for slot in result.slots[:3]],
            "start": result.slots[0].start,
            "window": target.isoformat(),
        }


@pytest_asyncio.fixture
async def acme(owner_maker: Sessionmaker) -> dict[str, Any]:
    return await _seed_business(owner_maker, slug=f"acme-{uuid.uuid4().hex[:6]}")


def _payload(seeded: dict[str, Any], *, index: int = 0, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "start": seeded["slots"][index],
        "guest_name": "Ada Lovelace",
        "guest_email": f"ada+{uuid.uuid4().hex[:6]}@example.com",
        "guest_timezone": "UTC",
        "turnstile_token": _StubTurnstile.VALID,
    }
    body.update(over)
    return body


def _as_guest(ip: str = _GUEST_IP) -> dict[str, str]:
    """The headers the booking page sets when it forwards a guest's request to the API."""
    return {"X-Forwarded-For": ip}


def _public(seeded: dict[str, Any], suffix: str = "") -> str:
    return f"/api/v1/public/{seeded['tenant_slug']}/{seeded['event_slug']}{suffix}"


async def _stored(owner_maker: Sessionmaker, booking_id: str) -> Booking:
    """The booking as PostgreSQL actually holds it. Read on the OWNER engine, which sees EVERY
    business — so a row filed under the wrong one is a visible failure, not an invisible one."""
    sessionmaker: Sessionmaker = owner_maker
    async with sessionmaker() as session:
        row = await session.scalar(select(Booking).where(Booking.id == uuid.UUID(booking_id)))
    assert row is not None
    return row


# --------------------------------------------------------------------------------------
# No key. That is the whole point.
# --------------------------------------------------------------------------------------


async def test_event_types_are_readable_with_NO_api_key(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    response = await public_client.get(f"/api/v1/public/{acme['tenant_slug']}/event-types")

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["intro"]


async def test_the_public_event_type_projection_leaks_no_internals(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """==Its OWN projection, never ``EventTypeRead``.== That model carries ``tenant_id``,
    ``host_id``
    and ``schedule_id`` — internal identifiers of a business, handed to anonymous callers, for
    nothing. What a guest needs in order to choose a time is a slug, a title, a description, a
    duration and the questions they will be asked — plus the price they will pay (B-05b), but NEVER
    the internal refund policy (``refund_window_minutes``/``refund_kind`` stay off it)."""
    response = await public_client.get(f"/api/v1/public/{acme['tenant_slug']}/event-types")

    assert set(response.json()[0]) == {
        "slug",
        "title",
        "description",
        "title_translations",
        "description_translations",
        "location",
        "duration_seconds",
        "questions",
        "price_cents",
        "currency",
        "collects_phone",
    }


async def test_slots_are_readable_with_NO_api_key(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    response = await public_client.get(
        _public(acme, "/slots"),
        params={"from": acme["window"], "to": acme["window"], "tz": "UTC"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["event_slug"] == "intro"
    assert body["slots"]
    assert "event_type_id" not in body  # not a secret — simply not the public contract


async def test_a_booking_is_created_with_NO_api_key(
    public_client: AsyncClient, acme: dict[str, Any], owner_maker: Sessionmaker
) -> None:
    response = await public_client.post(
        _public(acme, "/bookings"), json=_payload(acme), headers=_as_guest()
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # Six fields now: the four originals + ``checkout_url`` (B-05b) + ``checkout_token`` (r5). Both
    # carry no PII and are ``None`` for a FREE booking like this one (``acme`` has no price, so it
    # confirms on the spot with no hold, no checkout, and no resume token).
    assert set(body) == {"id", "start", "end", "status", "checkout_url", "checkout_token"}
    assert body["status"] == "confirmed"
    assert body["checkout_url"] is None
    assert body["checkout_token"] is None
    assert (await _stored(owner_maker, body["id"])).tenant_id == acme["tenant_id"]


async def test_the_public_booking_response_is_NOT_the_pii_dump(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """``BookingRead`` answers with ``guest_name``, ``guest_email``, ``guest_notes`` and
    ``answers``.
    On an endpoint with no authentication, echoing those back is how a booking id becomes an oracle
    for somebody else's personal data. The public model has four fields, and this is its lock."""
    response = await public_client.post(
        _public(acme, "/bookings"),
        json=_payload(acme, guest_notes="my private note", answers={"q": "secret"}),
        headers=_as_guest(),
    )

    body = response.json()
    assert "guest_email" not in body
    assert "guest_name" not in body
    assert "guest_notes" not in body
    assert "answers" not in body


# --------------------------------------------------------------------------------------
# Branding — the keyless "whose page is this?", four public columns and not one more.
# --------------------------------------------------------------------------------------


async def test_branding_is_readable_with_NO_api_key(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """The booking page reads its business's brand with no key, naming the business in the ROUTE —
    the keyless twin of ``GET /api/v1/branding``. Four public columns cross the wire and not one
    more: the registered ``name``, the ``slug`` and the id are the operator's handles, never a
    guest's to see."""
    response = await public_client.get(f"/api/v1/public/{acme['tenant_slug']}/branding")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"display_name", "logo_url", "accent_color", "timezone"}
    # No public_name is set on the seeded business, so a guest reads the registered name; the logo
    # and colour are absent, and the zone is the NOT-NULL default every existing row backfilled to.
    assert body["display_name"] == acme["tenant_slug"]
    assert body["logo_url"] is None
    assert body["accent_color"] is None
    assert body["timezone"] == "UTC"


async def test_branding_for_an_unknown_business_fails_CLOSED(
    public_client: AsyncClient,
) -> None:
    """An unknown slug is the shared public 404 — the same answer every other public miss gives, so
    the endpoint that asked for no credentials is not an oracle for which businesses exist."""
    response = await public_client.get("/api/v1/public/no-such-business/branding")

    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# ==Criterion 15== — the same event slug lives in two businesses. It must land in the right one.
# --------------------------------------------------------------------------------------


async def test_the_same_event_slug_in_TWO_businesses_lands_in_the_correct_one(
    public_client: AsyncClient, owner_maker: Sessionmaker
) -> None:
    """==The most expensive bug this router could have.== ``intro`` is not unique on the instance —
    only ``(tenant_id, slug)`` is — so resolving an event type by slug ALONE finds two rows, and any
    "take the first" files a guest's booking in a stranger's diary.

    Both businesses are seeded with the SAME event slug, on purpose. The resolver is
    ``(tenant_slug, event_slug)``, and ``tenants.slug`` IS globally unique, so the pair is
    unambiguous — and anything but exactly one row fails closed.
    """
    first = await _seed_business(owner_maker, slug=f"first-{uuid.uuid4().hex[:6]}")
    second = await _seed_business(owner_maker, slug=f"second-{uuid.uuid4().hex[:6]}")
    assert first["event_slug"] == second["event_slug"] == "intro"

    response = await public_client.post(
        _public(second, "/bookings"), json=_payload(second), headers=_as_guest()
    )

    assert response.status_code == 201, response.text
    stored = await _stored(owner_maker, response.json()["id"])
    assert stored.tenant_id == second["tenant_id"], "the booking landed in the WRONG business"
    assert stored.tenant_id != first["tenant_id"]
    assert stored.event_type_id == second["event_type_id"]


async def test_an_unknown_business_fails_CLOSED(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    response = await public_client.post(
        "/api/v1/public/no-such-business/intro/bookings",
        json=_payload(acme),
        headers=_as_guest(),
    )

    assert response.status_code == 404


async def test_an_event_slug_that_belongs_to_ANOTHER_business_fails_CLOSED(
    public_client: AsyncClient, owner_maker: Sessionmaker
) -> None:
    """The PAIR is the resolver, so half of one valid pair is not a booking — it is a 404."""
    first = await _seed_business(owner_maker, slug=f"first-{uuid.uuid4().hex[:6]}")
    second = await _seed_business(
        owner_maker, slug=f"second-{uuid.uuid4().hex[:6]}", event_slug="private-consult"
    )

    response = await public_client.post(
        f"/api/v1/public/{first['tenant_slug']}/{second['event_slug']}/bookings",
        json=_payload(second),
        headers=_as_guest(),
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# ==Criterion 14, at the endpoint== — the captcha is the gate, and it does not open on its own.
# --------------------------------------------------------------------------------------


async def test_a_booking_with_no_captcha_token_is_REFUSED(
    public_client: AsyncClient, acme: dict[str, Any], owner_maker: Sessionmaker
) -> None:
    payload = _payload(acme)
    del payload["turnstile_token"]

    response = await public_client.post(
        _public(acme, "/bookings"), json=payload, headers=_as_guest()
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "captcha_required"
    async with owner_maker() as session:
        assert list((await session.scalars(select(Booking))).all()) == []


async def test_a_booking_with_a_bad_captcha_token_is_REFUSED(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    response = await public_client.post(
        _public(acme, "/bookings"),
        json=_payload(acme, turnstile_token="i-am-a-bot"),
        headers=_as_guest(),
    )

    assert response.status_code == 403


async def test_the_captcha_is_verified_against_the_GUESTS_address(
    public_client: AsyncClient, acme: dict[str, Any], turnstile: _StubTurnstile
) -> None:
    await public_client.post(_public(acme, "/bookings"), json=_payload(acme), headers=_as_guest())

    assert turnstile.seen == [(_StubTurnstile.VALID, _GUEST_IP)]


# --------------------------------------------------------------------------------------
# The address — recorded, and taken from the declared proxy contract.
# --------------------------------------------------------------------------------------


async def test_the_guests_address_is_recorded_on_the_booking(
    public_client: AsyncClient, acme: dict[str, Any], owner_maker: Sessionmaker
) -> None:
    response = await public_client.post(
        _public(acme, "/bookings"), json=_payload(acme), headers=_as_guest()
    )

    assert (await _stored(owner_maker, response.json()["id"])).source_ip == _GUEST_IP


# --------------------------------------------------------------------------------------
# The API's own rate limit — there was none, anywhere, before this cut.
# --------------------------------------------------------------------------------------


async def test_the_public_router_is_rate_limited(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """The page's limiter only ever guarded the PAGE. A caller talking to the API directly — which
    is now a thing anyone can do — walked straight past it."""
    path = f"/api/v1/public/{acme['tenant_slug']}/event-types"
    for _ in range(_RATE_LIMIT):
        assert (await public_client.get(path, headers=_as_guest())).status_code == 200

    assert (await public_client.get(path, headers=_as_guest())).status_code == 429


async def test_the_rate_limit_is_per_ADDRESS_not_per_instance(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """Behind a proxy, counting the transport peer collapses every guest onto ONE bucket — a
    self-inflicted outage that looks exactly like an attack. The identity is the forwarded address,
    and it is believed only because the peer is a DECLARED trusted proxy."""
    path = f"/api/v1/public/{acme['tenant_slug']}/event-types"
    for _ in range(_RATE_LIMIT + 1):
        await public_client.get(path, headers=_as_guest())

    assert (await public_client.get(path, headers=_as_guest(_OTHER_GUEST_IP))).status_code == 200


async def test_the_rate_limit_does_not_touch_the_authenticated_api(
    public_app: FastAPI, public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """It guards the ANONYMOUS surface. A business's own integration, holding its own key, has an
    identity that is not an IP address and a budget that is not a stranger's."""
    public_app.include_router(bookings_api.router)
    for _ in range(_RATE_LIMIT + 2):
        response = await public_client.get("/bookings/", headers={**acme["headers"], **_as_guest()})

    assert response.status_code == 200


# --------------------------------------------------------------------------------------
# ==Criterion 16== — the PII dump stays behind the key, and it grows a ceiling.
# --------------------------------------------------------------------------------------


async def test_listing_bookings_is_NOT_public(
    public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """It returns ``guest_name``, ``guest_email``, ``guest_notes`` and ``answers`` for EVERY booking
    a business ever took. It is not mounted under ``/public``, and it never will be."""
    del acme
    assert (await public_client.get("/api/v1/public/bookings")).status_code == 404
    assert (await public_client.get("/api/v1/bookings/")).status_code == 401


async def test_the_private_booking_list_paginates(
    public_app: FastAPI, public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """Unbounded, it is an availability problem *for the owner of the data*: a business with a year
    of appointments asks for its list and gets a response nobody can hold in memory."""
    public_app.include_router(bookings_api.router)
    for index in range(3):
        created = await public_client.post(
            _public(acme, "/bookings"), json=_payload(acme, index=index), headers=_as_guest()
        )
        assert created.status_code == 201, created.text

    page = await public_client.get("/bookings/", params={"limit": 2}, headers=acme["headers"])
    rest = await public_client.get(
        "/bookings/", params={"limit": 2, "offset": 2}, headers=acme["headers"]
    )

    assert page.status_code == 200
    # ==The envelope, not a bare truncated list.== A default ``limit`` on an endpoint that used to
    # return EVERYTHING means a caller who asks for their bookings now receives 100 of them. Handed
    # back as a plain array, that is a silent truncation: the integration believes it holds the
    # whole
    # diary and holds a hundredth of it. ``total`` is what makes the missing rows VISIBLE.
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2
    assert page.json()["limit"] == 2
    assert len(rest.json()["items"]) == 1
    assert rest.json()["offset"] == 2


async def test_the_page_size_has_a_HARD_ceiling(
    public_app: FastAPI, public_client: AsyncClient, acme: dict[str, Any]
) -> None:
    """A caller-chosen ``limit`` with no maximum is the same unbounded query wearing a parameter."""
    public_app.include_router(bookings_api.router)

    response = await public_client.get(
        "/bookings/", params={"limit": 10_000}, headers=acme["headers"]
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------------------
# ==Criterion 17b== — the per-IP cap DENIES A REAL SEND. Not "the column exists".
# --------------------------------------------------------------------------------------


async def test_the_per_ip_cap_denies_a_REAL_send(
    public_client: AsyncClient, owner_maker: Sessionmaker, worker_pools: WorkerPools
) -> None:
    """==The no-op, closed, end to end.==

    Two bookings, from the same address, through the PUBLIC endpoint — the one an attacker can
    reach. Both are ``confirmed`` (a free event type confirms directly: there is no hold, which is
    exactly why a hold-based cap would have covered nothing — criterion 17). Both queue a real
    WhatsApp reminder, and the channel is CONFIGURED and standing by, able to message a real phone.

    The cap is one message per address per day. The drain sends the first and REFUSES the second —
    and the refusal is asserted AT THE SENDER, not at a counter: what did the provider actually
    receive? Exactly one message. That is the difference between a cap and a comment.
    """
    seeded = await _seed_business(
        owner_maker, slug=f"cap-{uuid.uuid4().hex[:6]}", with_whatsapp_rule=True
    )
    for index in range(2):
        created = await public_client.post(
            _public(seeded, "/bookings"),
            json=_payload(
                seeded, index=index, guest_phone="+13055550123", guest_phone_consent=True
            ),
            headers=_as_guest(),
        )
        assert created.status_code == 201, created.text

    whatsapp = _RecordingWhatsAppSender(per_ip=1)
    execute = make_booking_effect_executor(
        sessionmaker=worker_pools.exec_maker,
        sender=_RecordingEmailSender(),
        service_factory=None,
        channels={Channel.WHATSAPP: whatsapp},
    )
    # Drain at the LATER booking's reminder time, so BOTH reminders are due in one pass (the two
    # bookings sit on different slots). The earlier one sends; the second, from the same address,
    # meets the spent cap and is skipped.
    report = await drain_outbox(
        worker_pools, now=seeded["starts"][1] - timedelta(hours=24), execute=execute
    )

    assert len(whatsapp.sent) == 1, "the per-IP cap did not deny the second send"
    assert len(report.skipped) == 1

    async with owner_maker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.effect == OutboxEffect.NOTIFY.value)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
    whatsapp_steps = [row for row in rows if row.payload["channel"] == Channel.WHATSAPP.value]
    statuses = sorted(row.status for row in whatsapp_steps)
    assert statuses == [OutboxStatus.DELIVERED.value, OutboxStatus.SKIPPED.value]


async def test_a_booking_from_ANOTHER_address_still_gets_its_message(
    public_client: AsyncClient, owner_maker: Sessionmaker, worker_pools: WorkerPools
) -> None:
    """The cap must bite the abuser and NOBODY else. A guard that silenced every guest would satisfy
    the test above — and would have destroyed the product."""
    seeded = await _seed_business(
        owner_maker, slug=f"cap2-{uuid.uuid4().hex[:6]}", with_whatsapp_rule=True
    )
    for index, ip in enumerate((_GUEST_IP, _OTHER_GUEST_IP)):
        created = await public_client.post(
            _public(seeded, "/bookings"),
            json=_payload(
                seeded, index=index, guest_phone="+13055550123", guest_phone_consent=True
            ),
            headers=_as_guest(ip),
        )
        assert created.status_code == 201, created.text

    whatsapp = _RecordingWhatsAppSender(per_ip=1)
    execute = make_booking_effect_executor(
        sessionmaker=worker_pools.exec_maker,
        sender=_RecordingEmailSender(),
        service_factory=None,
        channels={Channel.WHATSAPP: whatsapp},
    )
    # Later reminder time so both bookings' reminders are due together; they are from DIFFERENT
    # addresses, so both are under the cap and both send.
    await drain_outbox(worker_pools, now=seeded["starts"][1] - timedelta(hours=24), execute=execute)

    assert len(whatsapp.sent) == 2


# --------------------------------------------------------------------------------------
# What is switched off is not on sale — publicly least of all.
# --------------------------------------------------------------------------------------


async def test_a_deactivated_event_type_is_not_bookable_and_not_listed(
    public_client: AsyncClient, owner_maker: Sessionmaker, acme: dict[str, Any]
) -> None:
    sessionmaker: Sessionmaker = owner_maker
    async with sessionmaker() as session, session.begin():
        row = await session.scalar(select(EventType).where(EventType.id == acme["event_type_id"]))
        assert row is not None
        row.active = False

    listing = await public_client.get(f"/api/v1/public/{acme['tenant_slug']}/event-types")
    booking = await public_client.post(
        _public(acme, "/bookings"), json=_payload(acme), headers=_as_guest()
    )

    assert listing.json() == []
    assert booking.status_code == 404


async def test_the_public_booking_confirms_directly_because_it_is_free(
    public_client: AsyncClient, acme: dict[str, Any], owner_maker: Sessionmaker
) -> None:
    """Criterion 17's premise, pinned: today's booking confirms DIRECTLY. There is no ``pending``
    on this path — so any abuse ceiling built on "unpaid holds" would guard exactly nothing."""
    response = await public_client.post(
        _public(acme, "/bookings"), json=_payload(acme), headers=_as_guest()
    )

    assert (await _stored(owner_maker, response.json()["id"])).status is BookingStatus.CONFIRMED
