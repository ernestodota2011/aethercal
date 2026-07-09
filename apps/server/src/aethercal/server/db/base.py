"""Declarative base, naming convention, and the shared column mixins.

Every domain table gets a UUID primary key and created/updated timestamps; every table except the
tenant root gets a non-null ``tenant_id`` foreign key (the multi-tenancy belt decided in the plan:
a shared schema scoped by ``tenant_id`` from day one, with Postgres RLS layered on later in F4).
The deterministic naming convention keeps Alembic autogenerate free of cross-machine drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint/index names (Alembic naming convention) → drift-free autogenerate.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_NOW = sa.text("CURRENT_TIMESTAMP")  # portable across PostgreSQL and SQLite DDL.


class Base(DeclarativeBase):
    """Declarative base carrying the shared metadata + naming convention."""

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """Mixin: a client-generated UUID primary key (works identically on PG and SQLite)."""

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class TenantScoped:
    """Mixin: the non-null ``tenant_id`` foreign key that scopes a row to one tenant."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class CreatedAt:
    """Mixin: a server-set creation timestamp for append-only rows."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=_NOW, nullable=False
    )


class Timestamps(CreatedAt):
    """Mixin: creation + update timestamps for mutable rows."""

    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=_NOW,
        onupdate=sa.func.now(),
        nullable=False,
    )
