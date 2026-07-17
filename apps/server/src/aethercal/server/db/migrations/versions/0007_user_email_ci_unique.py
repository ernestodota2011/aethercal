"""a host's address is unique per business CASE-INSENSITIVELY — enforced by the DATABASE.

``users`` has been ``UNIQUE (tenant_id, email)`` since 0001 — on the EXACT string. So
``Ana@example.com`` and ``ana@example.com`` are two hosts to the database and ONE human being to
everybody else: a host selector offering two of somebody, an event type landing on whichever was
clicked, and mail going to whichever row happens to be read first.

``services/users.py`` already refuses that pair case-insensitively, and that closes the hole a
person walks into. It cannot close the other one: the guard is a **check-then-act** — read, find
nobody, write — and two CONCURRENT creates (two admin tabs, an admin racing ``create-tenant``, a
retried request) can each read "nobody has that address" and each land. Nothing in the application
layer can fix that; the window between the read and the write is exactly where the second writer
lives. ==An invariant the database does not hold is not an invariant.==

So this migration moves it where it belongs: a functional unique index on
``(tenant_id, lower(email))``.

.. rubric:: The exact-string UNIQUE is SUBSTITUTED, not kept alongside

``lower(a) = lower(b)`` whenever ``a = b``, so a unique ``(tenant_id, lower(email))`` already
implies a unique ``(tenant_id, email)``: the old constraint now guarantees **nothing** the new index
does not. Keeping it would buy a second B-tree to maintain on every write and — the part that
actually bites — a second constraint name an ``IntegrityError`` could arrive under, so the service
would have to guess which invariant the database was complaining about in order to word its refusal.
One invariant, one index, one name.

.. rubric:: Portability: this is the SAME index on both backends

SQLite has had expression indexes since 3.9 and ``lower()`` is deterministic on both engines, so
``CREATE UNIQUE INDEX ... ON users (tenant_id, lower(email))`` is issued verbatim to PostgreSQL and
to SQLite alike. The offline suite therefore proves the same guarantee production enforces — there
is no "on SQLite you are on your own" caveat, and the service's guard is not quietly load-bearing
anywhere.

(The one seam worth writing down: SQLite's ``lower()`` is ASCII-only while PostgreSQL's is
locale-aware, so a pair differing only in the case of a NON-ASCII character would be caught by the
index on PostgreSQL and only by the service's guard on SQLite. Since SQLite serialises writers, the
guard cannot lose that race there — nothing is left unprotected.)

.. rubric:: The ORDER of the operations is load-bearing

The old constraint comes off inside ``batch_alter_table`` (SQLite has no ``ALTER TABLE ... DROP
CONSTRAINT``: Alembic reflects the table and rebuilds it). SQLAlchemy cannot REFLECT an
expression-based index on SQLite — it warns and skips it — so a rebuild performed while the new
index existed would silently drop it again. Hence: drop the old constraint FIRST, create the new
index SECOND, and mirror that in reverse on the way down.

.. rubric:: Existing data is not merged. It is NAMED, and the migration stops

``CREATE UNIQUE INDEX`` over data that already violates it fails — as a ``UniqueViolation`` naming
an index nobody has heard of, out of a deploy running at whatever hour deploys run. So the upgrade
LOOKS first, and if it finds a legacy pair it refuses **before touching anything**, printing the
tenant, the address they collide on, and every conflicting row with its id and its exact spelling.

It does not pick a survivor. Which of two hosts lives — and what becomes of the event types,
schedules and calendar connections hanging off the other, each of them attached to real bookings —
is an operator's decision with consequences a migration cannot see. A migration that makes it
silently, at 3 a.m., is far worse than one that stops and says why.

Revision ID: 0007_user_email_ci_unique
Revises: 0006_webhook_delivery_reason
Create Date: 2026-07-13 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0007_user_email_ci_unique'
down_revision: str | None = '0006_webhook_delivery_reason'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The revision id above is 25 characters, and counting them is not pedantry: Alembic stores it in
# `alembic_version.version_num`, a VARCHAR(32). A longer id passes the ENTIRE offline suite (SQLite
# does not enforce VARCHAR length) and then fails on PostgreSQL — with StringDataRightTruncation, in
# the UPDATE that stamps the version, at a self-hoster's first boot. `test_alembic_config.py` now
# guards every revision id offline; this comment is why.
_CI_INDEX = 'uq_users_tenant_id_email_lower'  # 30 chars < PostgreSQL's 63-byte identifier limit
_EXACT_UNIQUE = 'uq_users_tenant_id_email'


def _users() -> sa.Table:
    """A minimal Core table for the pre-flight read (never the ORM — a migration is frozen in time)."""
    return sa.Table(
        'users', sa.MetaData(),
        sa.Column('id', sa.Uuid()),
        sa.Column('tenant_id', sa.Uuid()),
        sa.Column('email', sa.String(320)),
        sa.Column('name', sa.String(255)),
    )


def _refuse_legacy_case_duplicates(bind: sa.Connection) -> None:
    """Stop — loudly, and by NAME — if the data already contradicts the index we are about to create.

    The key is computed by the DATABASE's own ``lower()``, not by Python's: the check has to ask the
    exact question the index will ask, or it is a different question with a different answer
    (Python's ``str.lower`` and SQLite's ASCII-only ``lower()`` disagree on non-ASCII, and a guard
    that disagrees with the constraint it guards is worse than no guard).
    """
    users = _users()
    rows = bind.execute(
        sa.select(
            users.c.id,
            users.c.tenant_id,
            users.c.name,
            users.c.email,
            sa.func.lower(users.c.email).label('key'),
        ).order_by(users.c.tenant_id, sa.func.lower(users.c.email), users.c.id)
    ).fetchall()

    groups: dict[tuple[str, str], list[sa.Row[tuple[object, ...]]]] = {}
    for row in rows:
        groups.setdefault((str(row.tenant_id), str(row.key)), []).append(row)
    conflicts = {key: group for key, group in groups.items() if len(group) > 1}
    if not conflicts:
        return

    blocks: list[str] = []
    for (tenant_id, key), group in conflicts.items():
        listing = '\n'.join(f'      - {row.id}  {row.email!r}  ({row.name})' for row in group)
        blocks.append(f'  tenant {tenant_id}, address {key!r}:\n{listing}')
    detail = '\n'.join(blocks)

    raise RuntimeError(
        "migration 0007 cannot make a host's address unique per business: this database ALREADY "
        f'holds {len(conflicts)} address(es) carried by more than one host, differing only in '
        'capitalisation.\n'
        '\n'
        f'{detail}\n'
        '\n'
        '==Nothing has been changed.== These rows are NOT merged automatically. Two rows for one '
        'person is a defect, but choosing WHICH host survives — and what becomes of the event '
        'types, schedules and calendar connections hanging off the other, each of them attached to '
        "real bookings — is the operator's decision, not a migration's.\n"
        '\n'
        'Re-address or delete the duplicate(s), then run the migration again. (Left to itself, '
        '`CREATE UNIQUE INDEX` would have failed here anyway — with a unique-violation naming an '
        'index you have never heard of, and not one word about which rows caused it.)'
    )


def upgrade() -> None:
    _refuse_legacy_case_duplicates(op.get_bind())

    # FIRST the drop (batch mode: SQLite has no DROP CONSTRAINT, so Alembic rebuilds the table), and
    # only THEN the new index — a rebuild cannot reflect an expression index and would drop it again
    # on its way through. See the module docstring.
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_constraint(op.f(_EXACT_UNIQUE), type_='unique')

    op.create_index(_CI_INDEX, 'users', ['tenant_id', sa.text('lower(email)')], unique=True)


def downgrade() -> None:
    # The mirror image: the expression index goes FIRST, so the table rebuild below never has to
    # reflect it. The exact-string UNIQUE it restores is strictly WEAKER than the index it replaces,
    # so it cannot fail on data the new schema allowed — a rollback that only works on data the new
    # schema cannot produce is not a rollback.
    op.drop_index(_CI_INDEX, table_name='users')

    with op.batch_alter_table('users') as batch_op:
        batch_op.create_unique_constraint(op.f(_EXACT_UNIQUE), ['tenant_id', 'email'])
