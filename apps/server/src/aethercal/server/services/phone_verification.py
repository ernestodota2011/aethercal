"""Phone possession verification service (C-02b, RF-24).

Implements the approved C-02b design package, whose decisions live in this module and in
``docs/phone-channels.md``:
- 6-digit CSPRNG codes, TTL 10 min, max 5 attempts, single-use (D-4).
- HMAC-SHA256 storage under AETHERCAL_APP_SECRET; timing-attack safe compare_digest (D-5, A-4).
- Anti-enumeration: identical byte-for-byte failure response
  for missing, invalid or expired (D-6, A-3).
- Rate limits on tombstones: 3/phone/24h, 1/phone/60s, 20/ip/24h (D-7, D-7·bis, A-12, A-13).
- Single live challenge per booking: resending turns previous into tombstone (D-7·bis).
- Channel dispatch with WhatsApp preference and automatic fallback to SMS
  on PermanentSendError (D-8, D-8·bis).
- PermanentSendError does not consume quota slot (D-7·bis).
- Instance-level phone suppression list with dedicated non-rotatable key
  AETHERCAL_SUPPRESSION_KEY (D-12).
- Atomic consume with RETURNING and guest_phone_verified_at seal on booking (A-7).
- Reschedule inheritance (OTP-9) and phone write invalidation (OTP-10).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking
from aethercal.server.db.models.otp import PhoneSuppression, PhoneVerificationChallenge
from aethercal.server.integrations.messaging.guard import (
    PermanentSendError,
)
from aethercal.server.services.tenant_senders import TenantSenders

_logger = logging.getLogger(__name__)

OTP_TTL: Final = timedelta(minutes=10)
MAX_ATTEMPTS: Final = 5
TOMBSTONE_WINDOW: Final = timedelta(hours=24)
PHONE_MIN_INTERVAL: Final = timedelta(seconds=60)
MAX_PER_PHONE_24H: Final = 3
MAX_PER_IP_24H: Final = 20

_NON_DIGITS = re.compile(r"\D")

# Generic byte-for-byte error response detail for anti-enumeration (D-6, A-3)
GENERIC_OTP_FAILURE_DETAIL: Final = "Invalid, expired, or non-existent verification code."

_OTP_CODE_RE = re.compile(r"[0-9]{6}")
"""The ONLY shape a code can have (D-4: six digits). Anything else is not a guess at the secret."""


class VerificationStatus(StrEnum):
    SUCCESS = "success"
    INVALID = "invalid"
    BURNED = "burned"
    """The code was correct in shape but the 5-attempt budget is spent: the challenge is dead.

    It is its own state because the guest-actionable advice differs ("request a new code" instead of
    "check the digits"), and because the UI has carried a distinct message for it since the feature
    landed — a message that nothing could reach while verification answered a bare boolean."""
    RATE_LIMITED = "rate_limited"
    IP_RATE_LIMITED = "ip_rate_limited"
    SUPPRESSED = "suppressed"


class PhoneVerificationError(Exception):
    """Base error for phone verification operations."""


class PhoneVerificationRateLimitError(PhoneVerificationError):
    """Raised when phone-level challenge rate limit is exceeded."""


class PhoneVerificationCooldownError(PhoneVerificationRateLimitError):
    """The 60-second minimum interval: a DIFFERENT actionable fact from the daily ceiling.

    The guest who must wait a minute is not the guest who is out of codes for the day, and the two
    cases carry different copy. They used to be one exception whose message the UI had to
    substring-match; the machine code now says which one it is."""


class IPRateLimitError(PhoneVerificationError):
    """Raised when source IP challenge rate limit is exceeded."""


class SuppressionKeyNotConfigured(PhoneVerificationError):
    """The instance-level suppression key is missing from the configuration (D-12).

    Fail-closed on purpose: without the key the opt-out list cannot be consulted, and a message
    must not be sent when the list that says "do not message this number" is unreadable.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_e164(phone: str) -> str:
    """Normalize a phone number to ``+<digits>`` (best-effort; validation lives at the edge).

    This is a NORMALIZER, not a validator: it strips formatting and prefixes a ``+`` to whatever
    digits it finds, so an inbound webhook's ``13055551234@s.whatsapp.net`` and a form's
    ``+1 (305) 413-1728`` compare equal. Length and country-prefix rules belong to the booking
    schema (``E164Phone``), which refuses an unaddressable number with a 422 on the way IN — a
    webhook sender, by contrast, is whatever WhatsApp says it is, and refusing it here would only
    turn a legacy-format number into silence.
    """
    cleaned = phone.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith("+"):
        digits = _NON_DIGITS.sub("", cleaned)
        return f"+{digits}" if digits else ""
    digits = _NON_DIGITS.sub("", cleaned[1:])
    return f"+{digits}" if digits else ""


def compute_hmac(key: str, data: str) -> str:
    """Compute HMAC-SHA256 hex digest using a given key."""
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def get_suppression_key(explicit_key: str | None = None) -> str:
    """Resolve the dedicated instance suppression key (D-12, A-1).

    ==There is NO derived fallback, and that is a fix.== An earlier revision derived the key from
    ``AETHERCAL_APP_SECRET`` — and, when that too was absent, from a constant default. That made
    the "dedicated, non-rotatable" key neither: it was a function of the master secret (so rotating
    that secret silently re-keyed the suppression list and split it in two), and on a misconfigured
    instance it was a PUBLISHED constant. Anyone holding it can compute the HMAC of a guessed phone
    number, so the list stops being opaque. Missing configuration is now a loud refusal: the caller
    cannot consult the opt-out list, so it must not message anybody.
    """
    key = (explicit_key or os.environ.get("AETHERCAL_SUPPRESSION_KEY") or "").strip()
    if not key:
        raise SuppressionKeyNotConfigured(
            "AETHERCAL_SUPPRESSION_KEY is required to consult the instance opt-out list (D-12). "
            "Refusing to message without it. Generate one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return key


async def is_phone_suppressed(
    session: AsyncSession, phone: str, suppression_key: str | None = None
) -> bool:
    """Check if the given phone number is on the instance suppression list (fail-closed, D-12)."""
    norm = normalize_e164(phone)
    if not norm:
        return False
    key = get_suppression_key(suppression_key)
    p_hmac = compute_hmac(key, norm)
    row = (
        await session.scalars(select(PhoneSuppression).where(PhoneSuppression.phone_hmac == p_hmac))
    ).first()
    return row is not None


async def suppress_phone(
    session: AsyncSession,
    phone: str,
    *,
    reason: str | None = None,
    suppression_key: str | None = None,
) -> PhoneSuppression:
    """Add a phone number to the instance-wide suppression list."""
    norm = normalize_e164(phone)
    if not norm:
        raise ValueError("Invalid phone number")
    key = get_suppression_key(suppression_key)
    p_hmac = compute_hmac(key, norm)
    existing = (
        await session.scalars(select(PhoneSuppression).where(PhoneSuppression.phone_hmac == p_hmac))
    ).first()
    if existing is not None:
        return existing
    entry = PhoneSuppression(phone_hmac=p_hmac, reason=reason)
    session.add(entry)
    await session.flush()
    return entry


_ISSUANCE_LOCK_NAMESPACE = "aethercal-phone-issuance"


def _issuance_lock_key(scope: str) -> int:
    """A stable signed 64-bit advisory-lock key for one issuance scope.

    Same shape as the booking path's per-host key (``services.bookings._host_lock_key``):
    ``pg_advisory_xact_lock`` takes a signed ``bigint`` and an 8-byte BLAKE2b digest fits exactly.
    """
    digest = hashlib.blake2b(scope.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _serialize_issuance(
    session: AsyncSession, *, phone_hmac: str, source_ip: str | None
) -> None:
    """Serialize concurrent OTP issuance for one phone, and one source network, on PostgreSQL.

    ==The rate-limit queries are check-then-insert, and that is a race.== Two concurrent resends
    for the same phone can both count the same committed rows, both pass, and both insert: the
    per-phone 3/24h and per-IP 20/24h ceilings become advisory. The ceiling is not cosmetic — it
    caps how many messages an attacker can make the instance send at their victim's expense
    (SMS/WhatsApp pumping), so it has to hold under concurrency, not just in a serial test.

    The transaction-scoped advisory lock makes the check and the insert one critical section: a
    second transaction blocks on the same key, and once the first commits it re-counts and sees
    the new row. The locks are taken in a FIXED order (phone, then IP): two transactions that took
    them in opposite orders would deadlock. On SQLite (the offline backend) this is a no-op —
    SQLite serializes writers anyway.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    scopes = [f"{_ISSUANCE_LOCK_NAMESPACE}:phone:{phone_hmac}"]
    if source_ip:
        scopes.append(f"{_ISSUANCE_LOCK_NAMESPACE}:ip:{source_ip}")
    for scope in scopes:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _issuance_lock_key(scope)}
        )


async def check_challenge_rate_limits(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone_hmac: str,
    source_ip: str | None,
    now: datetime,
) -> None:
    """Assert rate limit constraints on challenge creation (D-7, D-7·bis, A-12, A-13).

    - At most 3 challenges per phone per 24 hours.
    - At most 1 challenge per phone per 60 seconds.
    - At most 20 challenges per source IP per 24 hours.
    Counts on tombstones (rows survive 24h even if code_hmac is nulled).

    The counters are taken under the issuance advisory lock (see :func:`_serialize_issuance`), so
    the ceilings hold against concurrent resends and not only against a serial attacker.
    """
    await _serialize_issuance(session, phone_hmac=phone_hmac, source_ip=source_ip)

    cutoff_24h = now - TOMBSTONE_WINDOW
    cutoff_60s = now - PHONE_MIN_INTERVAL

    # 1. Check phone 60s minimum interval
    recent_phone_count = await session.scalar(
        select(func.count(PhoneVerificationChallenge.id)).where(
            PhoneVerificationChallenge.tenant_id == tenant_id,
            PhoneVerificationChallenge.phone_hmac == phone_hmac,
            PhoneVerificationChallenge.created_at >= cutoff_60s,
        )
    )
    if (recent_phone_count or 0) > 0:
        raise PhoneVerificationCooldownError(
            "Please wait at least 60 seconds before requesting another code."
        )

    # 2. Check phone 24h cap (3 per phone per 24h)
    daily_phone_count = await session.scalar(
        select(func.count(PhoneVerificationChallenge.id)).where(
            PhoneVerificationChallenge.tenant_id == tenant_id,
            PhoneVerificationChallenge.phone_hmac == phone_hmac,
            PhoneVerificationChallenge.created_at >= cutoff_24h,
        )
    )
    if (daily_phone_count or 0) >= MAX_PER_PHONE_24H:
        raise PhoneVerificationRateLimitError(
            "Maximum daily verification attempts reached for this phone number (limit: 3/24h)."
        )

    # 3. Check IP 24h cap (20 per IP per 24h)
    if source_ip:
        ip_count = await session.scalar(
            select(func.count(PhoneVerificationChallenge.id)).where(
                PhoneVerificationChallenge.source_ip == source_ip,
                PhoneVerificationChallenge.created_at >= cutoff_24h,
            )
        )
        if (ip_count or 0) >= MAX_PER_IP_24H:
            raise IPRateLimitError(
                "Maximum verification requests reached for this network address (limit: 20/24h). "
                "Please try again tomorrow or contact support."
            )


def format_otp_body(
    code: str,
    business_name: str,
    locale: str = "es",
) -> str:
    """Build the honest, unlinked bootstrap OTP text message with opt-out clause (D-3, A-11)."""
    clean_name = business_name.strip()[:50] or "AetherCal"
    # Defang links and remove newlines from business_name
    clean_name = re.sub(r"https?://\S+", "", clean_name).replace("\n", " ").strip()

    if locale.startswith("en"):
        return (
            f"Your verification code for {clean_name} is: {code}. "
            f"Valid for 10 minutes. Do not share this code. "
            f"To opt out of future messages, reply STOP."
        )
    return (
        f"Tu código de verificación para {clean_name} es: {code}. "
        f"Válido por 10 minutos. No compartas este código. "
        f"Para no recibir más mensajes, responde STOP."
    )


async def _dispatch_challenge_message(
    senders: TenantSenders | None,
    *,
    to: str,
    body: str,
) -> Channel:
    """Dispatch the verification code with WhatsApp preference and SMS fallback (D-8, D-8·bis).

    If WhatsApp fails with PermanentSendError (e.g. number not on WhatsApp),
    retries once via SMS if configured.
    """
    if senders is None:
        raise PermanentSendError("No active messaging senders configured.")

    whatsapp_sender = senders.channels.get(Channel.WHATSAPP)
    sms_sender = senders.channels.get(Channel.SMS)

    if whatsapp_sender is not None:
        try:
            await whatsapp_sender.send(to=to, subject=None, body=body)
            return Channel.WHATSAPP
        except PermanentSendError as exc:
            _logger.info(
                "WhatsApp dispatch rejected permanently (%s); attempting fallback to SMS", exc
            )
            if sms_sender is not None:
                await sms_sender.send(to=to, subject=None, body=body)
                return Channel.SMS
            raise
    elif sms_sender is not None:
        await sms_sender.send(to=to, subject=None, body=body)
        return Channel.SMS
    else:
        raise PermanentSendError(
            "Neither WhatsApp nor SMS channel is configured for this business."
        )


async def issue_verification_challenge(  # noqa: PLR0913
    session: AsyncSession,
    *,
    booking: Booking,
    app_secret: str,
    business_name: str,
    senders: TenantSenders | None,
    source_ip: str | None,
    suppression_key: str | None = None,
    now: datetime | None = None,
    locale: str = "es",
) -> tuple[PhoneVerificationChallenge, Channel]:
    """Issue, persist and transmit a phone verification challenge (OTP).

    - Checks opt-out suppression list (fail-closed, D-12).
    - Checks 24h/60s phone and 24h IP caps (D-7, A-12, A-13).
    - Turns any previous active challenge for this booking into a tombstone (D-7·bis).
    - Generates 6-digit CSPRNG code and persists challenge with code_hmac.
    - Dispatches message with WhatsApp preference and SMS fallback (D-8, D-8·bis).
    - If sending fails permanently before delivery, annuls secret so slot is not consumed.

    .. rubric:: A TRANSIENT failure is deliberately different

    A ``PermanentSendError`` means nothing was delivered and nothing ever will be (the number
    has no WhatsApp account, the provider refuses the recipient): the secret is annulled and the
    slot is not spent (D-7·bis). A TRANSIENT failure (timeout, 500, connection reset) is
    ambiguous — the provider may already have accepted the message — so it propagates with the
    challenge LIVE and its slot spent. Annulling on an ambiguous outcome would free a rate-limit
    slot that a real message may have consumed; retrying it here would risk a second code. The
    guest's own RESEND (after the 60-second cooldown) is the retry, and the caller reports the
    failed dispatch to the page.
    """
    if not booking.guest_phone:
        raise ValueError("Booking has no guest phone number to verify.")

    current_time = now or _now()
    norm_phone = normalize_e164(booking.guest_phone)
    if not norm_phone:
        raise ValueError("Invalid guest phone number format.")

    # 1. Check suppression list (D-12)
    if await is_phone_suppressed(session, norm_phone, suppression_key=suppression_key):
        _logger.warning("Suppression match: phone %r opted out; refusing OTP send", norm_phone)
        raise PhoneVerificationError("Phone number has opted out of notifications.")

    # 2. Check rate limits (D-7)
    phone_hmac = compute_hmac(app_secret, norm_phone)
    await check_challenge_rate_limits(
        session,
        tenant_id=booking.tenant_id,
        phone_hmac=phone_hmac,
        source_ip=source_ip,
        now=current_time,
    )

    # 3. Retire previous live challenge to tombstone (D-7·bis: exactly one live challenge)
    await session.execute(
        update(PhoneVerificationChallenge)
        .where(
            PhoneVerificationChallenge.booking_id == booking.id,
            PhoneVerificationChallenge.code_hmac.is_not(None),
        )
        .values(code_hmac=None)
    )

    # 4. Generate code and hash
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hmac = compute_hmac(app_secret, code)

    challenge = PhoneVerificationChallenge(
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        phone_hmac=phone_hmac,
        code_hmac=code_hmac,
        attempts=0,
        created_at=current_time,
        expires_at=current_time + OTP_TTL,
        source_ip=source_ip,
    )
    session.add(challenge)
    await session.flush()

    # 5. Dispatch message with channel fallback (D-8, D-8·bis)
    body = format_otp_body(code, business_name, locale=locale)
    try:
        channel_used = await _dispatch_challenge_message(senders, to=norm_phone, body=body)
    except PermanentSendError:
        # Permanent error: nothing was delivered; annul code_hmac to preserve quota slot (D-7·bis)
        challenge.code_hmac = None
        await session.flush()
        raise

    return challenge, channel_used


async def verify_phone_code(  # noqa: PLR0913, PLR0911 - one early exit per verification state
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    tenant_id: uuid.UUID,
    code: str,
    app_secret: str,
    now: datetime | None = None,
) -> VerificationStatus:
    """Verify an entered OTP code against the live challenge for booking_id (A-7).

    - Atomic compare and consume with UPDATE ... WHERE RETURNING.
    - Rate limits invalid attempts (max 5); burns the code on 5th attempt.
    - Timing-safe verification using compare_digest.
    - If valid, seals booking.guest_phone_verified_at = now and destroys secret (code_hmac=None).
    - Returns a :class:`VerificationStatus`: ``SUCCESS``, ``INVALID`` or ``BURNED``. It used to
      return a bare bool, which collapsed "that code is wrong" and "that code is dead, ask for a
      new one" — two different things to tell a guest — into the same answer.

    ==The attempt counter is incremented by ONE atomic statement.== It used to be a
    read-modify-write (``challenge.attempts += 1``): under concurrent guesses every attempt read
    the same value and wrote the same value back, so the 5-attempt budget was spent N times for
    the price of one — a brute-force limiter that only limited a serial attacker. The
    ``WHERE attempts < MAX`` predicate now makes each guess consume a distinct slot.
    """
    current_time = now or _now()
    clean_code = code.strip()
    if not _OTP_CODE_RE.fullmatch(clean_code):
        # A code that cannot BE a code is refused without spending an attempt: it is not a guess at
        # the secret, it is a caller that never sent one (an empty string, a stray word, a pasted
        # paragraph). Hashing it would only burn budget against the wrong thing.
        return VerificationStatus.INVALID

    booking = (
        await session.scalars(
            select(Booking).where(Booking.id == booking_id, Booking.tenant_id == tenant_id)
        )
    ).one_or_none()
    if booking is None or not booking.guest_phone:
        return VerificationStatus.INVALID

    # ==A challenge verifies the phone the booking has NOW.== The challenge carries the HMAC of the
    # number it was issued for; if the booking's phone changed after issuance (a reschedule with a
    # different number, a host edit), the old code would otherwise seal a possession stamp for a
    # number whose code nobody proved they could read. OTP-10 clears the stamp on a phone write;
    # this closes the same hole for the live challenge, which the write path cannot reach.
    current_phone_hmac = compute_hmac(app_secret, normalize_e164(booking.guest_phone))

    # Look up the live challenge
    challenge = (
        await session.scalars(
            select(PhoneVerificationChallenge)
            .where(
                PhoneVerificationChallenge.booking_id == booking_id,
                PhoneVerificationChallenge.tenant_id == tenant_id,
                PhoneVerificationChallenge.phone_hmac == current_phone_hmac,
                PhoneVerificationChallenge.code_hmac.is_not(None),
                PhoneVerificationChallenge.consumed_at.is_(None),
                PhoneVerificationChallenge.expires_at > current_time,
            )
            .order_by(PhoneVerificationChallenge.created_at.desc())
        )
    ).first()

    if challenge is None:
        return VerificationStatus.INVALID

    expected_hmac = compute_hmac(app_secret, clean_code)
    matches = hmac.compare_digest(challenge.code_hmac or "", expected_hmac)

    if not matches:
        # ``attempts + 1`` is a COLUMN EXPRESSION: it compiles to SQL (``SET attempts = attempts +
        # 1``), never a Python read-modify-write. The CASE below decides the burn inside the SAME
        # statement, so there is no window where the counter reads 5 and the secret is still live.
        attempts_next = PhoneVerificationChallenge.attempts + 1
        increment = (
            update(PhoneVerificationChallenge)
            .where(
                PhoneVerificationChallenge.id == challenge.id,
                PhoneVerificationChallenge.tenant_id == tenant_id,
                PhoneVerificationChallenge.phone_hmac == current_phone_hmac,
                PhoneVerificationChallenge.consumed_at.is_(None),
                PhoneVerificationChallenge.code_hmac.is_not(None),
                PhoneVerificationChallenge.expires_at > current_time,
                PhoneVerificationChallenge.attempts < MAX_ATTEMPTS,
            )
            .values(
                attempts=attempts_next,
                # The burn rides IN the same statement as the attempt that exhausts the budget:
                # two statements would leave a window where the counter reads 5 and the secret is
                # still live.
                code_hmac=case(
                    (attempts_next >= MAX_ATTEMPTS, None),
                    else_=PhoneVerificationChallenge.code_hmac,
                ),
            )
            .returning(PhoneVerificationChallenge.attempts)
            # Bulk statement: the in-session copy is stale afterwards and nothing reads it (the
            # caller returns immediately), while the evaluation strategy would trip over SQLite's
            # naive datetimes against the aware bound parameter.
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(increment)
        attempts = result.scalar_one_or_none()
        if attempts is None or attempts >= MAX_ATTEMPTS:
            # The first case is a lost race (the budget was already spent, or the challenge died,
            # under a concurrent guess); the second is this guess spending the last slot. Either
            # way the code the caller holds is dead.
            return VerificationStatus.BURNED
        return VerificationStatus.INVALID

    # Atomic consume: RETURNING id ensures exactly one winner in case of race (A-7)
    stmt = (
        update(PhoneVerificationChallenge)
        .where(
            PhoneVerificationChallenge.id == challenge.id,
            PhoneVerificationChallenge.tenant_id == tenant_id,
            PhoneVerificationChallenge.phone_hmac == current_phone_hmac,
            PhoneVerificationChallenge.consumed_at.is_(None),
            PhoneVerificationChallenge.code_hmac.is_not(None),
            PhoneVerificationChallenge.expires_at > current_time,
        )
        .values(
            consumed_at=current_time,
            code_hmac=None,  # Destroy secret upon consumption (D-7·bis)
        )
        .returning(PhoneVerificationChallenge.id)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        return VerificationStatus.INVALID

    # Seal the booking (loaded above, under this tenant, for the phone HMAC check)
    booking.guest_phone_verified_at = current_time
    await session.flush()

    return VerificationStatus.SUCCESS


def inherit_phone_verification_on_reschedule(predecessor: Booking, successor: Booking) -> None:
    """Reschedule inherits the phone verification stamp if the number has not changed (OTP-9)."""
    if (
        predecessor.guest_phone
        and successor.guest_phone
        and normalize_e164(predecessor.guest_phone) == normalize_e164(successor.guest_phone)
    ):
        successor.guest_phone_verified_at = predecessor.guest_phone_verified_at


def clear_phone_verification_on_phone_change(booking: Booking, new_phone: str | None) -> None:
    """Any write that changes guest_phone invalidates the verified stamp (OTP-10)."""
    old_norm = normalize_e164(booking.guest_phone or "")
    new_norm = normalize_e164(new_phone or "")
    if old_norm != new_norm:
        booking.guest_phone_verified_at = None


async def sweep_stale_challenges(
    session: AsyncSession,
    *,
    older_than: timedelta = TOMBSTONE_WINDOW,
    now: datetime | None = None,
) -> int:
    """Prune challenges and tombstones older than 24 hours (D-7·bis, OTP.0).

    ==Wired to the worker's scheduler==
    (:func:`~aethercal.server.scheduler.run_phone_challenge_sweep_once`, job
    ``phone-challenge-sweep``, hourly): the 24-hour retention is an ENFORCED rule, not a
    documented one. It runs on the declared ``BypassReason.SWEEP_PHONE_CHALLENGES`` because
    "delete every expired challenge" is an instance-level statement — on the app role with no GUC
    it would match zero rows and retain everything, silently.

    The counting queries keep filtering by ``created_at >= cutoff`` regardless, so this job is
    housekeeping, never a correctness dependency: a missed tick delays deletion, it does not
    resurrect a tombstone into a rate-limit slot.
    """
    current_time = now or _now()
    cutoff = current_time - older_than
    stmt = delete(PhoneVerificationChallenge).where(PhoneVerificationChallenge.created_at < cutoff)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "GENERIC_OTP_FAILURE_DETAIL",
    "MAX_ATTEMPTS",
    "MAX_PER_IP_24H",
    "MAX_PER_PHONE_24H",
    "OTP_TTL",
    "PHONE_MIN_INTERVAL",
    "TOMBSTONE_WINDOW",
    "IPRateLimitError",
    "PhoneVerificationCooldownError",
    "PhoneVerificationError",
    "PhoneVerificationRateLimitError",
    "VerificationStatus",
    "check_challenge_rate_limits",
    "clear_phone_verification_on_phone_change",
    "compute_hmac",
    "format_otp_body",
    "get_suppression_key",
    "inherit_phone_verification_on_reschedule",
    "is_phone_suppressed",
    "issue_verification_challenge",
    "normalize_e164",
    "suppress_phone",
    "sweep_stale_challenges",
    "verify_phone_code",
]
