"""The guest's own reply gets acknowledged — through the FULL outbound belt (Horizon 1 follow-up).

An acknowledgement is an outbound message, so it is not fired off the webhook: it is queued as an
outbox intent (``NOTIFY_REPLY``) and drained like every other send, which is what puts consent, the
opt-out list, the daily caps and the ledger between the product and a real phone. These tests pin
the two properties that make the feature safe rather than merely working:

* the SEAL is deliberately not demanded (the inbound proves possession better than the OTP it would
  demand) — and everything else still is;
* the ledger's step-less key makes it exactly-once per (booking, kind, channel), so a replayed
  provider webhook, a re-drained intent or a guest who answers "1" twice sends ONE message.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.channels import Channel
from aethercal.server.db.migrate import run_migrations
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Schedule,
    SentNotification,
    Tenant,
    User,
)
from aethercal.server.db.pools import WorkerPools
from aethercal.server.integrations.messaging.guard import DailyCaps
from aethercal.server.services.bookings import BookingParams, create_booking
from aethercal.server.services.outbox import (
    OutboxReport,
    drain_outbox,
    enqueue_guest_reply,
    make_booking_effect_executor,
)
from aethercal.server.services.phone_verification import suppress_phone
from aethercal.server.services.templates import (
    _BUILTIN_PHONE_BODIES,
    REPLY_CANCEL_KIND,
    REPLY_CONFIRM_KIND,
)
from aethercal.server.services.tenant_senders import TenantSenders
from aethercal.server.services.whatsapp_interactive import extract_provider_message_id

Sessionmaker = async_sessionmaker[AsyncSession]
_NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


def _pools(maker: Sessionmaker) -> WorkerPools:
    """Both of the drain's pools over ONE offline sessionmaker (SQLite has no roles to split)."""
    return WorkerPools.for_offline_tests(maker)


class _RecordingWhatsApp:
    """A configured WhatsApp channel, so "nobody could send it" is never why a test passes."""

    channel = Channel.WHATSAPP

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.caps = DailyCaps(per_phone=100, per_ip=100)

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        del subject
        self.sent.append((to, body))


class _RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest_asyncio.fixture
async def migrated(tmp_path: Path) -> AsyncIterator[Sessionmaker]:
    path = tmp_path / "guest_reply.sqlite"
    sync_engine = sa.create_engine(f"sqlite:///{path}")
    run_migrations(sync_engine)
    sync_engine.dispose()
    engine = sa.ext.asyncio.create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _booked_with_phone(
    maker: Sessionmaker, *, phone: str | None = "+13055551234", consent: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    """A confirmed booking with a phone (and, by default, the consent box ticked) — and ==no OTP
    seal==, which is the state this feature has to work in: the guest answered from that phone."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Acme")
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
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()
        booking = await create_booking(
            session,
            tenant_id=tenant.id,
            params=BookingParams(
                event_type_id=event_type.id,
                start=_SLOT,
                guest_name="Ana",
                guest_email="ana@example.com",
                guest_timezone="UTC",
                guest_phone=phone,
                guest_phone_consent=consent,
            ),
            now=_NOW,
        )
        return tenant.id, booking.id


async def _drain(maker: Sessionmaker, whatsapp: _RecordingWhatsApp) -> OutboxReport:
    execute = make_booking_effect_executor(
        sessionmaker=maker,
        resolve_senders=TenantSenders.for_offline_tests(
            email=_RecordingEmail(), channels={Channel.WHATSAPP: whatsapp}
        ),
        service_factory=None,
    )
    return await drain_outbox(_pools(maker), now=_NOW, execute=execute)


async def _enqueue(maker: Sessionmaker, booking_id: uuid.UUID, *, kind: str, key: str) -> None:
    async with maker() as session, session.begin():
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        await enqueue_guest_reply(
            session,
            booking=booking,
            channel=Channel.WHATSAPP,
            kind=kind,
            dedupe_key=key,
        )


async def test_a_confirmed_reply_is_sent_through_the_belt_without_the_OTP_seal(
    migrated: Sessionmaker,
) -> None:
    """==El caso que define la exención:== el huésped respondió DESDE ese teléfono, así que el sello
    de posesión no se exige — pero el resto del cinturón sí: consentimiento, lista de bajas, topes y
    libro. Sin la exención, todo huésped que no completó el OTP quedaría sin acuse."""
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    await _enqueue(migrated, booking_id, kind=REPLY_CONFIRM_KIND, key="reply:reply_confirm:m1")

    whatsapp = _RecordingWhatsApp()
    report = await _drain(migrated, whatsapp)

    assert len(report.delivered) == 1
    assert len(whatsapp.sent) == 1, "el huésped respondió y no recibió acuse"
    recipient, body = whatsapp.sent[0]
    assert recipient == "+13055551234"
    assert body.strip() and "{{" not in body
    assert "Ana" in body  # el cuerpo viene del built-in, con el contexto del booking

    async with migrated() as session:
        ledger = list((await session.scalars(sa.select(SentNotification))).all())
    assert [row.kind for row in ledger] == [REPLY_CONFIRM_KIND]
    assert ledger[0].step_id is None
    assert ledger[0].channel == Channel.WHATSAPP.value


async def test_two_inbound_messages_of_the_same_kind_send_only_ONE_acknowledgement(
    migrated: Sessionmaker,
) -> None:
    """==El libro es la autoridad de exactitud:== la clave sin ``step_id`` es (reserva, tipo,
    así que un webhook reintentado — o un huésped que responde "1" dos veces — produce UN mensaje.
    Las dos filas del outbox se liquidan (una envía, la otra encuentra el libro escrito)."""
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    await _enqueue(migrated, booking_id, kind=REPLY_CONFIRM_KIND, key="reply:reply_confirm:m1")
    await _enqueue(migrated, booking_id, kind=REPLY_CONFIRM_KIND, key="reply:reply_confirm:m2")

    whatsapp = _RecordingWhatsApp()
    report = await _drain(migrated, whatsapp)

    assert report.attempted == 2
    assert len(whatsapp.sent) == 1, "el huésped recibió el mismo acuse dos veces"

    async with migrated() as session:
        rows = list(
            (await session.scalars(sa.select(Outbox).where(Outbox.effect == "notify_reply"))).all()
        )
    assert len(rows) == 2
    assert all(row.status == "delivered" for row in rows), [row.status for row in rows]


async def test_a_SUPPRESSED_phone_gets_no_acknowledgement(migrated: Sessionmaker) -> None:
    """La supresión gana: quien pidió silencio no recibe ni el acuse de su propio mensaje."""
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    async with migrated() as session, session.begin():
        await suppress_phone(session, "+13055551234")
    await _enqueue(migrated, booking_id, kind=REPLY_CONFIRM_KIND, key="reply:reply_confirm:m1")

    whatsapp = _RecordingWhatsApp()
    await _drain(migrated, whatsapp)

    assert whatsapp.sent == []
    async with migrated() as session:
        row = (
            await session.scalars(sa.select(Outbox).where(Outbox.effect == "notify_reply"))
        ).one()
    assert row.status == "skipped"
    assert "phone-suppressed" in (row.skip_reason or "")


async def test_no_consent_means_no_acknowledgement(migrated: Sessionmaker) -> None:
    """==El consentimiento NO se exime.== A quien nunca autorizó mensajes no se le escribe — ni
    siquiera para acusar recibo: su propio mensaje entrante no crea un permiso que no dio."""
    _tenant_id, booking_id = await _booked_with_phone(migrated, consent=False)
    await _enqueue(migrated, booking_id, kind=REPLY_CANCEL_KIND, key="reply:reply_cancel:m1")

    whatsapp = _RecordingWhatsApp()
    await _drain(migrated, whatsapp)

    assert whatsapp.sent == []
    async with migrated() as session:
        row = (
            await session.scalars(sa.select(Outbox).where(Outbox.effect == "notify_reply"))
        ).one()
    assert row.status == "skipped"
    assert "no-phone-consent" in (row.skip_reason or "")


async def test_the_cancel_acknowledgement_has_its_own_body(migrated: Sessionmaker) -> None:
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    await _enqueue(migrated, booking_id, kind=REPLY_CANCEL_KIND, key="reply:reply_cancel:m1")

    whatsapp = _RecordingWhatsApp()
    await _drain(migrated, whatsapp)

    assert len(whatsapp.sent) == 1
    body = whatsapp.sent[0][1]
    assert "cancel" in body.lower()


async def test_the_CANCELLED_booking_still_gets_its_cancellation_acknowledgement(
    migrated: Sessionmaker,
) -> None:
    """==La trampa que este test existe para atrapar.== En producción el "2" del huésped CANCELA la
    reserva antes de que el intento se drene, y ``_is_chain_current`` es False para una cancelada
    *por construcción*. Con una política SUBJECT el acuse quedaba marcado como entregado y NUNCA
    salía: el huésped que canceló no recibía nada. Por eso ``reply_cancel`` es EXEMPT — y por eso
    esta prueba reproduce el ORDEN real (cancelar primero, drenar después), que es justo lo que la
    prueba de cuerpo de arriba no hace."""
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    await _enqueue(migrated, booking_id, kind=REPLY_CANCEL_KIND, key="reply:reply_cancel:m1")

    async with migrated() as session, session.begin():
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = _NOW

    whatsapp = _RecordingWhatsApp()
    report = await _drain(migrated, whatsapp)

    assert len(report.delivered) == 1
    assert len(whatsapp.sent) == 1, "el huésped canceló y no recibió acuse"
    assert "cancel" in whatsapp.sent[0][1].lower()


async def test_a_confirmation_acknowledgement_is_NOT_sent_after_the_booking_was_cancelled(
    migrated: Sessionmaker,
) -> None:
    """La otra mitad de la clasificación por tipo: el acuse de confirmación HABLA de una cita que
    debe ocurrir, así que si la cadena se movió, "gracias, tu asistencia está confirmada" sobre una
    reserva cancelada es sencillamente falso. SUBJECT, y no sale."""
    _tenant_id, booking_id = await _booked_with_phone(migrated)
    await _enqueue(migrated, booking_id, kind=REPLY_CONFIRM_KIND, key="reply:reply_confirm:m1")

    async with migrated() as session, session.begin():
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = _NOW

    whatsapp = _RecordingWhatsApp()
    report = await _drain(migrated, whatsapp)

    assert whatsapp.sent == [], "'asistencia confirmada' sobre una reserva cancelada"
    assert len(report.skipped) == 1
    async with migrated() as session:
        row = (
            await session.scalars(sa.select(Outbox).where(Outbox.effect == "notify_reply"))
        ).one()
    assert row.status == "skipped"


def test_every_reply_kind_has_a_body_in_every_locale() -> None:
    """Un tipo sin built-in se salta con ``no-template``: el acuse nunca sale, en silencio y para
    todos los inquilinos. Esta prueba lo convierte en rojo al añadir un tipo."""
    for kind in (REPLY_CONFIRM_KIND, REPLY_CANCEL_KIND):
        for language in ("es", "en"):
            body = _BUILTIN_PHONE_BODIES.get((kind, language))
            assert body and body.strip(), f"({kind}, {language}) has no built-in body"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": {"key": {"id": "3EB0ABC"}}}, "3EB0ABC"),
        ({"data": {"key": {"id": "  x  "}}}, "x"),
        ({"data": {"key": {}}}, None),
        ({"data": {}}, None),
        ({}, None),
        ({"data": {"key": {"id": 12345}}}, None),
        ({"data": {"key": {"id": ""}}}, None),
    ],
)
def test_the_provider_message_id_is_extracted_without_trusting_the_wire(
    payload: dict[str, Any], expected: str | None
) -> None:
    """Es la pieza que hace exactamente-una-vez al acuse: se lee de un payload externo, así que se
    acepta solo si es un texto no vacío — y se acota (la columna del dedupe son 128 chars)."""
    assert extract_provider_message_id(payload) == expected


def test_an_overlong_message_id_is_bounded() -> None:
    raw = "x" * 500
    bounded = extract_provider_message_id({"data": {"key": {"id": raw}}})
    assert bounded is not None and len(bounded) == 120
