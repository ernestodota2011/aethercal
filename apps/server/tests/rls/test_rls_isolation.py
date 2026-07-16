"""The isolation belt, against a real PostgreSQL. ==Every assertion is about EFFECTIVE STATE.==

Not "the code calls ``bind_tenant``". Not "the policy string looks right". The role the connection
actually holds (``SELECT current_user``), the flag the table actually carries
(``pg_class.relforcerowsecurity``), the rows a query actually returns. Under row-level security
almost nothing raises — a mis-wired belt returns **zero rows** and logs nothing — so a test
asserting
on *behaviour that looks right* would pass against a system enforcing nothing at all.

These map onto the batch's acceptance criteria; each class names its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db import Base
from aethercal.server.db.guc import (
    TenantBindingError,
    bind_tenant,
    reset_tenant_binding,
    tenant_scope,
)
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.db.pools import BypassReason, BypassRequiredError, WorkerPools
from aethercal.server.db.rls import tenant_scoped_tables, unscoped_tables
from aethercal.server.db.roles import DbRole
from aethercal.server.observability import collect_metrics
from aethercal.server.services.api_keys import parse_key
from aethercal.server.services.tenant_resolution import tenant_by_api_key_prefix, tenant_by_slug

pytestmark = pytest.mark.db

_CURRENT_USER = sa.text("SELECT current_user")


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


async def _current_user(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        return str((await conn.execute(_CURRENT_USER)).scalar_one())


async def _seed_booking(
    owner_maker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> uuid.UUID:
    """One bookable row for a business, written on the OWNER engine — arrangement, not behaviour."""
    async with owner_maker() as session, session.begin():
        user = (await session.scalars(sa.select(User).where(User.tenant_id == tenant_id))).one()
        schedule = Schedule(
            tenant_id=tenant_id, user_id=user.id, name="Default", timezone="UTC", rules={}
        )
        session.add(schedule)
        await session.flush()
        event_type = EventType(
            tenant_id=tenant_id,
            host_id=user.id,
            schedule_id=schedule.id,
            slug="discovery-call",
            title="Discovery",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 60,
        )
        session.add(event_type)
        await session.flush()
        start = datetime.now(UTC) + timedelta(days=1)
        booking = Booking(
            tenant_id=tenant_id,
            event_type_id=event_type.id,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=BookingStatus.CONFIRMED,
            # Confirmed ⇒ stamped: after B-05a a confirmed booking always carries when it became so.
            confirmed_at=start - timedelta(days=1),
            guest_name="Guest",
            guest_email="guest@example.com",
            guest_timezone="UTC",
            ical_uid=f"{uuid.uuid4()}@aethercal.test",
        )
        session.add(booking)
        await session.flush()
        return booking.id


# ==========================================================================================
# Criterion 1 — `SELECT current_user`, per sessionmaker. The EFFECTIVE state, not the apparent.
# ==========================================================================================


class TestCriterion1EachEngineHoldsItsOwnRole:
    async def test_the_app_engine_is_aethercal_app(self, app_engine: AsyncEngine) -> None:
        assert await _current_user(app_engine) == DbRole.APP.value

    async def test_the_owner_engine_is_aethercal_owner(self, owner_engine: AsyncEngine) -> None:
        assert await _current_user(owner_engine) == DbRole.OWNER.value

    async def test_the_scan_pool_is_the_worker_and_the_exec_pool_is_the_app(
        self, worker_pools: WorkerPools
    ) -> None:
        """==Criterion 13d.== The worker's EXECUTION pool must be the app role, under RLS.

        Swap the two and nothing complains: the scan pool on the app role finds no work (silently),
        and the exec pool on the worker role runs every effect with ``BYPASSRLS`` — the
        cross-business
        leak this batch exists to make impossible, in the one process where it would be externally
        visible.
        """
        async with worker_pools.scan_session(BypassReason.PLAN_OUTBOX) as scan:
            assert (await scan.execute(_CURRENT_USER)).scalar_one() == DbRole.WORKER.value

        async with worker_pools.exec_maker() as execute:
            assert (await execute.execute(_CURRENT_USER)).scalar_one() == DbRole.APP.value


# ==========================================================================================
# Criterion 2 — no engine in the WEB process carries BYPASSRLS.
# ==========================================================================================


class TestCriterion2TheWebHoldsNoBypass:
    async def test_the_app_role_does_not_have_bypassrls(self, app_engine: AsyncEngine) -> None:
        """The web builds exactly ONE engine, and it is this role. So ask the SERVER about it.

        ``rolbypassrls`` is an attribute of the role, which is what makes this the whole of
        criterion
        2: whatever the web process does with its engine, it cannot bypass a policy, because the
        identity it connects with is not permitted to.
        """
        async with app_engine.connect() as conn:
            bypasses = await conn.scalar(
                sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        assert bypasses is False


# ==========================================================================================
# Criteria 5, 6, 7 — the belt: the owner reads across, the app with no GUC reads NOTHING, and a
# write carrying somebody else's id is DENIED.
# ==========================================================================================


class TestCriterion5TheOwnerStillReadsAcrossBusinesses:
    async def test_bypassrls_beats_force_row_level_security(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==Proved, not assumed.== ``guest purge`` hangs from this exact fact.

        The owner OWNS the tables and ``FORCE ROW LEVEL SECURITY`` is on, which makes an owner
        subject
        to its own policies. The only reason erasure still works is the ``BYPASSRLS`` attribute —
        and
        if that assumption were wrong, ``guest purge --tenant X`` would match zero rows and exit 0:
        erasure of a real person's data, reporting success, having erased nothing.
        """
        for tenant_id, _ in two_businesses:
            await _seed_booking(owner_maker, tenant_id)

        async with owner_maker() as session:
            total = await session.scalar(sa.select(sa.func.count()).select_from(Booking))
        assert total == 2, "the owner must see BOTH businesses' bookings, under FORCE"


class TestCriterion6TheAppWithNoGucReadsZero:
    async def test_no_guc_means_zero_rows_never_all_rows(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==Fail-closed, not fail-open.== The single most important assertion in this file.

        ``current_setting('aethercal.tenant_id', true)`` is NULL when nothing was bound, and
        ``tenant_id = NULL`` is NULL, which is not TRUE — so an unbound session sees NOTHING.
        Written
        the other way round, with a permissive fallback, every unbound query in the product would
        have read EVERY business instead.
        """
        for tenant_id, _ in two_businesses:
            await _seed_booking(owner_maker, tenant_id)

        async with app_maker() as session:
            visible = await session.scalar(sa.select(sa.func.count()).select_from(Booking))
        assert visible == 0


class TestCriterion7CrossBusinessReadsAndWrites:
    async def test_business_b_cannot_see_business_a(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_booking(owner_maker, tenant_a)

        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_b)
            visible = await session.scalar(sa.select(sa.func.count()).select_from(Booking))
        assert visible == 0, "B, bound to itself, must not see A's booking"

    async def test_business_b_sees_exactly_its_own(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_booking(owner_maker, tenant_a)
        await _seed_booking(owner_maker, tenant_b)

        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_b)
            rows = list((await session.scalars(sa.select(Booking))).all())
        assert [row.tenant_id for row in rows] == [tenant_b]

    async def test_a_write_carrying_another_businesss_id_is_denied(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==``WITH CHECK``.== Reading is only half a belt.

        Without it, a service holding a stale ``tenant_id`` could still INSERT a row into another
        business — a row it can then never see itself. That is worse than a leak, because it is
        invisible from both sides.
        """
        (tenant_a, _), (tenant_b, _) = two_businesses

        with pytest.raises(sa.exc.ProgrammingError):  # new row violates row-level security policy
            async with app_maker() as session, session.begin():
                await bind_tenant(session, tenant_b)
                session.add(
                    User(
                        tenant_id=tenant_a,  # NOT the bound business
                        email="intruder@example.com",
                        name="Intruder",
                        timezone="UTC",
                    )
                )
                await session.flush()


# ==========================================================================================
# Criteria 8, 9 — the GUC's lifecycle: immutable in a scope, and re-stamped after a commit.
# ==========================================================================================


class TestCriterion8BindingIsImmutableWithinAScope:
    async def test_re_binding_to_another_business_raises(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        (tenant_a, _), (tenant_b, _) = two_businesses
        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_a)
            with pytest.raises(TenantBindingError):
                await bind_tenant(session, tenant_b)


class TestCriterion9TheGucSurvivesAMidRequestCommit:
    async def test_the_transaction_after_a_commit_still_carries_the_business(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==This is what the ``after_begin`` listener is FOR.==

        ``SET LOCAL`` dies at every ``commit()``. The payments batch introduces a commit half-way
        through a request (persist the intent before calling the provider), and
        ``expire_on_commit=False`` lets a lazy load open a brand-new transaction after one. Both
        would
        land on a session with an EMPTY GUC — reading zero rows, silently — if the binding were
        stamped once, by hand, instead of being re-stamped on every transaction the scope opens.
        """
        (tenant_a, _), _ = two_businesses
        await _seed_booking(owner_maker, tenant_a)

        async with app_maker() as session:
            await session.begin()
            await bind_tenant(session, tenant_a)
            assert await session.scalar(sa.select(sa.func.count()).select_from(Booking)) == 1
            await session.commit()  # from the server's point of view, the SET LOCAL is now gone

            # A brand-new transaction, opened by the very next statement. The listener re-stamps it.
            assert await session.scalar(sa.select(sa.func.count()).select_from(Booking)) == 1
            await session.commit()


# ==========================================================================================
# The bootstrap paradox — the three SECURITY DEFINER resolvers.
# ==========================================================================================


class TestTheResolversAnswerWithoutAGuc:
    async def test_an_api_key_prefix_resolves_to_its_business_with_no_guc_bound(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==Without this, EVERY authenticated request in the product 401s.==

        ``api_keys`` is itself a scoped table, and a request arrives carrying nothing but the key.
        The
        resolver runs as its owner (``BYPASSRLS``), so it sees the row; it returns a bare uuid, so
        it
        leaks nothing — not the hash, and not the existence of any other business.
        """
        (tenant_a, key_a), _ = two_businesses
        parsed = parse_key(key_a)
        assert parsed is not None
        prefix, _secret = parsed

        async with app_maker() as session:
            resolved = await tenant_by_api_key_prefix(session, prefix)
        assert resolved == tenant_a

    async def test_an_unknown_prefix_resolves_to_nothing(
        self, app_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        async with app_maker() as session:
            assert await tenant_by_api_key_prefix(session, "nope_nope") is None

    async def test_the_slug_resolver_answers_on_the_app_role(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        (tenant_a, _), _ = two_businesses
        async with owner_maker() as session:
            tenant = await session.get(Tenant, tenant_a)
            assert tenant is not None
            slug = tenant.slug

        async with app_maker() as session:
            assert await tenant_by_slug(session, slug) == tenant_a


# ==========================================================================================
# Criterion 12b — collect_metrics FAILS on a session with no bypass. It does not fill with zeros.
# ==========================================================================================


class TestCriterion12bCollectMetricsRefusesWithoutTheBypass:
    async def test_it_raises_on_an_app_session(
        self, app_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==The root fix — and what stops the next ``/health/ready`` from ever being born.==

        Every query in ``collect_metrics`` is cross-business, and the function fills zeros in
        wherever
        it finds nothing. On the app role that combination reports ``outbox.due = 0`` and
        ``status: ready`` — for ever, with the queue on fire. So it REFUSES, rather than degrading.
        """
        async with app_maker() as session:
            with pytest.raises(BypassRequiredError):
                await collect_metrics(session, now=datetime.now(UTC))

    async def test_it_runs_on_the_workers_scan_session(self, worker_pools: WorkerPools) -> None:
        async with worker_pools.scan_session(BypassReason.OPERATOR_METRICS) as session:
            snapshot = await collect_metrics(session, now=datetime.now(UTC))
        assert snapshot.outbox_by_status["pending"] == 0  # empty — but it ANSWERED


# ==========================================================================================
# Criterion 13f — a batch of TWO businesses executes each effect under ITS OWN business.
# ==========================================================================================


class TestCriterion13fAPerItemScopeCrossesBusinessesSafely:
    async def test_each_item_sees_only_its_own_business(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        worker_pools: WorkerPools,
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==The worker's scope is ONE ITEM, and both alternatives are silent disasters.==

        Hold one binding for the whole loop and the batch's second business raises, killing
        everything
        that does not belong to the first. Relax the immutability so the loop compiles, and an item
        of
        B runs under the GUC of A — its reads come back ``None``, the handlers treat that as
        "cascade-deleted, be defensive", and the intent settles ``delivered`` **having sent
        nothing**.

        This drives the real mechanism (``tenant_scope`` over the exec pool) across a real
        two-business batch, and asserts each item saw exactly one booking: its own.
        """
        (tenant_a, _), (tenant_b, _) = two_businesses
        booking_a = await _seed_booking(owner_maker, tenant_a)
        booking_b = await _seed_booking(owner_maker, tenant_b)

        seen: list[tuple[uuid.UUID, list[uuid.UUID]]] = []
        for tenant_id in (tenant_a, tenant_b):
            with tenant_scope(tenant_id):
                async with worker_pools.exec_maker() as session, session.begin():
                    rows = list((await session.scalars(sa.select(Booking.id))).all())
                    seen.append((tenant_id, rows))

        assert seen == [(tenant_a, [booking_a]), (tenant_b, [booking_b])]

    async def test_the_scan_pool_sees_the_whole_batch(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        worker_pools: WorkerPools,
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """And the PLANNING half must see BOTH — otherwise there is no batch to bind item by
        item."""
        for tenant_id, _ in two_businesses:
            await _seed_booking(owner_maker, tenant_id)

        async with worker_pools.scan_session(BypassReason.PLAN_OUTBOX) as session:
            total = await session.scalar(sa.select(sa.func.count()).select_from(Booking))
        assert total == 2


# ==========================================================================================
# Criteria 4, 13c, 13j — derived from the METADATA, so that a new table breaks CI.
# ==========================================================================================


class TestCriterion4EveryScopedTableIsForced:
    async def test_every_table_with_a_tenant_id_has_force_and_a_policy(
        self, owner_engine: AsyncEngine
    ) -> None:
        """==Derived from ``Base.metadata``, never from a list.== A new table with no RLS fails
        HERE.

        A hand-written list of tables is a photograph: correct the day it is written, silently wrong
        the day somebody adds a table — and that table then becomes the one place where every
        business
        can read every other. So the expectation comes from the models, and the answer comes from
        ``pg_class``, on the really-migrated database.
        """
        expected = set(tenant_scoped_tables(Base.metadata))
        assert expected, "the derivation itself must not be empty"

        async with owner_engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "  (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relkind = 'r'"
                    )
                )
            ).all()
        state = {name: (enabled, forced, policies) for name, enabled, forced, policies in rows}

        for table in sorted(expected):
            assert table in state, f"{table} is in the metadata but not in the migrated database"
            enabled, forced, policies = state[table]
            assert enabled, f"{table}: row-level security is not ENABLED"
            assert forced, (
                f"{table}: FORCE ROW LEVEL SECURITY is off. The owner would bypass its own "
                "policy — and the whole belt with it."
            )
            assert policies >= 1, f"{table}: RLS is on and there is NO POLICY — it reads zero rows"

    async def test_the_tenant_root_deliberately_carries_no_policy(
        self, owner_engine: AsyncEngine
    ) -> None:
        """``tenants`` is the ONE exception, and it is a decision, not an oversight.

        The public booking router resolves a business by slug on an unauthenticated route, so the
        slugs are semi-public by design — and protecting the table BREAKS THE ADMIN'S BOOT, which
        reads ``Tenant`` by slug before any GUC can possibly exist.
        """
        async with owner_engine.connect() as conn:
            policies = await conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relname = 'tenants'"
                )
            )
        assert policies == 0


class TestCriterion13jTheUnscopedSetIsExactlyTenants:
    def test_a_new_table_without_a_tenant_id_breaks_ci(self) -> None:
        """Offline, and deliberately so: it forces somebody to DECIDE a new table's regime by hand,
        instead of letting it inherit one nobody chose."""
        assert unscoped_tables(Base.metadata) == ("tenants",)


class TestCriterion13cTheAppRoleCanActuallyUseEveryTable:
    async def test_every_metadata_table_is_readable_and_writable_by_the_app_role(
        self, app_engine: AsyncEngine
    ) -> None:
        """==The mirror of criterion 4 — and the error the agency has already been burnt by.==

        The OWNER creates the tables. With no ``GRANT``, the app role cannot read a single one of
        them, and the failure surfaces as a permission error in production rather than in CI. A new
        table that arrives without a grant fails right here.
        """
        async with app_engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT table_name, "
                        "  string_agg(privilege_type, ',' ORDER BY privilege_type) "
                        "FROM information_schema.table_privileges "
                        "WHERE grantee = current_user AND table_schema = current_schema() "
                        "GROUP BY table_name"
                    )
                )
            ).all()
        granted = {name: set(privileges.split(",")) for name, privileges in rows}

        for table in sorted(Base.metadata.tables):
            assert table in granted, f"{table}: the app role has NO privilege on it at all"
            assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= granted[table], (
                f"{table}: the app role is missing part of SELECT/INSERT/UPDATE/DELETE — "
                f"it has only {sorted(granted[table])}"
            )
