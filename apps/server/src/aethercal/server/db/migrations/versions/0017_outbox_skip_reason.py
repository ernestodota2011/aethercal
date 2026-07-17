"""outbox.skip_reason: record WHY a skipped intent was skipped, not just THAT it was.

An ``OutboxSkipped`` ends an intent terminally and correctly — an unconfigured channel that can never
send should not burn six backoff attempts and dead-letter. But the REASON only reached the worker's
log. ``status='skipped'`` was queryable; *why* was a grep away, and it was the OPERATOR's grep:
"why did this business's reminder never go out?" had no answer on the row.

One nullable column. It is NULL for every non-skipped status (nothing to say), and the drain writes
the exception's text at settle time for a skip. ``services/tenant_senders.py`` documented this exact
column as the way to close the gap; this is that migration.

Revision ID: 0017_outbox_skip_reason
Revises: 0016_grant_alembic_version
Create Date: 2026-07-17 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# 23 characters — well inside ``alembic_version.version_num``'s VARCHAR(32); an over-long id would
# pass the whole offline suite and die on PostgreSQL at boot. ``tests/test_alembic_config.py`` guards
# every revision id offline.
revision: str = "0017_outbox_skip_reason"
down_revision: str | None = "0016_grant_alembic_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and no default: "no reason" is the honest state of every existing row and every future
    # non-skipped one, so the ADD COLUMN succeeds against existing data with no backfill.
    op.add_column("outbox", sa.Column("skip_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN, NOT batch mode (unlike 0014). This revision is the head, so a downgrade to
    # base reflects the full schema if it copies-and-moves the table — and that reflection warns on
    # `users`' expression-based unique index, which the parity suite treats as an error. A native
    # DROP COLUMN needs no reflection: `skip_reason` is a plain nullable column in no index, and
    # every SQLite that Python 3.11+ bundles (>= 3.37) and PostgreSQL both drop it directly.
    # Production migrates forward-only at boot; a real downgrade keeps this revision honest.
    op.drop_column("outbox", "skip_reason")
