"""per-business branding: the public name, the logo, the accent colour and the timezone (B-07).

Until now ``tenants`` held ``slug`` and ``name`` and nothing else. Both are the OPERATOR's handles
on a business — one routes, the other is what an invoice is made out to — and neither is a thing a
guest should be shown. So every business sharing an instance served the same page: headed
"AetherCal", in AetherCal's colours, with its times in UTC. Multi-business was real in the database
and invisible on the surface the customer actually looks at.

Four columns, and the asymmetry between them is the design:

* ``public_name``, ``logo_url``, ``accent_color`` — NULLABLE. "Unset" is a real state (a business
  with no logo has none), and it is the state every existing row is in. A NOT NULL column with an
  empty-string default would invent a *second* spelling of "unset" for the resolver to disagree
  with.
* ``timezone`` — ==NOT NULL, ``DEFAULT 'UTC'``==. Every other field degrades to "the page shows a
  little less"; an absent timezone degrades to "the page shows the wrong TIME", and there is no
  rendering of a slot that does not need a zone. This is not a new fact being introduced — the
  booking page has been hard-coding ``DEFAULT_TZ = "UTC"`` since it was written. It is that fact
  moved to where the operator can reach it, so ==every existing row backfills to exactly the zone it
  was already being displayed in==. Zero behaviour change on the day this lands; a real one the day
  an operator sets theirs.

The server default is KEPT (0004 dropped its own, deliberately, because the app's ``default=dict``
owned that JSON blob). It is kept here because a NOT NULL column on a table the runbook and the CLI
both INSERT into by hand must have an answer at the DATABASE, not only in the ORM.

.. rubric:: No policy, no new GRANT — and both are worth saying out loud

``tenants`` deliberately carries **no** RLS policy (0008: the admin resolves it by slug at boot,
before any GUC can exist). Adding columns to it therefore adds no policy work — and it also means
==the ``app`` role can read every business's branding==. What keeps one business's mark off
another's page is the ``WHERE tenants.id = :tenant_id`` in ``services/branding.py``, not the
database. That is stated where it can be acted on (the model, the service, and
``tests/rls/test_branding_isolation.py``, which asserts both halves) rather than left to be
discovered.

The ``GRANT`` needs no touching either: 0008 granted SELECT/INSERT/UPDATE/DELETE on the TABLE, and a
table-level grant covers columns added later.

.. rubric:: ``down_revision`` will be rebased at merge — that is expected, not a defect

B-02…B-05 each carry a migration too, and they are built in parallel worktrees off the same B-01
parent. The batch spec reserves the numbers (0009…0014) and declares the order; whichever wave
merges second rebases its ``down_revision`` onto the head the first one left. That merge step has
been applied here: the parent is now ``0012_booking_confirmed_at`` — the head the earlier waves
(0009…0012) left — and the rebase was mechanical (the upgrade only ADDs columns to ``tenants``, so
it is independent of everything between 0008 and 0012). The revision ID itself is the reserved one
and does not move.

Revision ID: 0014_tenant_branding
Revises: 0012_booking_confirmed_at
Create Date: 2026-07-13 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_tenant_branding"
down_revision: str | None = "0012_booking_confirmed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("public_name", sa.String(length=255), nullable=True))
    op.add_column("tenants", sa.Column("logo_url", sa.String(length=2048), nullable=True))
    op.add_column("tenants", sa.Column("accent_color", sa.String(length=7), nullable=True))
    # NOT NULL with a server default, so the ADD COLUMN succeeds against existing rows with no
    # backfill step — and every one of them lands on 'UTC', which is what they were already being
    # displayed in. The default STAYS (see the module docstring).
    op.add_column(
        "tenants",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
    )


def downgrade() -> None:
    # Batch mode so DROP COLUMN works on SQLite too. Production migrates forward-only at boot, but a
    # real downgrade keeps this revision honest — and the offline parity suite exercises it.
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("timezone")
        batch_op.drop_column("accent_color")
        batch_op.drop_column("logo_url")
        batch_op.drop_column("public_name")
