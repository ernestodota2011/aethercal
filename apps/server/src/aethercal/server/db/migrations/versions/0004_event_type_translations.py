"""per-locale title/description overrides on event_types (i18n gate fix).

Adds ``title_translations`` and ``description_translations`` JSON columns to ``event_types``. The
existing ``title``/``description`` stay the canonical fallback (the tenant's base-locale text); the
new columns hold only sparse per-locale overrides, e.g. ``{"en": "Discovery call"}``. This closes
the web-qa-auditor NO-GO: the EN booking surface served the base-locale event-type text under
English chrome because there was nowhere to store an English override.

Forward-only expand migration (RF-19): both columns are added NOT NULL with a ``'{}'`` server
default so the ADD COLUMN succeeds against existing rows with zero backfill (an empty override map
reads exactly like "no translations yet"), then the server default is dropped so the app's
``default=dict`` stays the single source of truth. The ``downgrade`` symmetrically drops them (via
batch mode, so it works on SQLite too), though production only ever migrates forward.

Revision ID: 0004_event_type_translations
Revises: 0003_outbox_and_sequence
Create Date: 2026-07-10 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0004_event_type_translations'
down_revision: str | None = '0003_outbox_and_sequence'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'event_types',
        sa.Column('title_translations', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        'event_types',
        sa.Column(
            'description_translations', sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
    )
    # Drop the server default now that every row (existing and future) is covered; the app's
    # ``default=dict`` becomes the sole source of truth so it never drifts from the ORM. Batch mode
    # makes ALTER COLUMN work on SQLite too (it has no bare ALTER COLUMN).
    with op.batch_alter_table('event_types') as batch_op:
        batch_op.alter_column('title_translations', server_default=None)
        batch_op.alter_column('description_translations', server_default=None)


def downgrade() -> None:
    # Batch mode makes DROP COLUMN work on SQLite too; production migrates forward-only at boot,
    # but a real downgrade keeps this revision honest and consistent with 0002/0003.
    with op.batch_alter_table('event_types') as batch_op:
        batch_op.drop_column('description_translations')
        batch_op.drop_column('title_translations')
