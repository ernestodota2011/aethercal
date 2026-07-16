"""Structural invariants of the SQLAlchemy models — no database required.

These assert the F1-01 contract directly off ``Base.metadata``: every MVP entity exists, every
tenant-scoped table carries a non-null ``tenant_id`` foreign key to ``tenants``, the tenant-scoping
composite constraints are in place, and the anti-double-booking index exists. Metadata is built at
import time, so these run on every CI cell with no DB.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db import Base
from aethercal.server.db.migrate import run_migrations

# The F1 MVP core, plus the shared foundation these cuts land: the multichannel Workflow tables
# (migration 0005), ``memberships`` (migration 0009, B-02 — who is in a business, and what they may
# do there) and the per-business credential vault (migration 0010, B-03 — BYOK, encrypted at rest).
# Payments are still deliberately NOT here: they ship with their own cut of the payments/tenancy
# batch, carrying its own migration and its own gate.
# ``outbox`` (the transactional-outbox queue for a booking's post-commit effects, and now also the
# durable scheduler) landed with the F1-05 residual fix in migration 0003. This set is asserted
# EXACTLY, so an accidental omission or a stray extra table fails loudly — which is what it did the
# moment ``memberships`` arrived, and why this line is a decision rather than a rubber stamp.
EXPECTED_TABLES = {
    "tenants",
    "users",
    "api_keys",
    "schedules",
    "date_overrides",
    "event_types",
    "bookings",
    "guest_tokens",
    "external_connections",
    "external_calendar_links",
    "busy_cache",
    "webhooks",
    "webhook_deliveries",
    "sent_notifications",
    "outbox",
    # 0005 — multichannel workflows (RF-24/25).
    "workflows",
    "workflow_steps",
    "workflow_templates",
    # 0009 — RBAC (B-02, RF-27). Who is in a business, and what they may do there. It carries a
    # ``tenant_id`` like everything else, so the belt of 0008 reaches it (``tests/rls/`` derives the
    # scoped set from this same metadata and asserts the policy is really on the table in a real,
    # migrated PostgreSQL) — but ==RLS cannot enforce a ROLE==, and this table is the input to the
    # layer that can: ``services/rbac.py``.
    "memberships",
    # 0010 — BYOK: each business's own provider credentials, encrypted at rest (RF-27).
    "tenant_credentials",
    # 0013 — payments (B-05b, RF-26). ``payments`` is the money ledger the arbiter reasons over
    # (idempotency anchored on ``provider_ref``, NEVER ``event.id``); ``payment_events`` is the
    # "parking lot" — every inbound webhook event lands here first, so an event whose booking does
    # not exist yet is retried rather than lost, and the same ``event.id`` can never be replayed.
    "payments",
    "payment_events",
}

# tenants is the tenant root; every other table hangs off it via tenant_id.
TENANT_ROOT = "tenants"


def _unique_column_sets(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(col.name for col in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_exactly_the_mvp_tables_are_defined() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_tenant_scoped_table_has_a_non_null_tenant_id_fk() -> None:
    for name, table in Base.metadata.tables.items():
        if name == TENANT_ROOT:
            assert "tenant_id" not in table.c, "the tenant root must not carry its own tenant_id"
            continue
        assert "tenant_id" in table.c, f"{name} is missing tenant_id"
        column = table.c["tenant_id"]
        assert not column.nullable, f"{name}.tenant_id must be NOT NULL"
        fks = list(column.foreign_keys)
        assert len(fks) == 1, f"{name}.tenant_id must have exactly one foreign key"
        assert fks[0].column.table.name == TENANT_ROOT, f"{name}.tenant_id must reference tenants"


def test_every_table_has_a_primary_key() -> None:
    for name, table in Base.metadata.tables.items():
        assert list(table.primary_key.columns), f"{name} has no primary key"


def _unique_index_column_sets(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {tuple(col.name for col in index.columns) for index in table.indexes if index.unique}


def test_tenant_scoping_unique_constraints() -> None:
    # ``users`` is NOT here: a host's address is unique per business CASE-INSENSITIVELY, which is a
    # functional index rather than a column constraint — see the test immediately below.
    assert ("tenant_id", "slug") in _unique_column_sets("event_types")
    assert ("tenant_id", "name") in _unique_column_sets("schedules")
    # B-02: one person, one role, one business. Two rows would be two answers to "what may they do",
    # and the code acts on whichever it reads first — which is how a demoted owner keeps a panel.
    assert ("tenant_id", "user_id") in _unique_column_sets("memberships")
    assert ("tenant_id", "schedule_id", "date") in _unique_column_sets("date_overrides")
    assert ("tenant_id", "workflow_id", "position") in _unique_column_sets("workflow_steps")
    assert ("tenant_id", "channel", "kind", "locale") in _unique_column_sets("workflow_templates")


def test_a_hosts_address_is_unique_per_business_case_insensitively() -> None:
    """RF-30 / 0006: ``Ana@example.com`` and ``ana@example.com`` are ONE host, and the DATABASE is
    what says so.

    The old ``UNIQUE (tenant_id, email)`` was on the exact string, so the pair was two rows for one
    person: a selector offering both, an event type landing on whichever was clicked, mail going to
    whichever was read first. The service refuses it case-insensitively, but a check-then-act cannot
    survive two concurrent creates — only an index can.

    The exact-string constraint is SUBSTITUTED, not kept alongside: ``lower(a) = lower(b)`` whenever
    ``a = b``, so it now guarantees nothing this index does not, and a redundant unique is a second
    B-tree per write and a second constraint name an ``IntegrityError`` can arrive under.
    """
    table = Base.metadata.tables["users"]
    by_name = {index.name: index for index in table.indexes}
    assert "uq_users_tenant_id_email_lower" in by_name, "missing the case-insensitive unique index"
    index = by_name["uq_users_tenant_id_email_lower"]
    assert index.unique
    # Declared as an EXPRESSION, on both backends alike (SQLite has supported expression indexes
    # since 3.9), so the offline suite proves the same guarantee production enforces.
    assert [str(expression) for expression in index.expressions] == [
        "users.tenant_id",
        "lower(email)",
    ]
    assert ("tenant_id", "email") not in _unique_column_sets("users")


def test_the_notification_ledger_is_unique_per_kind_channel_and_step() -> None:
    """RF-24 generalises the ledger from ``(booking, kind)`` to
    ``(booking, kind, channel, step_id)`` — a workflow may send the same kind on two channels, and
    two steps of one workflow may share a kind.

    It MUST be expressed as the partial-index PAIR, not a single five-column ``UNIQUE``: on both
    PostgreSQL and SQLite, NULLs inside a UNIQUE compare as DISTINCT, so a flat constraint would
    admit unlimited duplicates for every row with ``step_id IS NULL`` — i.e. it would silently stop
    protecting the confirmation, the cancellation and the reminder, which is every message that
    exists today."""
    indexes = _unique_index_column_sets("sent_notifications")
    assert ("tenant_id", "booking_id", "kind", "channel") in indexes
    assert ("tenant_id", "booking_id", "kind", "channel", "step_id") in indexes

    by_name = {index.name: index for index in Base.metadata.tables["sent_notifications"].indexes}
    null_step = by_name["uq_sent_notifications_kind_channel"]
    real_step = by_name["uq_sent_notifications_kind_channel_step"]
    # Both predicates are declared for BOTH backends, so the offline (SQLite) suite proves the same
    # guarantee production (PostgreSQL) enforces.
    for index, predicate in ((null_step, "step_id IS NULL"), (real_step, "step_id IS NOT NULL")):
        for dialect in ("postgresql_where", "sqlite_where"):
            assert str(index.dialect_kwargs[dialect]) == predicate

    # The flat legacy constraint is GONE — leaving it would keep rejecting the second channel of a
    # workflow step, which is exactly what RF-24 needs to allow.
    assert ("tenant_id", "booking_id", "kind") not in _unique_column_sets("sent_notifications")


def test_the_outbox_carries_its_lease_columns() -> None:
    """R8: a worker claims a row and then does its network I/O with no transaction open. The lease
    is what makes that safe — a worker that dies mid-send must not strand the row forever."""
    table = Base.metadata.tables["outbox"]
    assert table.c["claimed_by"].nullable
    assert table.c["lease_expires_at"].nullable
    assert "ix_outbox_lease" in {index.name for index in table.indexes}


def test_a_workflow_may_apply_to_every_event_type() -> None:
    """RF-24: ``workflows.event_type_id`` NULL = the rule applies to all of the tenant's types."""
    assert Base.metadata.tables["workflows"].c["event_type_id"].nullable


def test_tenant_slug_is_globally_unique() -> None:
    column = Base.metadata.tables["tenants"].c["slug"]
    assert column.unique or ("slug",) in _unique_column_sets("tenants")


def test_api_key_prefix_is_globally_unique() -> None:
    # API auth extracts the prefix from the presented key, looks up the row (which identifies the
    # tenant), then verifies the hash — so the prefix must be unique across all tenants, not scoped
    # by tenant_id.
    column = Base.metadata.tables["api_keys"].c["prefix"]
    assert column.unique or ("prefix",) in _unique_column_sets("api_keys")


def test_booking_status_uses_the_core_vocabulary() -> None:
    column = Base.metadata.tables["bookings"].c["status"]
    assert isinstance(column.type, sa.Enum)
    assert set(column.type.enums) == {status.value for status in BookingStatus}


def test_anti_double_booking_partial_unique_index_exists() -> None:
    table = Base.metadata.tables["bookings"]
    by_name = {index.name: index for index in table.indexes}
    assert "uq_bookings_active_slot" in by_name, "missing the anti-double-booking index"
    index = by_name["uq_bookings_active_slot"]
    assert index.unique
    assert tuple(col.name for col in index.columns) == ("tenant_id", "event_type_id", "start_at")
    # The partial predicate (cancelled bookings free their slot) is PostgreSQL-specific.
    assert index.dialect_kwargs.get("postgresql_where") is not None


def test_the_tenant_carries_its_branding(tmp_path: Path) -> None:
    """B-07 / RF-27: a business has a public name, a logo, an accent colour and a timezone.

    The three optional ones are NULLABLE (a business that has set no logo has none, and ``""`` is
    not a logo — the resolver in ``schemas.branding`` treats blank as absent, so a NOT NULL column
    with an empty default would be a second, disagreeing, way to say "unset").

    ``timezone`` is the one that is **NOT NULL, defaulted to UTC**, and that asymmetry is the whole
    point of it. Every other field degrades to "the page shows a little less"; a missing timezone
    degrades to "the page shows the wrong TIME", and there is no rendering of a slot that does not
    need a zone. The booking page's hard-coded ``DEFAULT_TZ = "UTC"`` was exactly that absent value,
    spelled somewhere the operator could not reach — so existing rows land on the zone they were
    already being displayed in, and the column can never be the thing that is missing.
    """
    table = Base.metadata.tables["tenants"]

    for name in ("public_name", "logo_url", "accent_color"):
        assert name in table.c, f"tenants is missing {name}"
        assert table.c[name].nullable, f"tenants.{name} must be nullable (unset is a real state)"

    timezone = table.c["timezone"]
    assert not timezone.nullable, "tenants.timezone must be NOT NULL"
    assert timezone.server_default is not None, "tenants.timezone needs a server-side default"

    # Migration parity, offline: the migration must create exactly the columns the model declares.
    # A model column with no migration behind it passes every SQLite test and dies in production.
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'tenant_branding.sqlite'}")
    run_migrations(engine)
    inspector = sa.inspect(engine)
    actual = {col["name"] for col in inspector.get_columns("tenants")}
    assert {"public_name", "logo_url", "accent_color", "timezone"} <= actual
    engine.dispose()


def test_payments_idempotency_is_anchored_on_the_provider_ref() -> None:
    """B-05b / RF-26: the money's idempotency key is ``(tenant_id, provider, provider_ref)``.

    ==NEVER ``event.id``.== Stripe delivers two events with distinct ``event.id`` for one payment
    (``checkout.session.completed`` and ``payment_intent.succeeded``), so a UNIQUE on the event id
    would let the SAME payment be processed twice. The provider's *charge/reference* is the one
    identity that is stable across both events, so it is what the ledger row is unique on.
    """
    assert ("tenant_id", "provider", "provider_ref") in _unique_column_sets("payments")


def test_payment_events_anti_replay_is_on_the_event_id() -> None:
    """The parking lot's UNIQUE is ``(tenant_id, provider, event_id)`` — anti-replay of ONE event.

    Distinct from the payments UNIQUE above, and the two are not the same key by accident: a webhook
    endpoint must reject a re-POST of the very same event (``event_id``), while the ARBITER must
    collapse two DIFFERENT events about one payment (``provider_ref``). One table anchors each.
    """
    assert ("tenant_id", "provider", "event_id") in _unique_column_sets("payment_events")


def test_a_booking_carries_its_hold_and_its_confirming_payment() -> None:
    """B-05b: ``hold_expires_at`` (when an unpaid hold self-cancels) and
    ``confirmed_by_payment_id`` (==the discriminator of the WINNER==: which payment confirmed this
    booking, so the arbiter can tell "I am the payment that confirmed it" from "I am an orphan").
    Both nullable — a free booking has neither."""
    table = Base.metadata.tables["bookings"]
    assert table.c["hold_expires_at"].nullable
    assert table.c["confirmed_by_payment_id"].nullable
    fks = list(table.c["confirmed_by_payment_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "payments"


def test_an_event_type_carries_its_price_and_refund_rule() -> None:
    """B-05b: ``price_cents`` (NULL = FREE, and a free type confirms directly with no hold),
    ``currency``, ``refund_window_minutes`` and ``refund_kind`` (full | none — partial/tiered is
    F5). The arbiter validates a payment's amount+currency against these before it will confirm."""
    table = Base.metadata.tables["event_types"]
    assert table.c["price_cents"].nullable, "price_cents NULL means the type is free"
    for name in ("currency", "refund_window_minutes", "refund_kind"):
        assert name in table.c, f"event_types is missing {name}"


def test_naming_convention_yields_deterministic_constraint_names() -> None:
    # Deterministic names keep Alembic autogenerate drift-free across machines.
    fk = next(iter(Base.metadata.tables["users"].foreign_key_constraints))
    assert fk.name is not None
    assert fk.name.startswith("fk_users_")


def test_event_type_translation_columns_are_json_overrides_with_empty_default(
    tmp_path: Path,
) -> None:
    """web-qa-auditor NO-GO fix: the EN booking surface must not fall back to the tenant's
    base-locale ``title``/``description`` under English chrome. ``title``/``description`` stay the
    canonical fallback (the tenant's base locale); the ``*_translations`` columns hold only sparse
    locale overrides (e.g. ``{"en": "Discovery call"}``), defaulting to ``{}`` so no backfill is
    needed for existing rows."""
    table = Base.metadata.tables["event_types"]
    for name in ("title_translations", "description_translations"):
        column = table.c[name]
        assert isinstance(column.type, sa.JSON), f"{name} must be a JSON column"
        assert not column.nullable, f"{name} must be NOT NULL"
        assert column.default is not None and column.default.is_callable, (
            f"{name} must have a callable Python-side default"
        )
        assert column.default.arg(None) == {}, f"{name} must default to an empty dict"

    # SQLite parity: the migration must create exactly the columns the model declares (no DB server
    # required — this runs on every CI cell, same discipline as test_migration_parity.py).
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'event_type_translations.sqlite'}")
    run_migrations(engine)
    inspector = sa.inspect(engine)
    actual = {col["name"] for col in inspector.get_columns("event_types")}
    assert {"title_translations", "description_translations"} <= actual
    engine.dispose()
