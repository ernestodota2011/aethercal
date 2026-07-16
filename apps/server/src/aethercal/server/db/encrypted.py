"""Which columns hold Fernet ciphertext — ==derived from the models, never from a list in prose.==

A key rotation has to find **every** encrypted column. Miss one and the failure takes the worst
shape this codebase has a name for: the rotation reports success, the operator retires the old
secret, and the column that was skipped is still encrypted with it. Nothing raises that day. It
raises weeks later, on the first webhook delivery or the first charge, as an ``InvalidToken`` on
data that can no longer be decrypted by anything — the secret it needed is gone.

So the registry is not written down. It is DERIVED: a column declares itself with
``info={FERNET_AT_REST: True}``, and :func:`encrypted_columns` reads that off ``Base.metadata``.

The other half is the one that actually bites: ``tests/test_encrypted_columns.py`` asserts that the
set of columns carrying the marker is EXACTLY the set of ``LargeBinary`` columns in the metadata. A
new binary column therefore cannot arrive quietly — it either declares that it is encrypted (and the
rotation picks it up on the day it lands) or the suite goes red and somebody has to say, out loud,
that it is not. ==A list is a photograph; this is a belt.==
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import MetaData

FERNET_AT_REST = "fernet_at_rest"
"""The ``Column.info`` key that says "this column holds a Fernet token, encrypted with the instance
key". :func:`encrypted_columns` is its only reader, and the key rotation its only consumer."""


@dataclass(frozen=True, slots=True, order=True)
class EncryptedColumn:
    """One column of ciphertext, addressed by table and name. Carries no data — only where to look.

    ``primary_key`` is the column the rotation writes back by. Naming it here, rather than assuming
    ``id``, keeps the rotation's ``UPDATE`` honest about what it is matching on.
    """

    table: str
    column: str
    primary_key: str


def _collect(metadata: MetaData, *, by_marker: bool) -> tuple[EncryptedColumn, ...]:
    found: list[EncryptedColumn] = []
    for table_name, table in metadata.tables.items():
        primary_key = next((column.name for column in table.primary_key.columns), None)
        for column in table.columns:
            matches = (
                bool(column.info.get(FERNET_AT_REST))
                if by_marker
                else isinstance(column.type, sa.LargeBinary)
            )
            if not matches:
                continue
            if primary_key is None:  # pragma: no cover - every table has one (asserted in tests)
                raise RuntimeError(
                    f"{table_name}.{column.name} holds ciphertext but its table has no primary "
                    "key, so a key rotation would have no way to write the re-encrypted value back."
                )
            found.append(
                EncryptedColumn(table=table_name, column=column.name, primary_key=primary_key)
            )
    return tuple(sorted(found))


def encrypted_columns(metadata: MetaData) -> tuple[EncryptedColumn, ...]:
    """Every column declared ``info={FERNET_AT_REST: True}``.

    ==This is exactly what a key rotation must rewrite.==
    """
    return _collect(metadata, by_marker=True)


def binary_columns(metadata: MetaData) -> tuple[EncryptedColumn, ...]:
    """Every ``LargeBinary`` column, marked or not — the set the marker is checked AGAINST.

    In this product the two sets are equal, and the test that says so is the point: a binary column
    that is *not* encrypted is a perfectly reasonable thing to add, and it must be added by somebody
    who has NOTICED that it is not — rather than by somebody who forgot the marker.
    """
    return _collect(metadata, by_marker=False)


__all__ = ["FERNET_AT_REST", "EncryptedColumn", "binary_columns", "encrypted_columns"]
