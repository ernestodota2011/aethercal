"""0007 — a host's address becomes unique per business CASE-INSENSITIVELY, in the DATABASE.

``users`` has been unique on ``(tenant_id, email)`` since 0001 — on the EXACT string. So
``Ana@example.com`` and ``ana@example.com`` are two hosts to the database and one human being to
everyone else, and the service's guard (``_ensure_email_available``) is a check-then-act: it closes
the hole a person walks into, and cannot close the one two CONCURRENT creates walk into. An
invariant the database does not hold is not an invariant.

These run the REAL migration against throwaway SQLite files, so they execute on every CI cell with
no Postgres. They pin three things:

1. the migration REPLACES the exact-string ``UNIQUE`` with a functional unique index on
   ``(tenant_id, lower(email))``, and the migrated database then refuses the pair on its own — with
   no service in the way at all;
2. a database that ALREADY holds such a pair does not meet a cryptic ``UniqueViolation`` thrown out
   of ``CREATE UNIQUE INDEX`` at three in the morning. ==The migration stops BEFORE it touches
   anything and NAMES the conflicting rows.== It does not merge them: which host survives — and
   which event types, schedules and calendar connections move with them — is an operator's
   decision, not a migration's;
3. the downgrade actually works. A rollback you discover is broken mid-incident is not a rollback.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from aethercal.server.db.migrate import make_alembic_config, run_migrations

_BEFORE = "0006_webhook_delivery_reason"  # the revision 0007 chains onto
_CI_INDEX = "uq_users_tenant_id_email_lower"


def _engine(tmp_path: Path, name: str) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / name}")


def _tenant(conn: sa.Connection, slug: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    conn.exec_driver_sql(
        f"INSERT INTO tenants (id, slug, name) VALUES ('{tenant_id.hex}', '{slug}', '{slug}')"
    )
    return tenant_id


def _host(conn: sa.Connection, tenant_id: uuid.UUID, email: str, name: str = "Host") -> uuid.UUID:
    """A ``users`` row written with NO service in the way — which is what a race amounts to."""
    user_id = uuid.uuid4()
    conn.exec_driver_sql(
        "INSERT INTO users (id, tenant_id, email, name, timezone) VALUES "
        f"('{user_id.hex}', '{tenant_id.hex}', '{email}', '{name}', 'UTC')"
    )
    return user_id


def _users_schema(engine: sa.Engine) -> tuple[str, dict[str, str]]:
    """The ``users`` CREATE TABLE statement and every index on it, straight from ``sqlite_master``.

    Read raw rather than through ``Inspector.get_indexes``: SQLAlchemy cannot reflect an
    expression-based index on SQLite and WARNS when it meets one — and this suite promotes warnings
    to errors. The catalogue is the source of truth anyway.
    """
    with engine.begin() as conn:
        table_sql: str = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).scalar_one()
        indexes = {
            str(row[0]): str(row[1] or "")
            for row in conn.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'users'"
            )
        }
    return table_sql, indexes


def test_the_migrated_database_carries_the_case_insensitive_unique_index(tmp_path: Path) -> None:
    """==The exact-string UNIQUE is SUBSTITUTED, not kept alongside.==

    ``lower(a) = lower(b)`` whenever ``a = b``, so a unique ``(tenant_id, lower(email))`` already
    implies a unique ``(tenant_id, email)``: the old constraint now guarantees nothing the new index
    does not. Keeping it would buy a second B-tree on every write and — worse — a second name an
    ``IntegrityError`` could arrive under, which is exactly the ambiguity the service must not have
    to guess at.
    """
    engine = _engine(tmp_path, "ci_index.sqlite")
    run_migrations(engine)

    table_sql, indexes = _users_schema(engine)

    assert _CI_INDEX in indexes, "the case-insensitive unique index was not created"
    assert "lower(email)" in indexes[_CI_INDEX].lower()
    assert "UNIQUE" in indexes[_CI_INDEX].upper()

    # The exact-string constraint is GONE — from the table definition, and with it the implicit
    # index SQLite kept for it. Asserted as the WHOLE index set, so neither a survivor nor a
    # casualty can hide: ``sqlite_autoindex_users_1`` is the PRIMARY KEY's (a CHAR(32) key is not a
    # rowid alias, so it gets one) and stays; ``sqlite_autoindex_users_2``, which was the UNIQUE's,
    # is the one that had to go; and ``ix_users_tenant_id`` proves the table REBUILD the drop needs
    # on SQLite did not quietly cost us an index it was supposed to carry across.
    assert "UNIQUE" not in table_sql.upper(), f"the redundant exact UNIQUE survived: {table_sql}"
    assert set(indexes) == {"sqlite_autoindex_users_1", "ix_users_tenant_id", _CI_INDEX}
    engine.dispose()


def test_the_migrated_database_refuses_a_case_variant_pair_by_itself(tmp_path: Path) -> None:
    """No service, no guard, no application: the DATABASE refuses the second row.

    This is the whole point of the migration. The service's check-then-act cannot survive two
    concurrent creates; a unique index does not care how many transactions are in flight.
    """
    engine = _engine(tmp_path, "refusal.sqlite")
    run_migrations(engine)

    with engine.begin() as conn:
        acme = _tenant(conn, "acme")
        _host(conn, acme, "Ana@example.com")

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _host(conn, acme, "ana@example.com")

    # And uniqueness is still per BUSINESS: one person may host for two of them.
    with engine.begin() as conn:
        other = _tenant(conn, "other")
        _host(conn, other, "ana@example.com")
    engine.dispose()


def test_a_legacy_pair_stops_the_migration_and_names_the_rows(tmp_path: Path) -> None:
    """==The failure mode this migration is not allowed to have.==

    ``CREATE UNIQUE INDEX`` over data that already violates it fails — with a database's own error,
    naming an index nobody has heard of, at whatever hour the deploy runs. So the migration looks
    FIRST, refuses, and says exactly which rows are in the way.

    It does NOT merge them. Choosing which of two hosts survives (and what happens to the event
    types, schedules and calendar connections hanging off the other) is a decision with consequences
    for real bookings; a migration that makes it silently, at 3 a.m., is worse than one that stops.
    """
    engine = _engine(tmp_path, "legacy.sqlite")
    config = make_alembic_config(str(engine.url))
    command.upgrade(config, _BEFORE)

    with engine.begin() as conn:
        acme = _tenant(conn, "acme")
        ana_upper = _host(conn, acme, "Ana@example.com", name="Ana")
        ana_lower = _host(conn, acme, "ana@example.com", name="Ana Ruiz")

    with pytest.raises(RuntimeError) as raised:
        command.upgrade(config, "head")

    message = str(raised.value)
    # It NAMES the rows: the tenant, the address they collide on, and each id with its exact
    # spelling — everything the operator needs to pick a survivor without going hunting.
    assert str(acme) in message
    assert str(ana_upper) in message
    assert str(ana_lower) in message
    assert "Ana@example.com" in message
    assert "ana@example.com" in message

    # Nothing was changed: both rows are still there (nobody was merged away behind the operator's
    # back), and the database is still on the previous revision.
    with engine.begin() as conn:
        survivors = {
            str(row[0]) for row in conn.exec_driver_sql("SELECT email FROM users").fetchall()
        }
        revision = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    assert survivors == {"Ana@example.com", "ana@example.com"}
    assert revision == _BEFORE
    engine.dispose()


def test_the_downgrade_restores_the_exact_unique_constraint(tmp_path: Path) -> None:
    """A rollback that only works on data the new schema cannot produce is not a rollback."""
    engine = _engine(tmp_path, "downgrade.sqlite")
    config = make_alembic_config(str(engine.url))
    run_migrations(engine)

    command.downgrade(config, _BEFORE)

    table_sql, indexes = _users_schema(engine)
    assert _CI_INDEX not in indexes
    assert "UNIQUE" in table_sql.upper()  # the exact-string constraint is back
    assert "ix_users_tenant_id" in indexes

    # And the old (broken) semantics are back with it, which is what a downgrade MEANS: the case
    # variant is accepted again, while the exact duplicate is still refused.
    with engine.begin() as conn:
        acme = _tenant(conn, "acme")
        _host(conn, acme, "Ana@example.com")
        _host(conn, acme, "ana@example.com")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _host(conn, acme, "ana@example.com")
    engine.dispose()
