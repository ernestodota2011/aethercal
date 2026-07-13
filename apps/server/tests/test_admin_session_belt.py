"""The admin's session belt: no admin session can be opened without a business bound (B-01).

.. rubric:: The failure this locks out

The admin is Reflex, and a Reflex handler is not an HTTP request: it runs in its own event-loop
task, where the ``ContextVar`` a FastAPI dependency populates **does not exist**. So under RLS an
admin
transaction opened from the raw ``async_sessionmaker`` carries an EMPTY ``aethercal.tenant_id``, the
policies compare against ``NULL``, and every panel reads **zero rows** — with no error, no exception
and no log line. The business's own admin simply comes up blank. That is the exact disease this wave
cures, walked back in through the one door nobody guarded.

.. rubric:: Why the lock is structural and not a rule in a docstring

The cure is not "remember to bind the tenant in all 28 service functions". It is that
:class:`~aethercal.server.admin.runtime.AdminRuntime` NO LONGER HANDS OUT the raw factory: the
only ways to reach a session are
:meth:`~aethercal.server.admin.runtime.AdminRuntime.admin_session` (which resolves the business and
binds it before it yields) and
:meth:`~aethercal.server.admin.runtime.AdminRuntime.bootstrap_session` (the one, named, deliberate
accessor with NO business bound — the operator's business selector, and in B-02 the member login).

So site 29 cannot open an unbelted session by forgetting to: the object it would need is private.
These tests assert that fact about the TREE — the same lock ``test_users_service`` puts on "nothing
outside the service constructs a ``User``" and ``test_workflow_rules_service`` puts on "nothing
outside the service re-projects a rule". A rule that is only written down is a rule that is
eventually skipped, and skipping THIS one produces no error whatsoever: only an empty panel.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.admin import runtime as runtime_module
from aethercal.server.admin import service as service_module
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.admin.service import AdminSetupError
from aethercal.server.db import Base
from aethercal.server.db.guc import current_tenant, reset_tenant_binding
from aethercal.server.db.models import Tenant

Sessionmaker = async_sessionmaker[AsyncSession]

# --------------------------------------------------------------------------------------
# What the structural assertions are ABOUT (named, so a future reader moves the goalposts
# deliberately rather than by accident).
# --------------------------------------------------------------------------------------

#: The private name the raw session factory hides behind on the runtime. Touching it outside
#: ``runtime.py`` is exactly the move this batch removes: it is a session with no belt.
_RAW_FACTORY = "_sessionmaker"

#: The accessors ``AdminRuntime`` offers. Anything else that hands out a session is a new door.
_ACCESSORS = frozenset({"admin_session", "bootstrap_session"})

#: ==The ONLY accessor allowed to open a session with NO business bound.== One, named, and asserted
#: to be alone — which is what makes "the unbelted session is not available any more" an honest
#: sentence rather than a hopeful one.
_UNBOUND_ACCESSORS = frozenset({"bootstrap_session"})

#: The admin modules that may CALL the unbound accessor, by module stem. Empty today, and that is
#: the point: the operator's business selector (and B-02's member login) is the reason it exists,
#: and it does not exist yet. A module added here is a decision somebody has to make in a diff.
_BOOTSTRAP_CALLERS: frozenset[str] = frozenset()

#: The parameter a session-owning admin service function takes. The two context resolvers (which run
#: INSIDE a session somebody else owns) take ``session`` instead.
_ACCESSOR_PARAM = "admin"

_ADMIN_ROOT = Path(runtime_module.__file__).resolve().parent
_RUNTIME_MODULE = Path(runtime_module.__file__).resolve()
_SERVICE_MODULE = Path(service_module.__file__).resolve()


def _admin_modules() -> list[Path]:
    """Every module of the admin package."""
    return sorted(path.resolve() for path in _ADMIN_ROOT.rglob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_def(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined at module level")


def _public_methods(klass: ast.ClassDef) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {
        node.name: node
        for node in klass.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and not node.name.startswith("_")
    }


def _called_names(node: ast.AST) -> set[str]:
    """Every plain-name call inside ``node`` (``bind_tenant(...)`` → ``{"bind_tenant"}``)."""
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def _touches(path: Path, attribute: str) -> bool:
    """Whether ``path`` reads ``<something>.<attribute>`` anywhere. ==On the TREE, not the text==:
    every docstring in this batch names ``_sessionmaker`` and ``bootstrap_session`` on purpose, and
    a grep would count the explanation as the offence."""
    return any(
        isinstance(node, ast.Attribute) and node.attr == attribute for node in ast.walk(_tree(path))
    )


# --------------------------------------------------------------------------------------
# The lock: the unbelted session is not reachable.
# --------------------------------------------------------------------------------------


def test_nothing_in_the_admin_reaches_for_the_raw_sessionmaker() -> None:
    """==The lock on the cause, not on its symptoms.==

    Auditing the 28 call sites once buys nothing if the 29th opens ``maker()`` of its own: it would
    type-check, it would run, it would commit — and under RLS it would read nothing at all, in
    silence. So the fact is asserted about the TREE: inside the admin package, the raw factory is
    touched in ``runtime.py`` and NOWHERE else.
    """
    offenders = sorted(
        str(path.relative_to(_ADMIN_ROOT))
        for path in _admin_modules()
        if path != _RUNTIME_MODULE and _touches(path, _RAW_FACTORY)
    )
    assert offenders == []


def test_the_runtime_hands_out_no_public_session_factory() -> None:
    """The attribute the 28 sites used to read (``runtime.sessionmaker``) is GONE, not deprecated.

    A private field plus a public alias would leave the door open and hang a sign on it.
    """
    assert not hasattr(AdminRuntime, "sessionmaker")
    assert not hasattr(_runtime_over(_maker_stub()), "sessionmaker")


def test_the_runtime_offers_exactly_two_session_accessors() -> None:
    """Every way to reach an admin session is one of these two, and both are pinned below: one binds
    the business, the other is the named exception. A third would be an unaudited door."""
    accessors = set(_public_methods(_class_def(_tree(_RUNTIME_MODULE), "AdminRuntime")))
    assert accessors == set(_ACCESSORS)


def test_bootstrap_session_is_the_only_accessor_without_a_business_bound() -> None:
    """==The sentence "the unbelted session is not available any more", made checkable.==

    There is exactly ONE unbound accessor, it is named, and every OTHER accessor binds the business
    before it yields — asserted on the source, so an accessor added later that forgets to call
    ``bind_tenant`` fails here instead of quietly serving an empty panel.
    """
    assert _UNBOUND_ACCESSORS < _ACCESSORS
    assert len(_UNBOUND_ACCESSORS) == 1

    methods = _public_methods(_class_def(_tree(_RUNTIME_MODULE), "AdminRuntime"))
    unbelted = sorted(
        name
        for name, node in methods.items()
        if name not in _UNBOUND_ACCESSORS and "bind_tenant" not in _called_names(node)
    )
    assert unbelted == []


def test_only_the_business_selector_may_open_an_unbound_session() -> None:
    """The unbound accessor exists for ONE caller (the operator's business selector; B-02's member
    login). Nobody else may call it — least of all a panel that just wants some rows."""
    offenders = sorted(
        str(path.relative_to(_ADMIN_ROOT))
        for path in _admin_modules()
        if path != _RUNTIME_MODULE
        and path.stem not in _BOOTSTRAP_CALLERS
        and _touches(path, "bootstrap_session")
    )
    assert offenders == []


def test_every_admin_service_entry_point_takes_the_accessor() -> None:
    """The service layer cannot be handed a raw factory, because it does not take one any more.

    Its session-owning functions take the ACCESSOR (``admin``); the two context resolvers take the
    ``session`` they are called inside. Nothing takes an ``async_sessionmaker``, so "open a session
    and forget the belt" is not expressible in this layer's signatures.
    """
    offenders: list[str] = []
    for node in _tree(_SERVICE_MODULE).body:
        if not isinstance(node, ast.AsyncFunctionDef) or node.name.startswith("_"):
            continue
        first = node.args.args[0].arg if node.args.args else "<none>"
        if first not in {_ACCESSOR_PARAM, "session"}:
            offenders.append(f"{node.name}({first}, ...)")
    assert offenders == []


# --------------------------------------------------------------------------------------
# The behaviour the lock protects.
# --------------------------------------------------------------------------------------


def _maker_stub() -> Sessionmaker:
    """A factory over an engine nobody connects to — enough to build a runtime for a shape test."""
    return async_sessionmaker(create_async_engine("sqlite+aiosqlite://"))


def _runtime_over(maker: Sessionmaker, *, tenant_slug: str | None = None) -> AdminRuntime:
    """The runtime the panel's handlers call through, over an offline sessionmaker.

    The config is the operator's login, which no session accessor reads — it is here because the
    Reflex state reads ``runtime.config.tenant_slug`` to decide WHICH business it administers.
    """
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="admin", password_hash="x", tenant_slug=tenant_slug),
    )


@pytest.fixture(autouse=True)
def _clean_binding() -> Iterator[None]:
    """No test inherits (or leaks) a bound business — the binding is the thing under test."""
    reset_tenant_binding()
    yield
    reset_tenant_binding()


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
    """An in-memory aiosqlite sessionmaker with the full schema (offline admin TDD).

    SQLite has no RLS and no ``set_config``, so this harness proves the BINDING (the ``ContextVar``
    every AetherCal sessionmaker's listener stamps its transactions from), not the policy. The
    policy is proven against a real PostgreSQL in the ``db`` suite — two halves of the same belt.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_tenant(maker: Sessionmaker, *, slug: str) -> uuid.UUID:
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
        await session.flush()
        return tenant.id


async def test_the_accessor_binds_the_business_for_the_length_of_the_block(
    sessionmaker: Sessionmaker,
) -> None:
    """Inside the block the business is BOUND — which is what makes the transaction's rows visible
    under RLS — and outside it, nothing is."""
    tenant_id = await _seed_tenant(sessionmaker, slug="acme")
    admin = _runtime_over(sessionmaker)

    assert current_tenant() is None
    async with admin.admin_session("acme") as (session, ctx):
        assert ctx.tenant_id == tenant_id
        assert current_tenant() == tenant_id
        assert list((await session.scalars(select(Tenant.slug))).all()) == ["acme"]
    assert current_tenant() is None


async def test_a_slugless_admin_resolves_the_single_business(sessionmaker: Sessionmaker) -> None:
    """==The mode every live instance runs in today.== ``AETHERCAL_ADMIN_TENANT_SLUG`` is optional,
    and unset it means "the one business there is". An accessor that DEMANDED a slug would leave the
    first LIVE admin with no door at all."""
    tenant_id = await _seed_tenant(sessionmaker, slug="acme")
    admin = _runtime_over(sessionmaker)

    async with admin.admin_session(None) as (_, ctx):
        assert ctx.tenant_id == tenant_id
        assert current_tenant() == tenant_id


async def test_an_unknown_slug_never_opens_an_unbound_session(sessionmaker: Sessionmaker) -> None:
    """A slug that resolves to nothing RAISES. It must never fall through into a bound-to-nobody
    session, which under RLS reads zero rows and looks exactly like an empty business."""
    await _seed_tenant(sessionmaker, slug="acme")
    admin = _runtime_over(sessionmaker)

    with pytest.raises(AdminSetupError, match="ghost"):
        async with admin.admin_session("ghost"):
            pass  # pragma: no cover - the resolver raises before the block is entered
    assert current_tenant() is None


async def test_administering_two_businesses_in_turn_is_two_scopes(
    sessionmaker: Sessionmaker,
) -> None:
    """Each block is its OWN binding scope, opened and closed around one business.

    Not a nicety: a binding is immutable WITHIN a scope (re-stamping the GUC half-way through is how
    a handler comes to read another business's rows under the authority the first half was granted),
    so a scope that OUTLIVED its block would turn the very next block — the operator switching
    business, a suite covering two of them — into a hard ``TenantBindingError``.
    """
    acme = await _seed_tenant(sessionmaker, slug="acme")
    other = await _seed_tenant(sessionmaker, slug="other")
    admin = _runtime_over(sessionmaker)

    async with admin.admin_session("acme") as (_, first):
        assert first.tenant_id == acme
    async with admin.admin_session("other") as (_, second):
        assert second.tenant_id == other
        assert current_tenant() == other
    assert current_tenant() is None


async def test_bootstrap_session_opens_with_no_business_bound(sessionmaker: Sessionmaker) -> None:
    """The one accessor ALLOWED to be unbound — and it must really be unbound, or the business
    selector would list the businesses of whoever happened to be bound last."""
    await _seed_tenant(sessionmaker, slug="acme")
    await _seed_tenant(sessionmaker, slug="other")
    admin = _runtime_over(sessionmaker)

    async with admin.bootstrap_session() as session:
        assert current_tenant() is None
        assert sorted((await session.scalars(select(Tenant.slug))).all()) == ["acme", "other"]
    assert current_tenant() is None
