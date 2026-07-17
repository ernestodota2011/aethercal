"""Tenancy: the tenant root, its users (hosts/operators), their memberships, and API keys."""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.core.model.membership import MemberRole
from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey

# Stored as its string value (``native_enum=False`` → ``VARCHAR(16)`` + a ``CHECK``), reusing the
# core vocabulary so the database and the domain can never disagree about the set of roles.
# ``create_constraint=True`` is not decoration: SQLAlchemy defaults it to False, and ``bookings``
# spent five migrations as a bare VARCHAR that would have accepted ``status = 'banana'`` for exactly
# that reason (see ``db/models/booking.py``). ==A role column that accepts 'owher' grants nothing,
# denies everything, and does it in silence.==
_MEMBER_ROLE = sa.Enum(
    MemberRole,
    name="member_role",
    native_enum=False,
    length=16,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


class Tenant(UUIDPrimaryKey, Timestamps, Base):
    """An isolated organization. Every other row is scoped to a tenant via ``tenant_id``.

    .. rubric:: The four branding columns (B-07 / RF-27), and why they live HERE

    ``slug`` and ``name`` are the operator's handles on the business: one routes, the other is what
    an invoice is made out to. Neither is a thing a GUEST should be shown, and until 0014 there was
    nothing else — so every business on a shared instance served a booking page headed "AetherCal",
    in AetherCal's colours, in UTC. The page was the product's, not theirs.

    * ``public_name`` — the name the guest reads. Nullable, and
      :func:`~aethercal.schemas.branding.resolve_display_name` falls back to ``name``: a business
      that has not chosen a trading name still has a name.
    * ``logo_url`` — an ``https`` URL, validated in ``schemas.branding``. The server never fetches
      it; the guest's browser does (that validator's docstring explains why that is a different
      threat model from the webhook allowlist, and why copying the allowlist here would be cargo
      cult).
    * ``accent_color`` — a hex triplet, and only a hex triplet: the value is interpolated into a
      ``<style>`` block, so the FORMAT is the injection belt.
    * ``timezone`` — ==NOT NULL, defaulted to UTC==, alone among the four. The others degrade to
      "the page shows a little less"; an absent timezone degrades to "the page shows the wrong
      TIME", and every slot on the page needs one. It is not a new fact — the booking page has been
      hard-coding ``DEFAULT_TZ = "UTC"`` all along — it is that fact moved somewhere the operator
      can reach. Existing rows therefore default to exactly the zone they were already displayed in.

    .. warning::
       ==``tenants`` carries NO row-level-security policy, by design== (migration 0008: the admin
       reads it by slug at boot, before any GUC can exist, and the public router makes slugs
       semi-public anyway). So the ``app`` role **can read every business's branding** — RLS is not
       what keeps one business's mark off another's page. ==The belt is the ``WHERE tenants.id =
       :tenant_id`` in :mod:`aethercal.server.services.branding`, and it is load-bearing.== It is
       asserted from both ends in ``tests/rls/test_branding_isolation.py``: that the policy really
       is absent (so nobody mistakes RLS for the guard), and that the service is exact regardless.
    """

    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(sa.String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)

    # -- branding (migration 0014) ---------------------------------------------------------
    public_name: Mapped[str | None] = mapped_column(sa.String(255))
    logo_url: Mapped[str | None] = mapped_column(sa.String(2048))
    accent_color: Mapped[str | None] = mapped_column(sa.String(7))
    # Both defaults, deliberately: the ``server_default`` is what backfills the rows that already
    # exist (and what a raw INSERT from psql gets); the Python-side ``default`` is what keeps a
    # freshly constructed, not-yet-flushed ``Tenant`` from reading back ``None`` — the CLI's
    # ``create-tenant`` holds exactly that object.
    timezone: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default="UTC", server_default=sa.text("'UTC'")
    )


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


class Membership(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """What a person IS to a business: the row that turns a HOST into somebody who may sign in.

    .. rubric:: Why this is a table and not a column on ``users``

    A ``users`` row is a HOST — the person an event type is offered against, whose name signs the
    confirmation email, into whose calendar the booking is written. Every business has hosts who
    administer nothing (and, until this migration, every host in the product had
    ``hashed_password = NULL`` and no way to sign in at all). ==Being bookable and being allowed to
    administer are different facts==, and a ``role`` column on ``users`` would have forced a default
    on every existing host: whatever value that default took, it would either lock the real
    administrators out or hand the panel to everybody. A separate table has no default. A host with
    no membership signs in nowhere, which is the only safe thing an absent fact can mean.

    .. rubric:: The role is checked in the SERVICE layer, never by RLS

    Row-level security compares ``tenant_id`` and nothing else. Every row a ``member`` of Acme
    writes carries Acme's ``tenant_id``, so ==every policy in the database says yes to them deleting
    Acme's hosts==. The belt isolates BUSINESSES; ``services/rbac.py`` authorises PEOPLE. This is
    input to the second one, and it is not — and cannot be — enforced by the first.

    .. rubric:: The host must belong to THIS business, and the service is what says so

    ``user_id`` is a plain foreign key to ``users.id`` (the shape every other table here uses:
    ``event_types.host_id``, ``schedules.user_id``, ...). So a row carrying Acme's ``tenant_id`` and
    a Globex ``user_id`` would satisfy the constraint — and would let a person of Globex sign in to
    Acme, with every RLS policy waving it through, because the row IS Acme's. Two things stop it:
    ``services/memberships.py`` resolves the host **within the business** before granting (so the
    foreign id raises ``UserNotFoundError``), and under RLS that foreign row is not readable at all.

    ==The database-level version of that guard — a composite FK ``(tenant_id, user_id)`` → a unique
    ``users (id, tenant_id)`` — is DECLARED FUTURE WORK, not an oversight.== Adding it means a
    ``batch_alter_table`` on ``users``, and migration 0007 warns in writing that batch-altering that
    table silently DROPS the functional unique index on ``(tenant_id, lower(email))`` on SQLite —
    trading a hole two layers already close for a regression in the one invariant the database
    genuinely holds alone. It belongs in a migration whose whole subject is ``users``, with that
    index recreated explicitly, and it is written down here so the next person finds the reasoning
    rather than the gap.
    """

    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MemberRole] = mapped_column(_MEMBER_ROLE, nullable=False)

    __table_args__ = (
        # One person, one role, one business. Two rows would be two answers to "what may they do",
        # and the code would take whichever it read first — which is how a demoted owner keeps their
        # panel.
        sa.UniqueConstraint("tenant_id", "user_id"),
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


__all__ = ["ApiKey", "MemberRole", "Membership", "Tenant", "User"]
