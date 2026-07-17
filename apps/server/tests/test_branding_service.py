"""The branding service — and the ``WHERE`` clause that is the whole isolation belt for it.

``tenants`` carries NO row-level-security policy (migration 0008, deliberately). Every other table
in this product is protected twice: by the policy, and by the service's own filter. This one is
protected ONCE. So these tests are not "does the getter get" — they are the belt itself, and the
two-business cases below are the ones that matter.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.branding import TenantBrandingUpdate
from aethercal.server.db.models import Tenant
from aethercal.server.services.branding import (
    TenantNotFoundError,
    get_branding,
    update_branding,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

SOL = TenantBrandingUpdate(
    public_name="Clinica Sol",
    logo_url="https://cdn.sol.example/sol.png",
    accent_color="#e0894b",
    timezone="America/New_York",
)
LUNA = TenantBrandingUpdate(
    public_name="Estudio Luna",
    logo_url="https://cdn.luna.example/luna.svg",
    accent_color="#3a5f8f",
    timezone="Europe/Madrid",
)


async def test_a_fresh_business_has_no_branding_and_a_utc_timezone(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The state every existing row is in on the day 0014 lands."""
    tenant = await tenant_factory(sqlite_session, name="Sol Holdings LLC")

    brand = await get_branding(sqlite_session, tenant_id=tenant.id)

    assert brand.public_name is None
    assert brand.logo_url is None
    assert brand.accent_color is None
    assert brand.timezone == "UTC"


async def test_update_round_trips_every_field(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)

    await update_branding(sqlite_session, tenant_id=tenant.id, data=SOL)
    await sqlite_session.flush()
    brand = await get_branding(sqlite_session, tenant_id=tenant.id)

    assert brand.public_name == "Clinica Sol"
    assert brand.logo_url == "https://cdn.sol.example/sol.png"
    assert brand.accent_color == "#e0894b"
    assert brand.timezone == "America/New_York"


async def test_an_update_clears_the_fields_the_operator_emptied(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The write is COMPLETE, not partial: an emptied box means "remove it", not "leave it"."""
    tenant = await tenant_factory(sqlite_session)
    await update_branding(sqlite_session, tenant_id=tenant.id, data=SOL)
    await sqlite_session.flush()

    await update_branding(
        sqlite_session,
        tenant_id=tenant.id,
        data=TenantBrandingUpdate(timezone="America/New_York"),
    )
    await sqlite_session.flush()
    brand = await get_branding(sqlite_session, tenant_id=tenant.id)

    assert brand.public_name is None
    assert brand.logo_url is None
    assert brand.accent_color is None
    assert brand.timezone == "America/New_York"


async def test_the_getter_reads_only_the_business_it_was_asked_for(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==Criterion 44, at the service.== Two businesses, both branded, on one instance.

    There is no policy on ``tenants`` to catch a missing filter here — a getter that reached for
    "the tenant" without saying WHICH would happily hand back the first row in the table, and on a
    single-business dev instance it would look perfect forever.
    """
    sol = await tenant_factory(sqlite_session, name="Sol Holdings LLC")
    luna = await tenant_factory(sqlite_session, name="Luna SL", email="luna@example.com")
    await update_branding(sqlite_session, tenant_id=sol.id, data=SOL)
    await update_branding(sqlite_session, tenant_id=luna.id, data=LUNA)
    await sqlite_session.flush()

    sol_brand = await get_branding(sqlite_session, tenant_id=sol.id)
    luna_brand = await get_branding(sqlite_session, tenant_id=luna.id)

    assert sol_brand.public_name == "Clinica Sol"
    assert sol_brand.logo_url == "https://cdn.sol.example/sol.png"
    assert sol_brand.accent_color == "#e0894b"
    assert sol_brand.timezone == "America/New_York"

    assert luna_brand.public_name == "Estudio Luna"
    assert luna_brand.logo_url == "https://cdn.luna.example/luna.svg"
    assert luna_brand.accent_color == "#3a5f8f"
    assert luna_brand.timezone == "Europe/Madrid"


async def test_writing_one_business_does_not_touch_the_other(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The belt in the WRITE direction: an unscoped ``UPDATE tenants SET ...`` rebrands everyone."""
    sol = await tenant_factory(sqlite_session, name="Sol Holdings LLC")
    luna = await tenant_factory(sqlite_session, name="Luna SL", email="luna@example.com")
    await update_branding(sqlite_session, tenant_id=luna.id, data=LUNA)
    await sqlite_session.flush()

    await update_branding(sqlite_session, tenant_id=sol.id, data=SOL)
    await sqlite_session.flush()

    luna_brand = await get_branding(sqlite_session, tenant_id=luna.id)
    assert luna_brand.public_name == "Estudio Luna"
    assert luna_brand.accent_color == "#3a5f8f"


@pytest.mark.parametrize("call", ["get", "update"])
async def test_an_unknown_business_raises_rather_than_returning_nothing(
    sqlite_session: AsyncSession, call: str
) -> None:
    """==Fail loud, not empty.==

    The ``tenant_id`` reaching this service came from an authenticated request (or from the admin's
    own resolved context), and ``api_keys.tenant_id`` is a foreign key — so a business that is not
    there means referential integrity has been lost, not that a guest typed a bad URL. Handing back
    ``None`` would let a caller render an unbranded page over a broken database and call it a day.
    """
    missing = uuid.uuid4()
    with pytest.raises(TenantNotFoundError):
        if call == "get":
            await get_branding(sqlite_session, tenant_id=missing)
        else:
            await update_branding(sqlite_session, tenant_id=missing, data=SOL)
