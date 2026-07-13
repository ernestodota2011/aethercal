"""The tenant GUC: what the RLS policies actually compare against, and how it stays stamped.

.. rubric:: The setting, and why an empty one means ZERO rows

Every policy in migration ``0008`` reads::

    tenant_id = nullif(current_setting('aethercal.tenant_id', true), '')::uuid

``current_setting(..., true)`` returns ``NULL`` when the setting was never made (without the second
argument it would *raise*, which sounds safer and is not: it would turn every unbound query into a
500 rather than into nothing). ``tenant_id = NULL`` is ``NULL``, which is not ``TRUE``, so the row
is not visible. ==An unbound session therefore sees zero rows, never all of them.== Fail-closed by
construction — and, because zero rows is silent, the NOISE is provided elsewhere: by the boot
assertion (:mod:`aethercal.server.db.roles`) and by a ``db`` test that proves the app role with no
GUC reads nothing.

.. rubric:: Why a ContextVar and a listener, and not a call at the top of each handler

``SET LOCAL`` is transaction-scoped: it dies at every ``commit()``. Three things in this codebase
outlive a transaction, and each would punch a hole in a hand-stamped belt:

* the request path commits **once** today, but the payments batch introduces a commit *mid-request*
  (persist the intent before the provider call) — the next transaction would carry no GUC;
* ``expire_on_commit=False`` (``db/engine.py``) means a lazy load AFTER the commit opens a **new**
  transaction — no GUC;
* the worker's drain is not a request at all. It is a **loop over items belonging to different
  businesses**, so its scope is ONE ITEM, not one request.

So the binding lives in a ``ContextVar`` and an ``after_begin`` listener stamps every transaction
opened while it is populated. Call sites bind; they never stamp. A future transaction nobody thought
about is covered by construction rather than by everybody remembering.

.. rubric:: Immutable inside a scope

:func:`bind_tenant` RAISES if the scope is already bound to a *different* business. Without that, a
handler could re-stamp the GUC half-way through and read another business's rows under the authority
the first half was granted. The scope is the **request** in the web (a ``ContextVar`` set inside an
asyncio Task does not escape it — asserted in the tests, not assumed) and **one item** in the
worker, which opens and closes it explicitly with :func:`tenant_scope`.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

TENANT_GUC = "aethercal.tenant_id"
"""The PostgreSQL run-time parameter the RLS policies read. Namespaced with a dot, which is what
makes it a *custom* setting PostgreSQL accepts with no server configuration at all."""

_SET_TENANT_GUC = text(f"SELECT set_config('{TENANT_GUC}', :tenant_id, true)")
"""``set_config(..., is_local => true)`` IS ``SET LOCAL`` — but it takes a bound parameter, and
``SET LOCAL`` does not. A GUC assembled by string interpolation would be fine for a uuid today and
an injection point the first time that value came from somewhere else."""

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("aethercal_tenant_id", default=None)


class TenantBindingError(RuntimeError):
    """An attempt to re-bind a scope that is already bound to a DIFFERENT business."""


def current_tenant() -> uuid.UUID | None:
    """The business bound to this scope, or ``None`` — and ``None`` means the GUC is empty."""
    return _current_tenant.get()


def reset_tenant_binding() -> None:
    """Clear the binding. For test harnesses, and for a worker item's teardown."""
    _current_tenant.set(None)


async def bind_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Bind this scope to ``tenant_id`` and stamp the GUC on ``session``'s OPEN transaction.

    Both halves are needed, and they are not the same half:

    * the ``ContextVar`` is what makes every *future* transaction of this scope carry the GUC (via
      the listener) — the mid-request commit, the SAVEPOINT, the post-commit lazy load;
    * the immediate ``set_config`` is what makes the transaction that is **already open** carry it.
      By the time a resolver has run (``SECURITY DEFINER``, so it works with no GUC at all), the
      request's transaction has begun and ``after_begin`` has already fired on an empty binding.

    Raises :class:`TenantBindingError` when the scope is already bound to another business.
    """
    bound = _current_tenant.get()
    if bound is not None and bound != tenant_id:
        raise TenantBindingError(
            f"this scope is already bound to business {bound}; refusing to re-bind it to "
            f"{tenant_id}.\n"
            "\n"
            "A scope — one HTTP request in the web, one item in the worker — resolves ONE "
            "business. "
            "Re-stamping the GUC half-way through would let the rest of a handler read another "
            "business's rows under the authority the first half was granted.\n"
            "\n"
            "If you genuinely need to act for another business (the worker's drain loop does, on "
            "every item), open a NEW scope with `tenant_scope(...)`."
        )
    if bound is None:
        _current_tenant.set(tenant_id)

    if session.get_bind().dialect.name == "postgresql":
        await session.execute(_SET_TENANT_GUC, {"tenant_id": str(tenant_id)})


@contextmanager
def tenant_scope(tenant_id: uuid.UUID) -> Generator[None]:
    """Open a fresh binding scope for ``tenant_id``, and close it on the way out.

    ==This is the worker's unit of isolation: ONE ITEM.== A drain batch legitimately spans several
    businesses, so its loop cannot be one scope — it must be one scope per item, opened and reset
    around each. The reset runs even when the effect raises: a failed item must not leave its
    business bound for the next one.
    """
    token = _current_tenant.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def request_scope() -> Generator[None]:
    """Open an EMPTY binding scope and tear it down on the way out. ==The web's unit: ONE REQUEST.==

    The binding starts at ``None`` — nothing bound, therefore nothing visible — and the auth
    dependency (or the guest-token path) is what fills it. On the way out it is reset, whatever
    happened in between.

    .. rubric:: Why this is EXPLICIT, and not left to the task boundary

    A ``ContextVar`` set inside an asyncio Task does not escape it, and under uvicorn every
    request IS its own Task — so in production the request boundary and the ContextVar boundary
    coincide, and it is tempting to lean on that and write nothing at all.

    ==Leaning on it is exactly the kind of implicit assumption this batch exists to destroy.== It is
    not true everywhere. An ASGI app called directly runs *in the caller's task* — which is what
    ``httpx.ASGITransport`` does, and therefore what this product's entire test suite does. There, a
    binding survives from one request into the next, and a second request for a different business
    hits the immutability rule and RAISES.

    That is the FRIENDLY failure. The unfriendly one is the same bug in the shape where it does NOT
    raise: a request that never binds at all, silently inheriting the previous request's business
    and reading rows it has no authority over. An ASGI middleware, a background task, or simply the
    next framework's idea of task boundaries is all it would take.

    So the scope is declared HERE, in the one seam every request already passes through
    (:func:`~aethercal.server.deps.get_session`), and it depends on nobody's task semantics.
    """
    token = _current_tenant.set(None)
    try:
        yield
    finally:
        _current_tenant.reset(token)


def _stamp_tenant_guc(
    session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """``after_begin``: stamp the bound business onto the transaction that has just opened.

    A no-op when nothing is bound — which is exactly right, and is the whole fail-closed story. An
    unbound transaction gets no GUC, the policies then compare against ``NULL``, and it reads
    **nothing**. It does not read everything.

    Also a no-op on a non-PostgreSQL dialect: ``set_config`` does not exist in SQLite, and the
    offline harness — which builds its schema straight from ``Base.metadata`` and has no RLS at all
    — is the only thing that runs there.
    """
    if connection.dialect.name != "postgresql":
        return
    tenant_id = _current_tenant.get()
    if tenant_id is None:
        return
    connection.execute(_SET_TENANT_GUC, {"tenant_id": str(tenant_id)})


class TenantGucSession(Session):
    """The ``Session`` class every AetherCal sessionmaker builds. It carries the GUC listener.

    A subclass, rather than a listener on ``Session`` itself: registering globally would reach every
    session in the process — Alembic's included — which is a wide blast radius for a hook that runs
    SQL on each transaction. Registering on the class the factory builds makes the belt an attribute
    of the sessions this product opens, and of nothing else.
    """


event.listen(TenantGucSession, "after_begin", _stamp_tenant_guc)


__all__ = [
    "TENANT_GUC",
    "TenantBindingError",
    "TenantGucSession",
    "bind_tenant",
    "current_tenant",
    "reset_tenant_binding",
    "tenant_scope",
]
