"""Tests for WhatsApp interactive bidirectional processing via Evolution API (Horizon 1).

Covers:
- Intent parsing (Spanish primary + English fallback, accents, prefixes "1 - Sí", "2 - No", "STOP").
- Payload extraction from Evolution API (direct message, extended text, button reply, list reply).
- Filtering of self-messages (fromMe=True), group chats (@g.us), and non-message events.
- Service execution:
  * "1" confirms attendance on upcoming booking.
  * "2" cancels booking under lock with cancellation effects.
  * "STOP" adds phone to instance-level suppression list (D-12).
  * Unknown text returns guidance response.
  * Phone with no active booking returns no_booking_found.
- Webhook endpoint HTTP routing, authentication (apikey, x-api-key, Bearer, query token),
  payload size capping (MAX_WEBHOOK_BODY_BYTES), and tenant resolution.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aethercal.core.model import BookingStatus
from aethercal.server.api.webhooks_inbound import (
    MAX_WEBHOOK_BODY_BYTES,
    receive_whatsapp_webhook,
)
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.integrations.messaging.guard import DailyCaps
from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.services.phone_verification import (
    is_phone_suppressed,
)
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    store_credential,
)
from aethercal.server.services.tenant_senders import InstanceSenderDefaults
from aethercal.server.services.whatsapp_interactive import (
    WhatsAppReplyAction,
    extract_evolution_payload,
    parse_reply_action,
    process_inbound_whatsapp,
)
from aethercal.server.settings import Settings

_APP_SECRET = "test-app-secret-12345"
_SUPPRESSION_KEY = "test-suppression-key-67890-0123456789"
_FERNET_KEY = derive_fernet_key(_APP_SECRET)
_TEST_API_KEY = "evo_api_key_secure_12345"


# --------------------------------------------------------------------------------------
# 1. Intent Parsing Tests
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1",
        "1 - Sí",
        "1-si",
        "si",
        "SI",
        "sí",
        "SÍ",
        "yes",
        "YES",
        "confirmo",
        "Confirmo",
        "confirmar",
        "CONFIRMAR",
        "confirm",
        "confirmo asistencia",
        "asistire",
        "asistiré",
    ],
)
def test_parse_reply_action_confirmations(text: str) -> None:
    assert parse_reply_action(text) == WhatsAppReplyAction.CONFIRM_ATTENDANCE


@pytest.mark.parametrize(
    "text",
    [
        "2",
        "2 - No",
        "2-no",
        "no",
        "NO",
        "cancelar",
        "Cancelar",
        "cancelo",
        "cancel",
        "reprogramar",
        "reagendar",
    ],
)
def test_parse_reply_action_cancellations(text: str) -> None:
    assert parse_reply_action(text) == WhatsAppReplyAction.CANCEL


@pytest.mark.parametrize(
    "text",
    [
        "stop",
        "STOP",
        "baja",
        "BAJA",
        "alto",
        "ALTO",
        "desuscribir",
        "unsubscribe",
        "parar",
        "detener",
    ],
)
def test_parse_reply_action_opt_out(text: str) -> None:
    assert parse_reply_action(text) == WhatsAppReplyAction.OPT_OUT


@pytest.mark.parametrize(
    "text",
    [
        "hola",
        "buenas tardes",
        "cuanto cuesta?",
        "ok gracias",
        "",
        "   ",
        "3",
        "informacion",
    ],
)
def test_parse_reply_action_unknown(text: str) -> None:
    assert parse_reply_action(text) == WhatsAppReplyAction.UNKNOWN


# --------------------------------------------------------------------------------------
# 2. Payload Extraction Tests (Evolution API)
# --------------------------------------------------------------------------------------


def test_extract_evolution_payload_conversation() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055551111@s.whatsapp.net",
                "fromMe": False,
                "id": "msg_001",
            },
            "message": {"conversation": "1"},
        },
    }
    extracted = extract_evolution_payload(payload)
    assert extracted == ("13055551111", "1")


def test_extract_evolution_payload_extended_text() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055552222@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {
                "extendedTextMessage": {"text": "Sí, confirmo asistencia"},
            },
        },
    }
    extracted = extract_evolution_payload(payload)
    assert extracted == ("13055552222", "Sí, confirmo asistencia")


def test_extract_evolution_payload_button_reply() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055553333@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {
                "buttonsResponseMessage": {
                    "selectedButtonId": "btn_cancel",
                    "selectedDisplayText": "2 - Cancelar",
                },
            },
        },
    }
    extracted = extract_evolution_payload(payload)
    assert extracted == ("13055553333", "btn_cancel")


def test_extract_evolution_payload_list_reply() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055554444@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {
                "listResponseMessage": {
                    "singleSelectReply": {"selectedRowId": "row_confirm"},
                },
            },
        },
    }
    extracted = extract_evolution_payload(payload)
    assert extracted == ("13055554444", "row_confirm")


def test_extract_evolution_payload_ignores_from_me() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055551111@s.whatsapp.net",
                "fromMe": True,
            },
            "message": {"conversation": "Hola"},
        },
    }
    assert extract_evolution_payload(payload) is None


def test_extract_evolution_payload_ignores_group_messages() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "12036302@g.us",
                "fromMe": False,
            },
            "message": {"conversation": "1"},
        },
    }
    assert extract_evolution_payload(payload) is None


def test_extract_evolution_payload_ignores_non_message_events() -> None:
    payload = {
        "event": "connection.update",
        "data": {"state": "open"},
    }
    assert extract_evolution_payload(payload) is None


def test_extract_evolution_payload_handles_missing_or_empty_text() -> None:
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055551111@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {},
        },
    }
    assert extract_evolution_payload(payload) is None


# --------------------------------------------------------------------------------------
# 3. Service Processing Tests with SQLite Session
# --------------------------------------------------------------------------------------


@pytest.fixture
async def seeded_whatsapp_booking(
    sqlite_session: AsyncSession,
) -> tuple[Tenant, EventType, Booking]:
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    tenant = Tenant(name="WhatsApp Clinic", slug=f"wa-{uuid.uuid4().hex[:8]}")
    sqlite_session.add(tenant)
    await sqlite_session.flush()

    user = User(
        tenant_id=tenant.id,
        name="Dr. Smith",
        email=f"smith-{uuid.uuid4().hex[:6]}@example.com",
        timezone="UTC",
        hashed_password="not-a-password",
    )
    schedule = Schedule(tenant_id=tenant.id, name="Default", timezone="UTC", rules={})
    sqlite_session.add_all([user, schedule])
    await sqlite_session.flush()

    event_type = EventType(
        tenant_id=tenant.id,
        host_id=user.id,
        schedule_id=schedule.id,
        slug="consultation",
        title="Consultation",
        duration_seconds=1800,
        max_advance_seconds=86400 * 30,
        price_cents=0,
    )
    sqlite_session.add(event_type)
    await sqlite_session.flush()

    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=1, minutes=30),
        status=BookingStatus.CONFIRMED,
        confirmed_at=now,
        guest_name="Jane Guest",
        guest_email="jane@example.com",
        guest_phone="+13055551111",
        guest_phone_consent_at=now,
        guest_timezone="UTC",
        source_ip="198.51.100.1",
    )
    sqlite_session.add(booking)
    await sqlite_session.flush()
    return tenant, event_type, booking


@pytest.mark.asyncio
async def test_process_inbound_confirm_attendance(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    result = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="1 - Sí confirmo",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert result.action == WhatsAppReplyAction.CONFIRM_ATTENDANCE
    assert result.status == "attendance_confirmed"
    assert result.booking_id == booking.id
    assert "confirmado tu asistencia" in (result.reply_message or "")

    # The confirmation is PERSISTED, not just announced: before this stamp the handler returned
    # "attendance_confirmed" and wrote nothing (a no-op wearing a success message).
    reloaded = await sqlite_session.get(Booking, booking.id)
    assert reloaded is not None
    assert reloaded.attendance_confirmed_at is not None


@pytest.mark.asyncio
async def test_process_inbound_confirm_is_idempotent_and_keeps_the_first_stamp(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    """A guest may answer "1" twice (or answer "1" and then "confirmo"). The second reply is
    idempotent: it reports success and does NOT move the instant of the first confirmation."""
    tenant, _, booking = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    first = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert first.status == "attendance_confirmed"
    reloaded = await sqlite_session.get(Booking, booking.id)
    assert reloaded is not None
    first_stamp = reloaded.attendance_confirmed_at
    assert first_stamp is not None

    again = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="confirmo",
        suppression_key=_SUPPRESSION_KEY,
        now=now + timedelta(hours=3),
    )
    assert again.status == "attendance_confirmed"
    final = await sqlite_session.get(Booking, booking.id)
    assert final is not None
    assert final.attendance_confirmed_at == first_stamp


@pytest.mark.asyncio
async def test_a_reply_never_acts_on_an_appointment_that_already_started(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    """==Una respuesta tardía no puede cancelar una cita que ya pasó.==

    El recordatorio se manda ANTES de la cita; un "2" que llega después (o un "2" reenviado) no
    tiene cita viva sobre la cual actuar. Antes de este arreglo el buscador caía a la reserva
    pasada más reciente y disparaba toda la cadena de cancelación sobre una visita terminada.
    """
    tenant, _, booking = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    booking.start_at = now - timedelta(days=1)
    booking.end_at = booking.start_at + timedelta(minutes=30)
    await sqlite_session.flush()

    cancel = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="2 - Cancelar cita",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert cancel.status == "no_booking_found"
    assert cancel.booking_id is None

    confirm = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="1 - Sí",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert confirm.status == "no_booking_found"

    # Nada se escribió: ni cancelación ni sello de asistencia sobre una cita pasada.
    reloaded = await sqlite_session.get(Booking, booking.id)
    assert reloaded is not None
    assert reloaded.status == BookingStatus.CONFIRMED
    assert reloaded.cancelled_at is None
    assert reloaded.attendance_confirmed_at is None


@pytest.mark.asyncio
async def test_process_inbound_cancel_booking(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    result = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="2 - Cancelar cita",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert result.action == WhatsAppReplyAction.CANCEL
    assert result.status == "cancelled"
    assert result.booking_id == booking.id
    assert "cancelada con éxito" in (result.reply_message or "")

    # Assert booking status transitioned to CANCELLED in DB
    reloaded = await sqlite_session.get(Booking, booking.id)
    assert reloaded is not None
    assert reloaded.status == BookingStatus.CANCELLED
    assert reloaded.cancelled_at is not None


@pytest.mark.asyncio
async def test_process_inbound_opt_out_suppression(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    phone = "+13055559999"

    assert not await is_phone_suppressed(sqlite_session, phone, suppression_key=_SUPPRESSION_KEY)

    result = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone=phone,
        message_text="STOP",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert result.action == WhatsAppReplyAction.OPT_OUT
    assert result.status == "suppressed"
    assert result.booking_id is None

    # Assert phone is now suppressed instance-wide
    assert await is_phone_suppressed(sqlite_session, phone, suppression_key=_SUPPRESSION_KEY)


@pytest.mark.asyncio
async def test_process_inbound_no_booking_found(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    result = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+19999999999",
        message_text="1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert result.action == WhatsAppReplyAction.CONFIRM_ATTENDANCE
    assert result.status == "no_booking_found"
    assert result.booking_id is None
    assert "No encontramos una cita" in (result.reply_message or "")


@pytest.mark.asyncio
async def test_process_inbound_unknown_action(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    result = await process_inbound_whatsapp(
        sqlite_session,
        tenant_id=tenant.id,
        sender_phone="+13055551111",
        message_text="hola buenas tardes",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    assert result.action == WhatsAppReplyAction.UNKNOWN
    assert result.status == "ignored"
    assert "Responde 1" in (result.reply_message or "")


# --------------------------------------------------------------------------------------
# 4. Webhook Endpoint HTTP Handler Tests
# --------------------------------------------------------------------------------------


class _DummyAppState:
    """Mock app state holding settings, fernet_keys, and sender_defaults."""

    def __init__(
        self,
        *,
        app_secret: str = _APP_SECRET,
        sender_defaults: InstanceSenderDefaults | None = None,
    ) -> None:
        self.settings = Settings(
            app_secret=app_secret,
            database_url="sqlite+aiosqlite://",
            app_name="AetherCal",
            suppression_key=_SUPPRESSION_KEY,
        )
        self.fernet_keys = (_FERNET_KEY,)
        self.sender_defaults = sender_defaults


def _build_webhook_request(
    *,
    body: bytes,
    headers: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
    app_state: _DummyAppState | None = None,
) -> Request:
    """Build a Starlette Request with streamed body and injected state."""
    hdr_list = list((headers or {}).items())
    hdr_bytes = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hdr_list]

    state = app_state or _DummyAppState()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/whatsapp/test-slug",
        "headers": hdr_bytes,
        "query_string": (
            "&".join(f"{k}={v}" for k, v in (query_params or {}).items()).encode("ascii")
        ),
        "app": type("App", (), {"state": state}),
    }
    return Request(scope, receive=receive)


@pytest.mark.asyncio
async def test_endpoint_unauthorized_if_tenant_unknown(
    sqlite_session: AsyncSession,
) -> None:
    req = _build_webhook_request(body=b"{}")
    with pytest.raises(HTTPException) as exc_info:
        await receive_whatsapp_webhook("non-existent-tenant", req, sqlite_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_unauthorized_if_whatsapp_unconfigured(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    req = _build_webhook_request(body=b"{}")
    with pytest.raises(HTTPException) as exc_info:
        await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_unauthorized_with_wrong_api_key(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    # Store WhatsApp credential for tenant
    await store_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.WHATSAPP,
        secrets={
            "base_url": "https://evolution.example.com",
            "instance": "test-instance",
            "api_key": _TEST_API_KEY,
        },
        fernet_key=_FERNET_KEY,
        current_implementations={},
    )
    await sqlite_session.flush()

    req = _build_webhook_request(
        body=b"{}",
        headers={"apikey": "wrong-key"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_payload_too_large_rejected(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    req = _build_webhook_request(
        body=b"{}",
        headers={"content-length": str(MAX_WEBHOOK_BODY_BYTES + 100)},
    )
    with pytest.raises(HTTPException) as exc_info:
        await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth_kwargs",
    [
        {"headers": {"apikey": _TEST_API_KEY}},
        {"headers": {"x-api-key": _TEST_API_KEY}},
        {"headers": {"authorization": f"Bearer {_TEST_API_KEY}"}},
    ],
)
async def test_endpoint_authenticates_with_various_headers(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
    auth_kwargs: dict[str, Any],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    await store_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.WHATSAPP,
        secrets={
            "base_url": "https://evolution.example.com",
            "instance": "test-instance",
            "api_key": _TEST_API_KEY,
        },
        fernet_key=_FERNET_KEY,
        current_implementations={},
    )
    await sqlite_session.flush()

    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055551111@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {"conversation": "1 - Confirmar"},
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")

    req = _build_webhook_request(body=raw_body, **auth_kwargs)
    resp = await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert resp["status"] == "attendance_confirmed"
    assert resp["action"] == "confirm_attendance"


@pytest.mark.asyncio
@pytest.mark.parametrize("query_params", [{"apikey": _TEST_API_KEY}, {"token": _TEST_API_KEY}])
async def test_endpoint_REFUSES_an_api_key_in_the_query_string(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
    query_params: dict[str, str],
) -> None:
    """==Un secreto en la URL es un secreto en cada log del camino.== El proveedor manda el header
    igual de bien, así que la clave SOLO se acepta por header; los query params se rechazan."""
    tenant, _, _ = seeded_whatsapp_booking
    await store_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.WHATSAPP,
        secrets={
            "base_url": "https://evolution.example.com",
            "instance": "test-instance",
            "api_key": _TEST_API_KEY,
        },
        fernet_key=_FERNET_KEY,
        current_implementations={},
    )
    await sqlite_session.flush()

    req = _build_webhook_request(body=b"{}", query_params=query_params)
    with pytest.raises(HTTPException) as exc_info:
        await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_falls_back_to_instance_default(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking

    defaults = InstanceSenderDefaults(
        smtp=None,
        whatsapp=EvolutionConfig(
            base_url="https://instance-evo.example.com",
            instance="inst-01",
            api_key="instance_secret_key",
            caps=DailyCaps(per_phone=10, per_ip=50),
        ),
        sms=None,
        phone_caps={},
        lend_operator_phone_identity=True,
    )
    app_state = _DummyAppState(sender_defaults=defaults)

    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "13055551111@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {"conversation": "2 - Cancelar"},
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    req = _build_webhook_request(
        body=raw_body,
        headers={"apikey": "instance_secret_key"},
        app_state=app_state,
    )
    resp = await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert resp["status"] == "cancelled"
    assert resp["action"] == "cancel"


@pytest.mark.asyncio
async def test_endpoint_ignores_malformed_json_gracefully(
    sqlite_session: AsyncSession,
    seeded_whatsapp_booking: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, _ = seeded_whatsapp_booking
    await store_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.WHATSAPP,
        secrets={
            "base_url": "https://evolution.example.com",
            "instance": "test-instance",
            "api_key": _TEST_API_KEY,
        },
        fernet_key=_FERNET_KEY,
        current_implementations={},
    )
    await sqlite_session.flush()

    req = _build_webhook_request(
        body=b"not-json-content",
        headers={"apikey": _TEST_API_KEY},
    )
    resp = await receive_whatsapp_webhook(tenant.slug, req, sqlite_session)
    assert resp == {"status": "ignored"}
