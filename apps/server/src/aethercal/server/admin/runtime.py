"""The admin runtime holder — and the ONLY two doors to an admin database session (F1-11, B-01).

Reflex state handlers are instance methods with no place to inject dependencies, so the admin's
same-process ``async_sessionmaker`` and its :class:`AdminConfig` are stashed in a process-global
holder that :func:`build_admin_app` configures once at build time and the state reads at request
time. It is a single-process, single-operator admin, so one configured runtime is exactly right;
the holder is a small class attribute (not a ``global`` statement) so it stays lint-clean.

.. rubric:: Why the raw factory is PRIVATE now (B-01)

The admin is not FastAPI, and that is the whole of it: ==a Reflex handler runs in the task context
of its own event loop, where the ``ContextVar`` an HTTP dependency populates does not exist.== Under
RLS a transaction opened straight from the factory carries an EMPTY ``aethercal.tenant_id``,
the policies compare against ``NULL``, and the panel reads **zero rows** — with no exception, no log
line and no clue. The business's own admin simply comes up blank, which is precisely the silent
failure this wave exists to cure.

The cure could not be "every one of the 28 service functions remembers to bind the business":
the 29th is the one that ships. So the factory is not on offer any more. There are exactly two ways
to reach a session, both here, and one of them binds the business for you:

* :meth:`AdminRuntime.admin_session` — resolves the business, BINDS it, and yields the session with
  its :class:`~aethercal.server.admin.service.AdminContext`. Every panel goes through it.
* :meth:`AdminRuntime.bootstrap_session` — the one, named, deliberate accessor with NO business
  bound. It exists for the operator's business SELECTOR (and, in B-02, the member login), which by
  definition runs before there is a business to bind.

``tests/test_admin_session_belt.py`` asserts that on the tree: the private field is touched nowhere
else in the package, ``bootstrap_session`` is the only unbound accessor, and no panel calls it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.service import AdminContext, resolve_admin_context
from aethercal.server.db.guc import bind_tenant, tenant_scope


class AdminRuntime:
    """Everything the admin state needs to serve a request: a way to a session + the config.

    Not a dataclass any more, and that IS the change: a dataclass field is public by construction,
    and the field in question is the one object in the admin that can open a database session with
    no business bound to it.
    """

    __slots__ = ("_sessionmaker", "config")

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        config: AdminConfig,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.config = config

    @asynccontextmanager
    async def admin_session(
        self, tenant_slug: str | None
    ) -> AsyncGenerator[tuple[AsyncSession, AdminContext]]:
        """Open a transaction BOUND to the business ``tenant_slug`` names, and yield it with its
        context. ==This is the admin's only way in, and the belt is the accessor, not the caller.==

        The four steps are in this order because each one needs the one before it:

        #. open a session and ``begin()`` — a transaction is needed FIRST, because ``SET LOCAL`` is
           transaction-scoped and there is nothing to stamp without one. Read-only panels get one
           too: committing a read is inert, and going without would leave the GUC nowhere to live,
           which is the empty panel all over again;
        #. resolve the business — by slug, or, when no slug is configured, THE single one.
           ==Slugless is the mode every live instance runs in today==
           (``AETHERCAL_ADMIN_TENANT_SLUG`` is optional), so an accessor that DEMANDED a slug would
           leave the first LIVE admin with no door at all;
        #. :func:`~aethercal.server.db.guc.bind_tenant` — the ``after_begin`` listener has ALREADY
           fired on this transaction, on an empty binding, because the business was not known yet.
           So the business is stamped onto the transaction that is already open, which is exactly
           what ``bind_tenant`` exists to do;
        #. yield.

        .. rubric:: Why the scope is the BLOCK

        :func:`~aethercal.server.db.guc.tenant_scope` opens the binding and closes it on the way
        out, so no business stays bound to the task once the block ends. In the web the scope is the
        request and in the worker it is one item; here it is one unit of admin work — and it has to
        be, for two reasons that are really the same one. A binding that OUTLIVED its block would
        (a) turn the next block for a DIFFERENT business — the operator switching business, a suite
        covering two of them — into a hard :class:`~aethercal.server.db.guc.TenantBindingError`, and
        (b) silently stamp any session opened afterwards in the same task with a business nobody
        asked for. Closing the scope leaves the task fail-CLOSED instead: an unbound session reads
        nothing, which is the only safe thing it can do.

        The resolver itself runs UNBOUND, deliberately and safely: ``tenants`` carries no RLS policy
        (the slugs are semi-public, and a policy there would lock the admin out of its own boot), so
        resolving one needs no ``SECURITY DEFINER`` escape hatch — a plain ``SELECT`` does it.

        Raises :class:`~aethercal.server.admin.service.AdminSetupError` when the slug names no
        business, or when there is no single one to choose. ==It raises rather than yielding an
        unbound session:== unbound, every panel would render empty, and the operator would be told
        their business has nothing in it rather than that the admin is misconfigured.
        """
        async with self._sessionmaker() as session, session.begin():
            ctx = await resolve_admin_context(session, tenant_slug=tenant_slug)
            with tenant_scope(ctx.tenant_id):
                await bind_tenant(session, ctx.tenant_id)
                yield session, ctx

    @asynccontextmanager
    async def bootstrap_session(self) -> AsyncGenerator[AsyncSession]:
        """A transaction with NO business bound — ==the single, named exception, and nothing else.==

        Something has to run before a business is chosen: the operator's business SELECTOR must LIST
        the businesses in order to offer them, and (B-02) a member's login must find the business
        their address belongs to. Neither can run inside a binding, because the binding is the
        answer they are looking for.

        It is declared, named, and alone on purpose. "The unbelted session is not available any
        more" is an honest sentence only if the exception is a door with a sign on it rather than a
        habit — so there is one, ``test_admin_session_belt`` asserts it is the only accessor that
        skips :func:`~aethercal.server.db.guc.bind_tenant`, and it asserts that nobody calls it.

        ==Everything read through it must be RLS-exempt by design (``tenants``), or it reads
        nothing.== That is not a limitation to work around: a session with no business bound sees no
        tenant-scoped row at all, and a caller who "just needs one more table" from here is a caller
        who wanted :meth:`admin_session`.
        """
        async with self._sessionmaker() as session, session.begin():
            yield session


class _Holder:
    """A tiny mutable box for the process-global runtime (avoids a ``global`` statement)."""

    value: AdminRuntime | None = None


def configure_runtime(runtime: AdminRuntime) -> None:
    """Install the process-global admin runtime (called once by :func:`build_admin_app`)."""
    _Holder.value = runtime


def current_runtime() -> AdminRuntime:
    """Return the configured runtime, or raise if the admin was not built before use."""
    if _Holder.value is None:
        raise RuntimeError("admin runtime is not configured; build the admin app first")
    return _Holder.value


__all__ = ["AdminRuntime", "configure_runtime", "current_runtime"]
