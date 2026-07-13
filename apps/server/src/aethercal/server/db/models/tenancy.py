"""Tenancy: the tenant root, its users (hosts/operators), and API keys."""

from __future__ import annotations

import datetime as _dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class Tenant(UUIDPrimaryKey, Timestamps, Base):
    """An isolated organization. Every other row is scoped to a tenant via ``tenant_id``."""

    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(sa.String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)


class User(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A host/operator within a tenant. The MVP admin is a single user (F1-11).

    .. rubric:: The address is unique per business, CASE-INSENSITIVELY (migration 0006)

    It used to be ``UNIQUE (tenant_id, email)`` — on the EXACT string. So ``Ana@example.com`` and
    ``ana@example.com`` were two hosts to the database and one human being to everyone else: a host
    selector offering two of somebody, an event type landing on whichever was clicked, and mail
    going to whichever row was read first.

    ``services/users.py`` refuses that pair, but it refuses it with a **check-then-act** — read,
    find nobody, write — and no amount of care closes the window between the read and the write. Two
    concurrent creates can each find nobody and each land. ==An invariant the database does not hold
    is not an invariant==, so it is held here: a functional unique index on
    ``(tenant_id, lower(email))``. The service's guard stays, doing the only thing it actually can —
    refusing legibly, before anything is written — and translating the index's own refusal when it
    loses the race.

    The exact-string ``UNIQUE`` is REPLACED, not kept alongside: ``lower(a) = lower(b)`` whenever
    ``a = b``, so it guarantees nothing this index does not, while costing a second B-tree on every
    write and offering a second constraint name for an ``IntegrityError`` to arrive under.

    The stored value keeps the case the operator typed (it is what a person reads); it is MATCHING
    that is case-insensitive — here, and in ``get_user_by_email``.

    .. warning::
       SQLAlchemy cannot REFLECT an expression index on SQLite (it warns and skips it), and
       ``batch_alter_table`` works by reflecting and rebuilding the table — so a future batch
       migration on ``users`` would silently drop this index on the way through. Recreate it
       explicitly if you ever batch-alter this table. (0006 sequences itself around exactly that: it
       drops the old constraint in batch mode FIRST, and only then creates this index.)
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(sa.String(255))

    __table_args__ = (
        sa.Index(
            "uq_users_tenant_id_email_lower",
            "tenant_id",
            sa.func.lower(sa.column("email")),
            unique=True,
        ),
    )


class ApiKey(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """An API credential (F1-17). The prefix identifies the row (and thus the tenant); the secret
    is verified against ``hashed_key``. The prefix is globally unique so lookup needs no tenant."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(16), unique=True, nullable=False)
    hashed_key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    last_used_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))


__all__ = ["ApiKey", "Tenant", "User"]
