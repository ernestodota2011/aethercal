"""Tests for the phone possession verification module (C-02b, RF-24).

Covers:
- OTP generation, HMAC hashing, CSPRNG 6-digit codes.
- Anti-enumeration: identical byte-for-byte error (D-6, A-3).
- 5 attempts limit and burning upon 5th failure (A-2).
- Rate limits: 1 per 60s, 3 per 24h per phone, 20 per 24h per IP (D-7, A-12, A-13).
- Tombstone counting: expired challenge still counts against the 24h cap (D-7·bis, A-12).
- Channel fallback: WhatsApp permanent failure retries via SMS (D-8, D-8·bis).
- Instance opt-out suppression check (D-12).
- Outbox phone gate: skips with reason 'phone-unverified' when unverified (OTP-4).
- Reschedule inheritance (OTP-9).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.db.models.otp import PhoneVerificationChallenge
from aethercal.server.integrations.messaging.guard import (
    PermanentSendError,
)
from aethercal.server.services.outbox import OutboxSkipped, _require_phone_consent
from aethercal.server.services.phone_verification import (
    MAX_PER_IP_24H,
    OTP_TTL,
    IPRateLimitError,
    PhoneVerificationError,
    PhoneVerificationRateLimitError,
    VerificationStatus,
    check_challenge_rate_limits,
    compute_hmac,
    inherit_phone_verification_on_reschedule,
    issue_verification_challenge,
    suppress_phone,
    sweep_stale_challenges,
    verify_phone_code,
)
from aethercal.server.services.tenant_senders import TenantSenders

_APP_SECRET = "test-app-secret-12345"
_SUPPRESSION_KEY = "test-suppression-key-67890-0123456789"


class DummySender:
    def __init__(
        self, channel: Channel, fail_permanent: bool = False, fail_transient: bool = False
    ) -> None:
        self.channel = channel
        self.fail_permanent = fail_permanent
        self.fail_transient = fail_transient
        self.sent_messages: list[dict[str, Any]] = []

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        if self.fail_transient:
            raise RuntimeError(f"provider 500 for {to}: retryable")
        if self.fail_permanent:
            raise PermanentSendError(f"Provider rejected recipient {to}")
        self.sent_messages.append({"to": to, "subject": subject, "body": body})


@pytest_asyncio.fixture
async def seeded_context(sqlite_session: AsyncSession) -> tuple[Tenant, EventType, Booking]:
    tenant = Tenant(slug="acme-corp", name="Acme Corp")
    sqlite_session.add(tenant)
    await sqlite_session.flush()

    host = User(tenant_id=tenant.id, email="host@acme.test", name="Host", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="Default", timezone="UTC", rules={})
    sqlite_session.add_all([host, schedule])
    await sqlite_session.flush()

    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="consultation",
        title="Consultation",
        duration_seconds=1800,
        max_advance_seconds=86400 * 30,
    )
    sqlite_session.add(event_type)
    await sqlite_session.flush()

    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=1, minutes=30),
        status=BookingStatus.CONFIRMED,
        confirmed_at=now,
        guest_name="Jane Doe",
        guest_email="jane@example.com",
        guest_phone="+13055551111",
        guest_phone_consent_at=now,
        guest_timezone="UTC",
        source_ip="198.51.100.1",
    )
    sqlite_session.add(booking)
    await sqlite_session.flush()
    return tenant, event_type, booking


# --------------------------------------------------------------------------------------
# Anti-enumeration & Verification tests
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_otp_verification_seals_booking_and_destroys_secret(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    wa_sender = DummySender(Channel.WHATSAPP)
    senders = TenantSenders(tenant_id=tenant.id, email=None, channels={Channel.WHATSAPP: wa_sender})  # type: ignore[arg-type]

    challenge, channel_used = await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )

    assert channel_used == Channel.WHATSAPP
    assert len(wa_sender.sent_messages) == 1
    msg_body = wa_sender.sent_messages[0]["body"]
    assert "STOP" in msg_body

    # Extract the 6-digit code sent in the message body
    code = ""
    for word in msg_body.split():
        clean_word = word.strip(".:,")
        if clean_word.isdigit() and len(clean_word) == 6:
            code = clean_word
            break
    assert len(code) == 6

    # Verify code
    verified = await verify_phone_code(
        sqlite_session,
        booking_id=booking.id,
        tenant_id=tenant.id,
        code=code,
        app_secret=_APP_SECRET,
        now=now + timedelta(minutes=2),
    )
    assert verified is VerificationStatus.SUCCESS

    expected_time = now + timedelta(minutes=2)
    assert booking.guest_phone_verified_at is not None
    assert (
        booking.guest_phone_verified_at.replace(tzinfo=UTC)
        if booking.guest_phone_verified_at.tzinfo is None
        else booking.guest_phone_verified_at
    ) == expected_time

    # Challenge code_hmac must be destroyed (tombstone)
    await sqlite_session.refresh(challenge)
    assert challenge.code_hmac is None
    assert challenge.consumed_at is not None
    assert (
        challenge.consumed_at.replace(tzinfo=UTC)
        if challenge.consumed_at.tzinfo is None
        else challenge.consumed_at
    ) == expected_time

    # Trying to verify again fails (single-use)
    re_verified = await verify_phone_code(
        sqlite_session,
        booking_id=booking.id,
        tenant_id=tenant.id,
        code=code,
        app_secret=_APP_SECRET,
        now=now + timedelta(minutes=3),
    )
    assert re_verified is VerificationStatus.INVALID


@pytest.mark.asyncio
async def test_wrong_code_increments_attempts_and_burns_after_5_attempts(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    wa_sender = DummySender(Channel.WHATSAPP)
    senders = TenantSenders(tenant_id=tenant.id, email=None, channels={Channel.WHATSAPP: wa_sender})  # type: ignore[arg-type]

    challenge, _ = await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )

    msg_body = wa_sender.sent_messages[0]["body"]
    code = next(
        w.strip(".:,")
        for w in msg_body.split()
        if w.strip(".:,").isdigit() and len(w.strip(".:,")) == 6
    )

    # 4 wrong attempts
    for _ in range(4):
        res = await verify_phone_code(
            sqlite_session,
            booking_id=booking.id,
            tenant_id=tenant.id,
            code="000000" if code != "000000" else "111111",
            app_secret=_APP_SECRET,
            now=now,
        )
        assert res is VerificationStatus.INVALID
        await sqlite_session.refresh(challenge)
        assert challenge.code_hmac is not None

    assert challenge.attempts == 4

    # 5th wrong attempt burns the challenge
    res5 = await verify_phone_code(
        sqlite_session,
        booking_id=booking.id,
        tenant_id=tenant.id,
        code="999999" if code != "999999" else "888888",
        app_secret=_APP_SECRET,
        now=now,
    )
    assert res5 is VerificationStatus.BURNED
    await sqlite_session.refresh(challenge)
    assert challenge.code_hmac is None  # Burned!

    # 6th attempt even with CORRECT code fails
    res6 = await verify_phone_code(
        sqlite_session,
        booking_id=booking.id,
        tenant_id=tenant.id,
        code=code,
        app_secret=_APP_SECRET,
        now=now,
    )
    assert res6 is VerificationStatus.INVALID
    assert booking.guest_phone_verified_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("garbage", ["", "   ", "12345", "1234567", "abcdef", "12 34 56"])
async def test_a_code_that_cannot_be_a_code_is_refused_WITHOUT_spending_an_attempt(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
    garbage: str,
) -> None:
    """==Un intento es una ADIVINANZA del secreto; un texto que no tiene su forma no lo es.==

    D-4 dice que un código son seis dígitos. Cualquier otra cosa (vacío, una palabra, un número de
    otra longitud, dígitos con espacios) se rechaza antes de calcular el HMAC y **sin consumir
    intento** — gastar cupo con eso sería gastarlo contra el invitado que se equivocó al pegar, no
    contra quien intenta adivinar.
    """
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    sender = DummySender(Channel.WHATSAPP)
    senders = TenantSenders(tenant_id=tenant.id, email=None, channels={Channel.WHATSAPP: sender})  # type: ignore[arg-type]
    challenge, _ = await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )

    assert (
        await verify_phone_code(
            sqlite_session,
            booking_id=booking.id,
            tenant_id=tenant.id,
            code=garbage,
            app_secret=_APP_SECRET,
            now=now,
        )
        is VerificationStatus.INVALID
    )

    await sqlite_session.refresh(challenge)
    assert challenge.attempts == 0, "un texto que no es un código gastó un intento"
    assert challenge.code_hmac is not None, "el código vivo se quemó por un texto que no era código"
    assert booking.guest_phone_verified_at is None


@pytest.mark.asyncio
async def test_a_challenge_does_NOT_verify_a_phone_the_booking_no_longer_has(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    """==El sello atestigua el número ACTUAL, no el que había cuando se emitió el desafío.==

    Si el teléfono de la reserva cambia después de emitir (una reprogramación con otro número, una
    edición del anfitrión), el código viejo no puede sellar posesión del número nuevo: nadie probó
    que pudiera leer un código en él. OTP-10 limpia el sello en cada escritura del teléfono; esto
    cierra el mismo agujero para el desafío vivo, que la escritura no alcanza.
    """
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    sender = DummySender(Channel.WHATSAPP)
    senders = TenantSenders(tenant_id=tenant.id, email=None, channels={Channel.WHATSAPP: sender})  # type: ignore[arg-type]
    challenge, _ = await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        suppression_key=_SUPPRESSION_KEY,
        now=now,
    )
    code = next(
        w.strip(".:,")
        for w in sender.sent_messages[0]["body"].split()
        if w.strip(".:,").isdigit() and len(w.strip(".:,")) == 6
    )

    booking.guest_phone = "+13055559999"  # el número cambió tras emitirse el desafío
    await sqlite_session.flush()

    outcome = await verify_phone_code(
        sqlite_session,
        booking_id=booking.id,
        tenant_id=tenant.id,
        code=code,  # el código CORRECTO del número viejo
        app_secret=_APP_SECRET,
        now=now,
    )

    assert outcome is VerificationStatus.INVALID
    assert booking.guest_phone_verified_at is None, "se selló posesión de un número que ya no es"
    await sqlite_session.refresh(challenge)
    assert challenge.consumed_at is None
    assert challenge.code_hmac is not None, "el desafío del número viejo se consumió"


@pytest.mark.asyncio
async def test_sweep_stale_challenges_deletes_only_what_is_older_than_the_window(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    """La retención de 24 h del diseño (D-7·bis) es una REGLA del barrido: borra lo viejo y deja la
    lápida reciente que los topes todavía cuentan. El barrido existe aunque hoy nadie lo llame desde
    el worker (cablearlo es el hilo abierto declarado en su docstring)."""
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    phone_hmac = compute_hmac(_APP_SECRET, "+13055550001")
    sqlite_session.add_all(
        [
            PhoneVerificationChallenge(
                tenant_id=tenant.id,
                booking_id=booking.id,
                phone_hmac=phone_hmac,
                code_hmac=None,
                attempts=0,
                expires_at=now - timedelta(days=2),
                created_at=now - timedelta(hours=25),
            ),
            PhoneVerificationChallenge(
                tenant_id=tenant.id,
                booking_id=booking.id,
                phone_hmac=phone_hmac,
                code_hmac=None,
                attempts=0,
                expires_at=now - timedelta(hours=10),
                created_at=now - timedelta(hours=1),
            ),
        ]
    )
    await sqlite_session.flush()

    deleted = await sweep_stale_challenges(sqlite_session, now=now)

    assert deleted == 1
    remaining = await sqlite_session.scalar(
        sa.select(sa.func.count()).select_from(PhoneVerificationChallenge)
    )
    assert remaining == 1


# --------------------------------------------------------------------------------------
# Rate limit & Tombstone tests (D-7, D-7·bis, A-12, A-13)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_rate_limits_and_tombstone_survival(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    t0 = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    senders = TenantSenders(
        tenant_id=tenant.id,
        email=None,
        channels={Channel.WHATSAPP: DummySender(Channel.WHATSAPP)},  # type: ignore[arg-type]
    )

    # 1st challenge: OK
    await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        now=t0,
    )

    # 2nd challenge at +30s: fails 60s minimum interval
    with pytest.raises(PhoneVerificationRateLimitError, match="60 seconds"):
        await issue_verification_challenge(
            sqlite_session,
            booking=booking,
            app_secret=_APP_SECRET,
            business_name="Acme Corp",
            senders=senders,
            source_ip="198.51.100.1",
            now=t0 + timedelta(seconds=30),
        )

    # 2nd challenge at +65s: OK
    t1 = t0 + timedelta(seconds=65)
    await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        now=t1,
    )

    # 3rd challenge at +130s: OK (reaches 3 / 24h cap)
    t2 = t1 + timedelta(seconds=65)
    await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        now=t2,
    )

    # 4th challenge at +195s: rejected due to 3 / 24h cap
    t3 = t2 + timedelta(seconds=65)
    with pytest.raises(PhoneVerificationRateLimitError, match="3/24h"):
        await issue_verification_challenge(
            sqlite_session,
            booking=booking,
            app_secret=_APP_SECRET,
            business_name="Acme Corp",
            senders=senders,
            source_ip="198.51.100.1",
            now=t3,
        )

    # A-12: Advance 15 minutes so the challenges expire (TTL 10 min)
    # The 4th challenge must STILL be rejected because tombstones survive 24h
    t_expired = t0 + timedelta(minutes=15)
    with pytest.raises(PhoneVerificationRateLimitError, match="3/24h"):
        await issue_verification_challenge(
            sqlite_session,
            booking=booking,
            app_secret=_APP_SECRET,
            business_name="Acme Corp",
            senders=senders,
            source_ip="198.51.100.1",
            now=t_expired,
        )


@pytest.mark.asyncio
async def test_ip_rate_limit_enforced_at_20_challenges(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    t0 = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    ip = "203.0.113.88"

    # Seed 20 challenges from the same IP with different phone numbers
    for i in range(MAX_PER_IP_24H):
        p_hmac = compute_hmac(_APP_SECRET, f"+1555000{i:04d}")
        ch = PhoneVerificationChallenge(
            tenant_id=tenant.id,
            booking_id=booking.id,
            phone_hmac=p_hmac,
            code_hmac=None,  # tombstone
            attempts=0,
            expires_at=t0 + OTP_TTL,
            source_ip=ip,
            created_at=t0 + timedelta(minutes=i),
        )
        sqlite_session.add(ch)
    await sqlite_session.flush()

    # The 21st attempt from that IP must be rejected by check_challenge_rate_limits
    with pytest.raises(IPRateLimitError, match="20/24h"):
        await check_challenge_rate_limits(
            sqlite_session,
            tenant_id=tenant.id,
            phone_hmac=compute_hmac(_APP_SECRET, "+19999999999"),
            source_ip=ip,
            now=t0 + timedelta(hours=1),
        )


# --------------------------------------------------------------------------------------
# Channel fallback test (D-8, D-8·bis)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whatsapp_permanent_error_falls_back_to_sms(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    # WhatsApp fails permanently (e.g. not on WhatsApp); SMS succeeds
    failing_wa = DummySender(Channel.WHATSAPP, fail_permanent=True)
    working_sms = DummySender(Channel.SMS, fail_permanent=False)
    senders = TenantSenders(
        tenant_id=tenant.id,
        email=None,
        channels={Channel.WHATSAPP: failing_wa, Channel.SMS: working_sms},  # type: ignore[arg-type]
    )

    challenge, channel_used = await issue_verification_challenge(
        sqlite_session,
        booking=booking,
        app_secret=_APP_SECRET,
        business_name="Acme Corp",
        senders=senders,
        source_ip="198.51.100.1",
        now=now,
    )

    assert channel_used == Channel.SMS
    assert len(working_sms.sent_messages) == 1
    assert challenge.code_hmac is not None


@pytest.mark.asyncio
async def test_a_TRANSIENT_whatsapp_error_does_NOT_fall_back_to_sms(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    """==El fallback de canal es para rechazos PERMANENTES (D-8·bis), no para cualquier fallo.==

    Un timeout o un 500 del proveedor es un problema de la entrega, no del número: cambiar de canal
    ahí duplicaría el mensaje cuando WhatsApp sí lo haya aceptado a medias, y el invitado recibiría
    dos códigos. El error transitorio se propaga para que el reintento sea el normal, y SMS no se
    toca.
    """
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    flaky_wa = DummySender(Channel.WHATSAPP, fail_transient=True)
    untouched_sms = DummySender(Channel.SMS)
    senders = TenantSenders(
        tenant_id=tenant.id,
        email=None,
        channels={Channel.WHATSAPP: flaky_wa, Channel.SMS: untouched_sms},  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="retryable"):
        await issue_verification_challenge(
            sqlite_session,
            booking=booking,
            app_secret=_APP_SECRET,
            business_name="Acme Corp",
            senders=senders,
            source_ip="198.51.100.1",
            suppression_key=_SUPPRESSION_KEY,
            now=now,
        )

    assert untouched_sms.sent_messages == [], "un fallo transitorio cambió de canal: mensaje doble"


# --------------------------------------------------------------------------------------
# Instance Opt-Out suppression test (D-12)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suppressed_phone_refuses_otp_send(
    sqlite_session: AsyncSession,
    seeded_context: tuple[Tenant, EventType, Booking],
) -> None:
    tenant, _, booking = seeded_context
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    # Opt-out the booking's phone
    await suppress_phone(
        sqlite_session,
        booking.guest_phone,
        reason="Requested stop via SMS",
        suppression_key=_SUPPRESSION_KEY,
    )

    senders = TenantSenders(
        tenant_id=tenant.id,
        email=None,
        channels={Channel.WHATSAPP: DummySender(Channel.WHATSAPP)},  # type: ignore[arg-type]
    )

    with pytest.raises(PhoneVerificationError, match="opted out"):
        await issue_verification_challenge(
            sqlite_session,
            booking=booking,
            app_secret=_APP_SECRET,
            business_name="Acme Corp",
            senders=senders,
            source_ip="198.51.100.1",
            suppression_key=_SUPPRESSION_KEY,
            now=now,
        )


# --------------------------------------------------------------------------------------
# Outbox phone gate test (OTP-4)
# --------------------------------------------------------------------------------------


def test_outbox_skips_when_phone_unverified() -> None:
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    unverified_booking = Booking(
        guest_phone="+13055552222",
        guest_phone_consent_at=now,
        guest_phone_verified_at=None,
    )

    with pytest.raises(OutboxSkipped) as exc_info:
        _require_phone_consent(unverified_booking, Channel.WHATSAPP)

    assert exc_info.value.args[0].startswith("phone-unverified:")


def test_outbox_allows_when_phone_verified() -> None:
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    verified_booking = Booking(
        guest_phone="+13055552222",
        guest_phone_consent_at=now,
        guest_phone_verified_at=now,
    )
    # Should not raise
    _require_phone_consent(verified_booking, Channel.WHATSAPP)


# --------------------------------------------------------------------------------------
# Reschedule inheritance test (OTP-9)
# --------------------------------------------------------------------------------------


def test_reschedule_inherits_verified_stamp_when_phone_matches() -> None:
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    predecessor = Booking(guest_phone="+13055553333", guest_phone_verified_at=now)
    successor = Booking(guest_phone="+13055553333", guest_phone_verified_at=None)

    inherit_phone_verification_on_reschedule(predecessor, successor)
    assert successor.guest_phone_verified_at == now


def test_reschedule_does_not_inherit_when_phone_differs() -> None:
    now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    predecessor = Booking(guest_phone="+13055553333", guest_phone_verified_at=now)
    successor = Booking(guest_phone="+13055554444", guest_phone_verified_at=None)

    inherit_phone_verification_on_reschedule(predecessor, successor)
    assert successor.guest_phone_verified_at is None
