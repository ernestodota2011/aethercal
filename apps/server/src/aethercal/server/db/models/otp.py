"""Phone verification (OTP C-02b) models and instance suppression table.

PhoneVerificationChallenge: short-lived OTP challenges (TTL 10 min, max 5 attempts).
When consumed, expired or cancelled, code_hmac is nulled out so the row survives as a
24-hour rate-limit counting tombstone (D-7·bis).

PhoneSuppression: instance-level opt-out table keyed by phone_hmac under a dedicated
non-rotatable key (AETHERCAL_SUPPRESSION_KEY, D-12). Survives guest purge.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class PhoneVerificationChallenge(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A phone verification challenge holding a 6-digit OTP code HMAC and rate-limit counters."""

    __tablename__ = "phone_verification_challenges"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # HMAC-SHA256 of the E.164 phone number using app secret
    # (for rate-limit counting across bookings)
    phone_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    # HMAC-SHA256 of the 6-digit code. Nulled on consume/expire/cancel
    # to become a tombstone (D-7·bis)
    code_hmac: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), default=0, nullable=False
    )
    expires_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[_dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    source_ip: Mapped[str | None] = mapped_column(sa.String(45), nullable=True, index=True)

    __table_args__ = (
        sa.Index(
            "ix_phone_verification_challenges_phone_window",
            "tenant_id",
            "phone_hmac",
            "created_at",
        ),
        sa.Index(
            "ix_phone_verification_challenges_ip_window",
            "source_ip",
            "created_at",
        ),
    )


class PhoneSuppression(UUIDPrimaryKey, CreatedAt, Base):
    """Instance-level suppression list (D-12 opt-out).

    Guards phone numbers that requested opt-out under AETHERCAL_SUPPRESSION_KEY HMAC.
    Scoped per instance (no tenant_id) and deliberately exempt from guest purge so that
    erasure does not reactivate messaging to an opt-out recipient.
    """

    __tablename__ = "phone_suppressions"

    phone_hmac: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)


__all__ = ["PhoneSuppression", "PhoneVerificationChallenge"]
