"""Idempotency ledger for transactional emails and the 24 h reminder (RF-08/RF-10).

One row per (booking, kind) makes each notification fire at most once even if a job retries."""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, TenantScoped, UUIDPrimaryKey


class SentNotification(UUIDPrimaryKey, TenantScoped, Base):
    """A record that a given notification kind was sent for a booking (idempotency key)."""

    __tablename__ = "sent_notifications"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    sent_at: Mapped[_dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (sa.UniqueConstraint("tenant_id", "booking_id", "kind"),)


__all__ = ["SentNotification"]
