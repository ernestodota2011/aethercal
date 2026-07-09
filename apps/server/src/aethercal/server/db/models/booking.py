"""Bookings and the signed guest tokens used to self-serve cancel/reschedule."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey

# Stored as its string value (native_enum=False → VARCHAR + CHECK), reusing the core vocabulary so
# the DB and the domain model never disagree on the set of statuses.
_BOOKING_STATUS = sa.Enum(
    BookingStatus,
    name="booking_status",
    native_enum=False,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class Booking(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A reserved slot for one guest (RF-07). The single guest is denormalized onto the row; the
    partial unique index enforces one active booking per slot at the database level (RF-04)."""

    __tablename__ = "bookings"

    event_type_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    end_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        _BOOKING_STATUS, server_default=sa.text("'confirmed'"), nullable=False
    )
    guest_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    guest_email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    guest_timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    guest_notes: Mapped[str | None] = mapped_column(sa.Text)
    answers: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(sa.String(255))
    meeting_url: Mapped[str | None] = mapped_column(sa.String(1024))
    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index(
            "uq_bookings_active_slot",
            "tenant_id",
            "event_type_id",
            "start_at",
            unique=True,
            postgresql_where=sa.text("status <> 'cancelled'"),
        ),
    )


class GuestToken(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """A signed, expiring, single-use token letting a guest cancel/reschedule without an account
    (RF-09). Only the hash is stored; ``used_at`` records logical single use."""

    __tablename__ = "guest_tokens"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    expires_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))


__all__ = ["Booking", "GuestToken"]
