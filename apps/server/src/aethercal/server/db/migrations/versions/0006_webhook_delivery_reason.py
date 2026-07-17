"""why a webhook delivery did not arrive (the silent dead-letter fix).

Adds ``webhook_deliveries.error_reason``: a short, stable token saying WHY the last attempt did not
succeed — ``blocked-private-target``, ``blocked-dns-rebind``, ``dns-failure``, ``transport-error``,
``http-error``, ``no-subscriber`` — or NULL when the delivery succeeded.

Until now ``dead`` was the answer to four different questions. An SSRF attempt, a self-hoster's OWN
LAN address (with no allowlist declared), a DNS blip and a consumer that 5xx'd six times all left
behind the identical row: ``status = 'dead'``, ``response_code = NULL``, nothing else. An operator
whose n8n was receiving no events had nowhere to look — the failure was not only real, it was
invisible. A log line alone does not fix that (it is gone by the time anybody asks) and neither does
a process counter (it resets on restart, and reads zero from the process that is not running the
scheduler). The reason belongs on the row.

Nullable, no backfill: rows written before this revision genuinely do not know why they failed, and
inventing a reason for them would be worse than admitting it. NULL therefore means either "delivered"
or "failed before this column existed" — and ``status`` tells those two apart.

``downgrade`` drops it via ``batch_alter_table`` so it also runs on SQLite, consistent with 0002-0005
(production only ever migrates forward).

⚠️ The revision id must fit ``alembic_version.version_num``, which Alembic creates as ``VARCHAR(32)``.
SQLite does not enforce a VARCHAR length, so an over-long id sails through the entire offline suite
and only explodes on PostgreSQL — at the UPDATE that stamps the version, i.e. during a production
boot migration. ``0006_webhook_delivery_error_reason`` was 34 characters and did exactly that; this
id is 28. It was caught by the ``-m db`` suite, which is precisely why that suite must FAIL rather
than skip when Postgres is absent.

Revision ID: 0006_webhook_delivery_reason
Revises: 0005_workflows_and_noshow
Create Date: 2026-07-13 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0006_webhook_delivery_reason'
down_revision: str | None = '0005_workflows_and_noshow'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'webhook_deliveries',
        sa.Column('error_reason', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table('webhook_deliveries') as batch_op:
        batch_op.drop_column('error_reason')
