"""Connected external calendars and the cached busy intervals read by the slots engine.

Slot calculation never calls Google in the request path (RNF-6); it reads ``busy_cache``, which a
background sync fills. Provider credentials are stored encrypted (Fernet, app key) as bytes."""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from aethercal.server.db.encrypted import FERNET_AT_REST


class ExternalConnection(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A host's connection to an external calendar provider (Google in F1-07)."""

    __tablename__ = "external_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    account_email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    # The host's OAuth credentials, held as a Fernet token (services/calendars.py). The `info`
    # marker enrols this column in the key rotation — see `db.encrypted` and `models/webhooks.py`.
    encrypted_credentials: Mapped[bytes] = mapped_column(
        sa.LargeBinary, nullable=False, info={FERNET_AT_REST: True}
    )
    revoked_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # The busy-cache coverage window this connection last synced, and when (RF-12/13). read_busy
    # judges freshness by whether this window CONTAINS the queried window (not by a per-row age), so
    # a cache filled for one window is never reused for another -- the fix for the F1-07 double-book
    # risk. All three are set together by refresh_busy_cache; NULL means "never synced".
    busy_synced_from: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    busy_synced_to: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    busy_synced_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "provider",
            "account_email",
            name="uq_external_connections_identity",
        ),
    )


class ExternalCalendarLink(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """One external calendar within a connection, with its incremental ``sync_token``."""

    __tablename__ = "external_calendar_links"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("external_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_calendar_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    sync_token: Mapped[str | None] = mapped_column(sa.Text)
    # Is this calendar READ when computing the host's busy time? Default true: a newly linked
    # calendar counts against availability, which is the safe direction — the failure mode is "we
    # offered too few slots", never "we double-booked the host".
    busy: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("true"), default=True, nullable=False
    )
    # Is this the calendar events are WRITTEN to? Default false, and at most one per connection —
    # see the partial unique index below.
    is_booking_target: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("false"), default=False, nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "connection_id",
            "external_calendar_id",
            name="uq_external_calendar_links_calendar",
        ),
        # At most ONE write target per connection, enforced by the DATABASE. Without it, "which
        # calendar do we write to?" gets answered by whatever row the code happened to read first —
        # an arbitrary choice that changes silently when rows are reordered. Partial (only the
        # targets are constrained), declared for BOTH backends so the offline suite proves the same
        # rule production enforces — the same technique as ``uq_bookings_active_slot``.
        sa.Index(
            "uq_external_calendar_links_target",
            "tenant_id",
            "connection_id",
            unique=True,
            postgresql_where=sa.text("is_booking_target"),
            sqlite_where=sa.text("is_booking_target"),
        ),
    )


class BusyCache(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """A cached busy interval from a connected calendar (RF-12/13), refreshed on a TTL."""

    __tablename__ = "busy_cache"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("external_connections.id", ondelete="CASCADE"), nullable=False
    )
    start_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    end_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[_dt.datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    __table_args__ = (sa.Index("ix_busy_cache_window", "tenant_id", "connection_id", "start_at"),)


__all__ = ["BusyCache", "ExternalCalendarLink", "ExternalConnection"]
