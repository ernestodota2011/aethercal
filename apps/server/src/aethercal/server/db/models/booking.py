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
#
# ``create_constraint=True`` is NOT decoration. SQLAlchemy 1.4+ defaults it to **False**, so this
# column has been a bare ``VARCHAR(16)`` with no validation at all since 0001 — the database would
# have accepted ``status = 'banana'``. The comment above claimed "VARCHAR + CHECK" and the CHECK was
# never emitted (verified against both the live PostgreSQL 16 schema and the SQLite one). Migration
# 0005 creates the constraint that should always have been there, over the full four-status
# vocabulary. Found while adding ``no_show``; fixed at the root rather than worked around.
_BOOKING_STATUS = sa.Enum(
    BookingStatus,
    name="booking_status",
    native_enum=False,
    length=16,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


def _new_ical_uid() -> str:
    """A fresh, stable RFC 5545 UID for a new booking (inherited by its reschedule successors)."""
    return f"{uuid.uuid4()}@aethercal"


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
    # NULL = the guest gave no phone. Validated as E.164 at the SCHEMA layer (``BookingCreate``),
    # not here: the column is storage, and a WhatsApp/SMS step simply skips a booking without one.
    guest_phone: Mapped[str | None] = mapped_column(sa.String(20))
    # When the guest agreed to be messaged on that number. NULL = they did not. ==Persist the
    # consent, or the consent did not happen.== A checkbox whose answer is thrown away is not
    # consent and cannot be evidenced later; a WhatsApp/SMS step must be able to ask the database
    # "may I message this person?" and get a defensible answer.
    guest_phone_consent_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    guest_timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    guest_notes: Mapped[str | None] = mapped_column(sa.Text)
    answers: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    # The RFC 5545 UID for this booking's calendar event (F1-08). A reschedule successor INHERITS
    # its predecessor's ``ical_uid`` so the confirmation, every reschedule, and the cancellation all
    # address the SAME event — which is what makes the strictly-increasing ``sequence`` above let a
    # client honor each update (a per-booking UID would make the sequence bumps meaningless).
    ical_uid: Mapped[str] = mapped_column(
        sa.String(255), server_default=sa.text("''"), default=_new_ical_uid, nullable=False
    )
    external_event_id: Mapped[str | None] = mapped_column(sa.String(255))
    meeting_url: Mapped[str | None] = mapped_column(sa.String(1024))
    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # When the host marked the guest a no-show (RF-25). Only ever set from ``confirmed``, and only
    # after the appointment has ENDED. The booking keeps occupying its slot (see BookingStatus).
    no_show_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # The persisted iCal SEQUENCE for this booking's UID (RFC 5545, F1-08). Starts at 0 (the
    # confirmation), and every mutation that emits an updated ``.ics`` bumps it — a cancellation
    # bumps it, a reschedule carries the predecessor + 1 — so successive updates strictly increase
    # and calendar clients never ignore a stale update (the by-kind constant could not).
    sequence: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), default=0, nullable=False
    )

    __table_args__ = (
        sa.Index(
            "uq_bookings_active_slot",
            "tenant_id",
            "event_type_id",
            "start_at",
            unique=True,
            # The partial predicate (cancelled bookings free their slot, RF-04) is declared for BOTH
            # PostgreSQL (production) and SQLite (the offline test backend) so ``create_all`` builds
            # a genuinely partial index everywhere. Without ``sqlite_where`` SQLite emits a FULL
            # unique index and a cancelled row keeps occupying its slot, so the offline suite could
            # not prove the freed-slot semantics. The initial migration stays PostgreSQL-only.
            #
            # The predicate is UNCHANGED by the no-show work (RF-25), and that is deliberate rather
            # than an oversight: the appointment already happened, so freeing its slot would corrupt
            # history and let a booking be written retroactively over it. "Everything except
            # cancelled occupies" is the safe default, and ``no_show`` inherits it for free.
            postgresql_where=sa.text("status <> 'cancelled'"),
            sqlite_where=sa.text("status <> 'cancelled'"),
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
