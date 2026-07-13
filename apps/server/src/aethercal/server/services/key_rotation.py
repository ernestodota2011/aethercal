"""Rotating the instance key: re-encrypt every stored secret. ==Lose nothing. Expose nothing.==

The Fernet key is derived from ``AETHERCAL_APP_SECRET``, so "rotate the key" means: set a new app
secret, keep the old one to hand, and re-encrypt everything written under it. Between those two
moments the database holds ciphertext under the OLD key while the process holds the NEW one — and
that gap is where a rotation goes wrong. Quietly, and in three different ways.

**It misses a column.** Those rows stay on the old key. The rotation says "done", the operator
retires the old secret, and that column is unreadable by anything, for ever. The symptom surfaces
weeks later, somewhere else entirely, as an ``InvalidToken`` on data nobody can recover.
→ The columns are **derived from the models**, never listed here:
:func:`~aethercal.server.db.encrypted.encrypted_columns`. A future encrypted column is rotated on
the day it lands, and ``tests/test_encrypted_columns.py`` fails CI if one arrives without declaring
that it holds ciphertext.

**It half-finishes.** Some rows on the new key, some on the old, and nothing to say which.
→ It runs in ==**ONE transaction**==: every row, or none. A row that decrypts under neither key
raises :class:`KeyRotationError` and the whole rotation rolls back — the database is left exactly as
it was, which is the only state a failed rotation may leave behind. Skipping the row would be worse
than failing: it would produce a database nobody can describe, under a report that said "success".

**It exposes what it rotated.** A plaintext in a terminal, in a CI log, in a shell history.
→ The plaintext exists only inside :func:`~aethercal.server.crypto.rotate_secret`, between one
decrypt and one encrypt. Nothing here returns it, logs it or holds it: :class:`RotationReport` is
**counts**, and the refusal names the table, the column and the row's *id*.

.. rubric:: Who runs it

The OWNER role, from the CLI (``aethercal-admin credentials rotate-key``). It has to read EVERY
business's rows, and under row-level security the app role sees only the business bound to its
session — which, for this operation, is none of them. It is the same reason ``guest purge`` runs as
the owner, and it is why this function is not reachable from the web process at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import rotate_secret
from aethercal.server.db import Base
from aethercal.server.db.encrypted import EncryptedColumn, encrypted_columns
from aethercal.server.db.roles import DbRole


class KeyRotationError(RuntimeError):
    """The rotation refused to proceed, or could not finish. ==It stops; it never half-does.==

    Two causes, one principle: a rotation may fail and a rotation may succeed, but a rotation must
    never *appear* to succeed having moved nothing.

    * the session is not on the OWNER role (see :func:`_assert_the_owner_is_rotating`);
    * a row decrypts under neither key (see :func:`rotate_fernet_key`).
    """


async def _assert_the_owner_is_rotating(session: AsyncSession) -> None:
    """==The rotation must be on the OWNER role, and this is the only way to know.==

    Under row-level security, a rotation on the app role does not fail. It reads **zero rows** —
    from every column, in silence — walks the whole schema, finds nothing to do, and reports
    ``rotated 0 row(s)``. ==Success.== The operator, believing every credential has been moved onto
    the new key, then retires the old app secret. At that instant every credential on the instance
    becomes ciphertext that nothing in the world can decrypt: the payment keys, the webhook secrets
    and the calendar tokens of every business, gone, with a green run in the log to say otherwise.

    It cannot be detected from the query results — zero rows is exactly what an empty instance looks
    like. So the connection is asked WHO IT IS: the same assertion the web process, the worker and
    every CLI invocation already make at boot, for the same reason. Under RLS this failure mode
    produces no exception, so the only detector that can exist is to go and look.

    A no-op on SQLite, which has neither roles nor row-level security — and therefore nothing to be
    silently blind to. The offline suite runs there, sees every row, and rotates them all.
    """
    if session.get_bind().dialect.name != "postgresql":
        return

    role = await session.scalar(sa.text("SELECT current_user"))
    if role == DbRole.OWNER.value:
        return

    raise KeyRotationError(
        f"a key rotation must run as {DbRole.OWNER.value}, and this session is {role!r}.\n"
        "\n"
        "==Refusing, because the alternative is silent and irreversible.== Under row-level "
        f"security the {role!r} role sees only the business bound to its session — none, here — "
        "so this rotation would have read ZERO rows from every column, rewritten nothing, and "
        "reported success. The old app secret would then be retired on the strength of that "
        "report, and every credential on the instance would become undecryptable, permanently.\n"
        "\n"
        "Run it through the CLI (`aethercal-admin credentials rotate-key`), which builds its "
        "engine from AETHERCAL_OWNER_DATABASE_URL."
    )


@dataclass(frozen=True, slots=True)
class RotationReport:
    """What a rotation did, in NUMBERS. ==It cannot carry a secret, because it never holds one.==

    Every encrypted column appears, including those with no rows: a ``0`` must read as *visited and
    empty*, never as *forgotten*. That distinction is the whole difference between a rotation an
    operator can trust and one they merely hope about.
    """

    rewritten: dict[str, int]
    """``"table.column" → rows re-encrypted``. One entry per encrypted column in the models."""

    @property
    def total(self) -> int:
        """Every row this rotation re-encrypted, across every column."""
        return sum(self.rewritten.values())

    def summary(self) -> str:
        """One line for the operator's terminal: the columns, and the counts. Nothing else."""
        columns = " ".join(f"{name}={count}" for name, count in sorted(self.rewritten.items()))
        return f"rotated {self.total} row(s): {columns}"


def _table_of(column: EncryptedColumn) -> sa.Table:
    return Base.metadata.tables[column.table]


async def rotate_fernet_key(
    session: AsyncSession, *, new_key: bytes, previous_key: bytes
) -> RotationReport:
    """Re-encrypt every stored secret from ``previous_key`` onto ``new_key``. ==Criterion 42.==

    Runs inside the CALLER's transaction, and that is load-bearing: the caller commits once, at the
    end, so a failure anywhere rolls back everything this touched. Do not commit half-way.

    Rows already under ``new_key`` are rewritten harmlessly — which is what makes an interrupted
    rotation safe to simply run again.

    Raises :class:`KeyRotationError` on a row that decrypts under neither key, naming the table, the
    column and the row's id, and NEVER its contents — or when it is not running as the OWNER, which
    is the one refusal that stands between an operator and a permanently undecryptable instance.
    """
    await _assert_the_owner_is_rotating(session)

    rewritten: dict[str, int] = {}

    for column in encrypted_columns(Base.metadata):
        table = _table_of(column)
        primary_key = table.c[column.primary_key]
        ciphertext = table.c[column.column]
        name = f"{column.table}.{column.column}"
        rewritten[name] = 0

        # Loaded in one go: these tables hold one row per business per provider (and one per webhook
        # subscription), so they are small by construction — there is no design in which an instance
        # has a million credentials. If that ever stops being true, THIS is the line to batch, and
        # the batching has to stay inside the single transaction.
        rows: list[Any] = list(await session.execute(sa.select(primary_key, ciphertext)))

        for row_id, token in rows:
            try:
                rotated = rotate_secret(bytes(token), new_key=new_key, previous_key=previous_key)
            except InvalidToken as exc:
                raise KeyRotationError(
                    f"{name}: row {row_id} decrypts under NEITHER the new key nor the previous "
                    "one, so the rotation has stopped and nothing has been written.\n"
                    "\n"
                    "==Nothing changed.== The whole rotation runs in one transaction precisely so "
                    "that this failure cannot leave half the instance on one key and half on the "
                    "other — a state with no way back, and no way to tell which row is which.\n"
                    "\n"
                    "Either AETHERCAL_PREVIOUS_APP_SECRET is not the secret this row was written "
                    "under (check it and re-run — the rotation is resumable), or that row was "
                    "written by something that did not use the instance key at all, in which case "
                    "it is already unreadable and must be repaired or removed before the rotation "
                    "can complete."
                ) from exc

            await session.execute(
                sa.update(table)
                .where(primary_key == row_id)
                .values({column.column: rotated})
                .execution_options(synchronize_session=False)
            )
            rewritten[name] += 1

    return RotationReport(rewritten=rewritten)


__all__ = ["KeyRotationError", "RotationReport", "rotate_fernet_key"]
