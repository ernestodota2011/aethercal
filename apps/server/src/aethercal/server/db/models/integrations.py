"""Connected external calendars and the cached busy intervals read by the slots engine.

Slot calculation never calls Google in the request path (RNF-6); it reads ``busy_cache``, which a
background sync fills. Provider credentials are stored encrypted (Fernet, app key) as bytes."""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class ExternalConnection(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A host's connection to an external calendar provider (Google in F1-07)."""

    __tablename__ = "external_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    account_email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    encrypted_credentials: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    revoked_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

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

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "connection_id",
            "external_calendar_id",
            name="uq_external_calendar_links_calendar",
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
