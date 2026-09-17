"""Tests for Microsoft 365 / Graph API calendar integration (Horizon 1).

Covers:
- Pure parsing and payload construction in `integrations/microsoft/parse.py`
- Fail-closed refusal on missing/erroneous schedule responses (RF-13: unknown is never free)
- Microsoft Teams online meeting generation and link extraction
- Client transport and idempotent deletion (404/410 already gone)
- Integration with service layer (`store_microsoft_connection`, `read_busy` fail-closed,
  event lifecycle)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import TimeInterval
from aethercal.server.db.models import Tenant, User
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.integrations.microsoft.client import (
    delete_event,
    insert_event,
    query_busy,
)
from aethercal.server.integrations.microsoft.parse import (
    MicrosoftEventRequest,
    _parse_graph_datetime,
    build_graph_event_body,
    build_schedule_request_body,
    extract_teams_join_url,
    parse_graph_schedule,
)
from aethercal.server.services.calendars import (
    BUSY_PROVIDERS,
    MICROSOFT_PROVIDER,
    BusyQuery,
    BusyStatus,
    MicrosoftCredential,
    _busy_query_for,
    create_event_for_booking,
    delete_event_for_booking,
    read_busy,
    store_microsoft_connection,
)

_FERNET = Fernet(Fernet.generate_key())


# --------------------------------------------------------------------------------------
# 1. Pure parse tests (integrations/microsoft/parse.py)
# --------------------------------------------------------------------------------------


def test_parse_graph_schedule_extracts_busy_oof_and_tentative() -> None:
    response = {
        "value": [
            {
                "scheduleId": "host@example.com",
                "availabilityView": "0220",
                "scheduleItems": [
                    {
                        "status": "free",
                        "start": {"dateTime": "2026-09-16T09:00:00.0000000", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T10:00:00.0000000", "timeZone": "UTC"},
                    },
                    {
                        "status": "busy",
                        "start": {"dateTime": "2026-09-16T10:00:00.0000000", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T11:00:00.0000000", "timeZone": "UTC"},
                    },
                    {
                        "status": "tentative",
                        "start": {"dateTime": "2026-09-16T11:30:00.0000000", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T12:00:00.0000000", "timeZone": "UTC"},
                    },
                    {
                        "status": "oof",
                        "start": {"dateTime": "2026-09-16T14:00:00.0000000Z", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T17:00:00.0000000Z", "timeZone": "UTC"},
                    },
                    {
                        "status": "workingElsewhere",
                        "start": {"dateTime": "2026-09-16T17:00:00", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T18:00:00", "timeZone": "UTC"},
                    },
                ],
            }
        ]
    }

    intervals = parse_graph_schedule(response, schedule_id="host@example.com")
    assert len(intervals) == 4

    assert intervals[0] == TimeInterval(
        start=datetime(2026, 9, 16, 10, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 11, 0, tzinfo=UTC),
    )
    assert intervals[1] == TimeInterval(
        start=datetime(2026, 9, 16, 11, 30, tzinfo=UTC),
        end=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
    )
    assert intervals[2] == TimeInterval(
        start=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 17, 0, tzinfo=UTC),
    )
    assert intervals[3] == TimeInterval(
        start=datetime(2026, 9, 16, 17, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    )


def test_parse_graph_schedule_fail_closed_on_errors() -> None:
    # Top-level API error
    with pytest.raises(RuntimeError, match="top-level error"):
        parse_graph_schedule({"error": {"code": "InvalidRequest", "message": "Bad token"}})

    # Empty value list
    with pytest.raises(RuntimeError, match="no schedule entries"):
        parse_graph_schedule({"value": []})

    # Missing targeted scheduleId
    with pytest.raises(RuntimeError, match="omitted schedule"):
        parse_graph_schedule(
            {"value": [{"scheduleId": "other@example.com", "scheduleItems": []}]},
            schedule_id="target@example.com",
        )

    # Per-entry error
    with pytest.raises(RuntimeError, match="reported error"):
        parse_graph_schedule(
            {
                "value": [
                    {
                        "scheduleId": "target@example.com",
                        "error": {"message": "Mailbox unavailable"},
                    }
                ]
            },
            schedule_id="target@example.com",
        )


def test_an_unknown_status_blocks_time_instead_of_reading_as_free() -> None:
    """==El defecto que este test fija: la lista de estados "ocupado" estaba al revés.==

    Con una allow-list de ocupados, cualquier estado nuevo (o ``unknown``, que Graph documenta)
    caía del lado del tiempo libre — el fallo abierto que produce una doble reserva. Ahora solo
    ``free`` es libre y el resto bloquea, aunque el parser nunca haya visto el estado.
    """
    response = {
        "value": [
            {
                "scheduleId": "host@example.com",
                "scheduleItems": [
                    {
                        "status": "unknown",
                        "start": {"dateTime": "2026-09-16T10:00:00Z", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T11:00:00Z", "timeZone": "UTC"},
                    },
                    {
                        "status": "aStatusFromTheFuture",
                        "start": {"dateTime": "2026-09-16T12:00:00Z", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T13:00:00Z", "timeZone": "UTC"},
                    },
                ],
            }
        ]
    }

    intervals = parse_graph_schedule(response, schedule_id="host@example.com")

    assert [i.start for i in intervals] == [
        datetime(2026, 9, 16, 10, 0, tzinfo=UTC),
        datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
    ]


def test_a_non_free_item_without_usable_instants_aborts_the_query() -> None:
    """Un bloque no libre sin instantes válidos no se puede nombrar: adivinar sería reservar a
    ciegas, así que la consulta entera se rechaza (fail-closed)."""
    missing_instants = {
        "value": [
            {
                "scheduleId": "host@example.com",
                "scheduleItems": [
                    {
                        "status": "busy",
                        "start": {"dateTime": "2026-09-16T10:00:00Z", "timeZone": "UTC"},
                    }
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="no valid start/end instants"):
        parse_graph_schedule(missing_instants, schedule_id="host@example.com")

    # Un ítem que no es un objeto tampoco se puede interpretar.
    with pytest.raises(RuntimeError, match="not an object"):
        parse_graph_schedule(
            {"value": [{"scheduleId": "host@example.com", "scheduleItems": ["busy"]}]},
            schedule_id="host@example.com",
        )

    # Una entrada sin la lista de ítems es una respuesta malformada, no un día vacío.
    with pytest.raises(RuntimeError, match="no usable 'scheduleItems'"):
        parse_graph_schedule({"value": [{"scheduleId": "host@example.com"}]})

    # Extremos invertidos: la ventana es un sinsentido y no se adivina.
    with pytest.raises(RuntimeError, match="end precedes its start"):
        parse_graph_schedule(
            {
                "value": [
                    {
                        "scheduleId": "host@example.com",
                        "scheduleItems": [
                            {
                                "status": "busy",
                                "start": {"dateTime": "2026-09-16T12:00:00Z", "timeZone": "UTC"},
                                "end": {"dateTime": "2026-09-16T11:00:00Z", "timeZone": "UTC"},
                            }
                        ],
                    }
                ]
            }
        )


def test_build_schedule_request_body() -> None:
    w = TimeInterval(
        start=datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        end=datetime(2026, 9, 23, 18, 0, tzinfo=UTC),
    )
    body = build_schedule_request_body(["doctor@clinic.com"], w, availability_view_interval=30)
    assert body["schedules"] == ["doctor@clinic.com"]
    assert body["startTime"]["dateTime"] == "2026-09-16T09:00:00Z"
    assert body["endTime"]["dateTime"] == "2026-09-23T18:00:00Z"
    assert body["availabilityViewInterval"] == 30


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-16T14:00:00", datetime(2026, 9, 16, 14, 0, tzinfo=UTC)),
        ("2026-09-16T14:00:00Z", datetime(2026, 9, 16, 14, 0, tzinfo=UTC)),
        ("2026-09-16T14:00:00.0000000Z", datetime(2026, 9, 16, 14, 0, tzinfo=UTC)),
        ("2026-09-16T14:00:00.0000000+02:00", datetime(2026, 9, 16, 12, 0, tzinfo=UTC)),
        ("2026-09-16T14:00:00+02:00", datetime(2026, 9, 16, 12, 0, tzinfo=UTC)),
    ],
)
def test_graph_datetimes_parse_in_every_shape_graph_actually_sends(
    raw: str, expected: datetime
) -> None:
    """==Graph mezcla formatos, y el parser tiene que aceptarlos todos.== Con y sin fracción
    (``fromisoformat`` acepta 6 dígitos o ninguno), con ``Z`` o con offset explícito. La única
    cirugía es el séptimo dígito de la fracción, que Python no acepta y Microsoft emite siempre; el
    resto se delega al parser estándar, que ya prueba la suite — en vez de una regex propia."""
    assert _parse_graph_datetime(raw) == expected


def test_build_graph_event_body_and_extract_teams_join_url() -> None:
    req = MicrosoftEventRequest(
        summary="Consulta médica",
        start=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 14, 30, tzinfo=UTC),
        timezone="America/New_York",
        guest_email="paciente@example.com",
        guest_name="Ana Gómez",
        body_content="Detalles de la cita médica.",
        is_online_meeting=True,
    )

    body = build_graph_event_body(req)
    assert body["subject"] == "Consulta médica"
    assert body["isOnlineMeeting"] is True
    assert body["onlineMeetingProvider"] == "teamsForBusiness"
    assert body["attendees"][0]["emailAddress"]["address"] == "paciente@example.com"
    assert body["attendees"][0]["emailAddress"]["name"] == "Ana Gómez"
    assert body["body"]["content"] == "Detalles de la cita médica."

    # Test teams link extraction
    sample_response = {
        "id": "AAMkAD12345",
        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/19%3ameeting..."},
    }
    url = extract_teams_join_url(sample_response)
    assert url == "https://teams.microsoft.com/l/meetup-join/19%3ameeting..."

    # Fallback onlineMeetingUrl field
    assert extract_teams_join_url({"onlineMeetingUrl": "https://teams.microsoft.com/fallback"}) == (
        "https://teams.microsoft.com/fallback"
    )
    assert extract_teams_join_url({}) is None


# --------------------------------------------------------------------------------------
# 2. Client operations (client.py)
# --------------------------------------------------------------------------------------


class FakeMicrosoftService:
    def __init__(self) -> None:
        self.schedule_response: dict[str, Any] = {}
        self.created_events: list[dict[str, Any]] = []
        self.deleted_events: list[tuple[str, str]] = []
        self.should_404_on_delete = False
        self.should_error_on_delete = False
        self.generic_error: Exception | None = None

    def get_schedule(self, schedule_id: str, window: TimeInterval) -> dict[str, Any]:
        return self.schedule_response

    def create_event(self, schedule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        created = dict(body)
        created["id"] = "ms-evt-999"
        created["onlineMeeting"] = {"joinUrl": "https://teams.microsoft.com/l/meetup-join/999"}
        self.created_events.append(created)
        return created

    def delete_event(self, schedule_id: str, event_id: str) -> None:
        if self.generic_error is not None:
            raise self.generic_error
        if self.should_404_on_delete:
            req = httpx.Request("DELETE", f"https://graph.microsoft.com/v1.0/me/events/{event_id}")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)
        if self.should_error_on_delete:
            req = httpx.Request("DELETE", f"https://graph.microsoft.com/v1.0/me/events/{event_id}")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Internal Error", request=req, response=resp)
        self.deleted_events.append((schedule_id, event_id))


class _SdkStyleError(Exception):
    """An error shaped like a vendor SDK's: the status lives on ``.response``."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = httpx.Response(
            status_code, request=httpx.Request("DELETE", "https://graph.microsoft.com/v1.0/x")
        )


def test_client_query_busy_and_event_lifecycle() -> None:
    svc = FakeMicrosoftService()
    svc.schedule_response = {
        "value": [
            {
                "scheduleId": "doctor@clinic.com",
                "scheduleItems": [
                    {
                        "status": "busy",
                        "start": {"dateTime": "2026-09-16T14:00:00Z", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-16T15:00:00Z", "timeZone": "UTC"},
                    }
                ],
            }
        ]
    }

    w = TimeInterval(
        start=datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    )
    intervals = query_busy(svc, "doctor@clinic.com", w)
    assert len(intervals) == 1
    assert intervals[0].start == datetime(2026, 9, 16, 14, 0, tzinfo=UTC)

    # Insert event
    req = MicrosoftEventRequest(
        summary="Cita",
        start=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 14, 30, tzinfo=UTC),
        timezone="UTC",
        guest_email="guest@example.com",
    )
    event_id, teams_url = insert_event(svc, "doctor@clinic.com", req)
    assert event_id == "ms-evt-999"
    assert teams_url == "https://teams.microsoft.com/l/meetup-join/999"

    # Delete event
    delete_event(svc, "doctor@clinic.com", event_id)
    assert ("doctor@clinic.com", "ms-evt-999") in svc.deleted_events

    # Idempotent 404 delete does not raise
    svc.should_404_on_delete = True
    delete_event(svc, "doctor@clinic.com", "ms-evt-999")

    # 500 error raises
    svc.should_404_on_delete = False
    svc.should_error_on_delete = True
    with pytest.raises(httpx.HTTPStatusError):
        delete_event(svc, "doctor@clinic.com", "ms-evt-999")


def test_delete_event_reads_the_already_gone_status_from_the_RESPONSE_too() -> None:
    """La firma «ya no existe» puede llegar en la excepción o en su respuesta; las dos formas son
    el mismo hecho, y una de ellas se perdía: se reintentaba contra un evento que ya no está."""
    svc = FakeMicrosoftService()

    svc.generic_error = _SdkStyleError(404)
    delete_event(svc, "doctor@clinic.com", "ms-evt-404")  # no raise

    svc.generic_error = _SdkStyleError(410)
    delete_event(svc, "doctor@clinic.com", "ms-evt-410")  # no raise

    svc.generic_error = _SdkStyleError(500)
    with pytest.raises(_SdkStyleError):
        delete_event(svc, "doctor@clinic.com", "ms-evt-500")


# --------------------------------------------------------------------------------------
# 3. Service layer integration (services/calendars.py)
# --------------------------------------------------------------------------------------


@pytest.fixture
async def sample_user(
    sqlite_session: AsyncSession,
) -> tuple[Tenant, User]:
    tenant = Tenant(id=uuid.uuid4(), name="Clinica Salud", slug=f"clinica-{uuid.uuid4().hex[:6]}")
    sqlite_session.add(tenant)
    await sqlite_session.flush()

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Dr. Medico",
        email="medico@clinica.com",
        timezone="UTC",
        hashed_password="hash",
    )
    sqlite_session.add(user)
    await sqlite_session.flush()
    return tenant, user


@pytest.mark.asyncio
async def test_store_microsoft_connection_and_registration(
    sqlite_session: AsyncSession,
    sample_user: tuple[Tenant, User],
) -> None:
    tenant, user = sample_user

    assert MICROSOFT_PROVIDER in BUSY_PROVIDERS
    assert _busy_query_for(MICROSOFT_PROVIDER) is not None

    cred = MicrosoftCredential(
        account_email="medico@clinica.com",
        token_json='{"access_token": "ms-tok-abc", "refresh_token": "ms-ref-123"}',
    )
    conn = await store_microsoft_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=user.id,
        credential=cred,
        fernet=_FERNET,
    )
    assert conn.provider == MICROSOFT_PROVIDER
    assert conn.account_email == "medico@clinica.com"
    assert conn.revoked_at is None

    # Decrypt and verify
    decrypted = _FERNET.decrypt(conn.encrypted_credentials).decode("utf-8")
    assert "ms-tok-abc" in decrypted


@pytest.mark.asyncio
async def test_microsoft_calendar_create_and_delete_event_lifecycle() -> None:
    svc = FakeMicrosoftService()
    req = MeetEventRequest(
        summary="Reunión Virtual",
        start=datetime(2026, 9, 16, 15, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 15, 30, tzinfo=UTC),
        timezone="America/New_York",
        guest_email="invitado@example.com",
    )

    event_id, teams_url = await create_event_for_booking(
        calendar_id="primary",
        request=req,
        service=svc,
        provider=MICROSOFT_PROVIDER,
    )
    assert event_id == "ms-evt-999"
    assert teams_url == "https://teams.microsoft.com/l/meetup-join/999"

    # Delete via service layer
    await delete_event_for_booking(
        calendar_id="primary",
        external_event_id=event_id,
        service=svc,
        provider=MICROSOFT_PROVIDER,
    )
    assert ("primary", "ms-evt-999") in svc.deleted_events


@pytest.mark.asyncio
async def test_read_busy_fail_closed_for_microsoft_connection(
    sqlite_session: AsyncSession,
    sample_user: tuple[Tenant, User],
) -> None:
    tenant, user = sample_user
    cred = MicrosoftCredential(account_email="medico@clinica.com", token_json="ms-token")
    await store_microsoft_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=user.id,
        credential=cred,
        fernet=_FERNET,
    )

    failing_svc = FakeMicrosoftService()
    failing_svc.schedule_response = {"error": {"message": "Service unavailable"}}

    w = TimeInterval(
        start=datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        end=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    )
    query = BusyQuery(
        window=w,
        now=datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
        ttl=timedelta(minutes=5),
    )

    # Calling read_busy when the Microsoft API is down MUST degrade fail-closed to UNAVAILABLE
    res = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=query,
        service_factory=lambda conn: failing_svc,
    )
    assert res.status == BusyStatus.UNAVAILABLE
    assert res.is_available is False  # Refuses host's slots to prevent double-booking
