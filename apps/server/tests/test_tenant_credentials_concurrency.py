"""Two concurrent first-time writes of the same credential must leave ONE row (PostgreSQL only).

``db``-marked, and for the same reason as ``test_key_rotation_concurrency.py``: the guarantee lives
in server-side concurrency control, which SQLite cannot exercise — it serialises writers anyway.

.. rubric:: The check-then-insert this closes

``store_credential`` reads the business's row first (``_row_for``) and INSERTs only when it finds
none. That read and that write are not one act. Two ``store_credential`` calls for the same
``(tenant_id, provider)`` — two admin tabs, a retried request — can interleave so that BOTH read "no
row yet" and BOTH take the INSERT path. The ``UNIQUE(tenant_id, provider)`` constraint then refuses
the second, and on a payment credential "it looked like it saved and then threw ``IntegrityError``"
is exactly the ambiguity this module must not have.

==The hold is what makes this a proof.== Without it the two coroutines interleave by luck: the first
can INSERT and COMMIT before the second has read, and then the second simply sees the row and takes
the UPDATE path — the test goes green having never exercised the race. The event + the hold pin the
first writer's row into the database UNCOMMITTED, so the second writer's read genuinely misses it
and its INSERT genuinely contends for the unique key — the window the bug lives in, made
deterministic instead of left to scheduling luck.

.. rubric:: Every secret here is synthetic

``sk_test_NOT_A_REAL_KEY_a`` is not a redaction of anything.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.crypto import decrypt_secret, derive_fernet_key
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import Tenant, TenantCredential
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential

pytestmark = pytest.mark.db

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("the-instance-app-secret")

STRIPE_A = {"secret_key": "sk_test_NOT_A_REAL_KEY_a", "webhook_secret": "whsec_FAKE_a"}
STRIPE_B = {"secret_key": "sk_test_NOT_A_REAL_KEY_b", "webhook_secret": "whsec_FAKE_b"}

# How long the first writer holds its uncommitted row. It only has to comfortably exceed the time
# the second writer needs to wake, bind, read (a miss) and reach its blocking INSERT — a couple of
# localhost round trips — so the contention is real rather than a scheduling accident.
_HOLD_SECONDS = 0.5


async def test_two_concurrent_first_writes_leave_one_row_and_raise_no_integrity_error(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
    tenant_factory: TenantFactory,
) -> None:
    """==The TOCTOU, reproduced and closed.== Two writers, one ``(tenant, provider)``, no row yet.

    Both read an empty table and both go to INSERT; the database lets exactly one land. Neither
    caller may see a raw ``IntegrityError`` — the loser must absorb the conflict and settle into an
    UPDATE — and the business must be left with ONE credential, the value of the writer that
    committed last (last-write-wins).
    """
    async with owner_maker() as session, session.begin():
        tenant = await tenant_factory(session)
        tenant_id = tenant.id

    first_row_pending = asyncio.Event()

    async def writer_one() -> None:
        """Store STRIPE_A, then HOLD it in the database uncommitted so the second writer's read
        misses it and its INSERT has to contend for the unique key."""
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            await store_credential(
                session,
                tenant_id=tenant_id,
                provider=CredentialProvider.STRIPE,
                secrets=STRIPE_A,
                fernet_key=KEY,
            )
            # Force the INSERT to the database, still uncommitted: this is the concurrent
            # transaction's real mid-flight state, and what makes the race below deterministic.
            await session.flush()
            first_row_pending.set()
            await asyncio.sleep(_HOLD_SECONDS)
            await session.commit()

    async def writer_two() -> None:
        """Store STRIPE_B while the first writer's row is uncommitted — its read misses, its INSERT
        contends, and it must resolve to an UPDATE rather than raising at the caller."""
        await first_row_pending.wait()
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            await store_credential(
                session,
                tenant_id=tenant_id,
                provider=CredentialProvider.STRIPE,
                secrets=STRIPE_B,
                fernet_key=KEY,
            )
            await session.commit()

    results = await asyncio.gather(writer_one(), writer_two(), return_exceptions=True)

    # ==The loser absorbs the conflict; it does not hand the caller a database traceback.== On the
    # check-then-insert this is where the second writer's INSERT surfaces as a raw duplicate-key
    # `IntegrityError` — a database's sentence, not a person's, for an ordinary re-save.
    assert not any(isinstance(outcome, IntegrityError) for outcome in results), (
        f"a concurrent store_credential leaked a raw IntegrityError to the caller: {results!r}"
    )
    assert not any(isinstance(outcome, BaseException) for outcome in results), (
        f"a concurrent store_credential raised: {results!r}"
    )

    # One row — the fact that actually matters — observed on the OWNER engine, which bypasses RLS so
    # a second row could not be hidden by a policy. Its value is the writer that committed last:
    # writer_two's INSERT only unblocks once writer_one commits, so writer_two settles on top.
    async with owner_maker() as session:
        rows = (await session.scalars(sa.select(TenantCredential))).all()
    assert len(rows) == 1, f"a concurrent pair left {len(rows)} credential rows, not one"
    surviving = json.loads(decrypt_secret(rows[0].encrypted_payload, KEY).decode("utf-8"))
    assert surviving == STRIPE_B, "last-write-wins: the credential committed last must survive"
