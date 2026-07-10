"""busy-cache coverage window on external_connections (F1-07, RF-12/13).

Adds the synced busy-window bounds and timestamp so ``read_busy`` can judge freshness by whether the
cache actually COVERS the queried window (not merely by a recent per-row ``fetched_at``). This closes
the double-booking risk where a cache filled for one window read as fresh for a different window.

Forward-only expand migration (RF-19): the three columns are nullable and additive, so the change is
safe to apply online with the app running (NULL = "never synced"). There is no destructive downgrade
of these columns (see ``downgrade``); they are removed only when 0001 drops the table.

Revision ID: 0002_busy_cache_coverage
Revises: 0001_initial
Create Date: 2026-07-09 18:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0002_busy_cache_coverage'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'external_connections',
        sa.Column('busy_synced_from', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'external_connections',
        sa.Column('busy_synced_to', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'external_connections',
        sa.Column('busy_synced_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Forward-only expand: no stand-alone column drop (SQLite has no DROP COLUMN outside batch mode,
    # and the columns are harmless nullable additions). Downgrade-to-base drops the table via 0001.
    pass
