"""The admin's branding panel: the only write surface for the four columns a guest sees (B-07).

Two businesses, always — because a branding editor that quietly writes "the tenant" instead of
"this tenant" is correct on every single-business instance in existence, including the one the
screenshot was taken on.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.admin.service import (
    AdminActionError,
    BrandingForm,
    branding_view,
    update_branding_action,
)
from aethercal.server.db import Base
from aethercal.server.db.models import Tenant
from aethercal.server.services.rbac import Principal

#: The instance operator holds every business-scoped capability, so it exercises the branding
#: service without turning these tests into RBAC tests (that is ``test_admin_rbac.py``'s job).
_OPERATOR = Principal.bootstrap_operator()

Sessionmaker = async_sessionmaker[AsyncSession]

SOL = BrandingForm(
    public_name="Clinica Sol",
    logo_url="https://cdn.sol.example/sol.png",
    accent_color="#c21e56",
    timezone="America/New_York",
)
LUNA = BrandingForm(
    public_name="Estudio Luna",
    logo_url="https://cdn.luna.example/luna.svg",
    accent_color="#3a5f8f",
    timezone="Europe/Madrid",
)


def _admin(maker: Sessionmaker) -> AdminRuntime:
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="admin", password_hash="x", tenant_slug=None),
    )


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[Sessionmaker]:
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


async def _tenant(maker: Sessionmaker, *, slug: str, name: str) -> uuid.UUID:
    async with maker() as session, session.begin():
        tenant = Tenant(slug=slug, name=name)
        session.add(tenant)
        await session.flush()
        return tenant.id


async def test_an_unbranded_business_shows_the_operator_empty_boxes_and_its_real_name(
    sessionmaker: Sessionmaker,
) -> None:
    """==The admin form needs the RAW ``public_name``, not the resolved one.==

    The API hands the booking page ``display_name`` already resolved (``public_name or name``), and
    that is right for a page. It would be wrong here: an operator who has set no trading name must
    see an EMPTY box — not their legal name pre-filled into a field they never filled, which they
    would then save, silently turning a fallback into a stored value.
    """
    await _tenant(sessionmaker, slug="sol", name="Sol Holdings LLC")

    view = await branding_view(_admin(sessionmaker), principal=_OPERATOR, tenant_slug="sol")

    assert view.public_name == ""
    assert view.logo_url == ""
    assert view.accent_color == ""
    assert view.timezone == "UTC"
    assert view.registered_name == "Sol Holdings LLC"


async def test_saving_round_trips(sessionmaker: Sessionmaker) -> None:
    await _tenant(sessionmaker, slug="sol", name="Sol Holdings LLC")
    admin = _admin(sessionmaker)

    await update_branding_action(admin, principal=_OPERATOR, tenant_slug="sol", form=SOL)
    view = await branding_view(admin, principal=_OPERATOR, tenant_slug="sol")

    assert view.public_name == "Clinica Sol"
    assert view.logo_url == "https://cdn.sol.example/sol.png"
    assert view.accent_color == "#c21e56"
    assert view.timezone == "America/New_York"


async def test_the_operator_edits_only_their_own_business(sessionmaker: Sessionmaker) -> None:
    """==Criterion 44, at the write surface.== Editing Sol must not touch Luna."""
    await _tenant(sessionmaker, slug="sol", name="Sol Holdings LLC")
    await _tenant(sessionmaker, slug="luna", name="Luna SL")
    admin = _admin(sessionmaker)

    await update_branding_action(admin, principal=_OPERATOR, tenant_slug="luna", form=LUNA)
    await update_branding_action(admin, principal=_OPERATOR, tenant_slug="sol", form=SOL)

    sol = await branding_view(admin, principal=_OPERATOR, tenant_slug="sol")
    luna = await branding_view(admin, principal=_OPERATOR, tenant_slug="luna")

    assert sol.public_name == "Clinica Sol"
    assert sol.accent_color == "#c21e56"
    assert luna.public_name == "Estudio Luna"
    assert luna.accent_color == "#3a5f8f"


async def test_clearing_a_box_removes_the_value(sessionmaker: Sessionmaker) -> None:
    """A blank box is "remove it". The form is a COMPLETE write, not a patch (see the schema)."""
    await _tenant(sessionmaker, slug="sol", name="Sol Holdings LLC")
    admin = _admin(sessionmaker)
    await update_branding_action(admin, principal=_OPERATOR, tenant_slug="sol", form=SOL)

    await update_branding_action(
        admin,
        principal=_OPERATOR,
        tenant_slug="sol",
        form=BrandingForm(
            public_name="", logo_url="", accent_color="", timezone="America/New_York"
        ),
    )
    view = await branding_view(admin, principal=_OPERATOR, tenant_slug="sol")

    assert view.public_name == ""
    assert view.logo_url == ""
    assert view.accent_color == ""
    assert view.timezone == "America/New_York"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accent_color", "red"),
        ("accent_color", "#fff; } body { display: none } .x {"),
        ("logo_url", "http://cdn.example/logo.png"),
        ("logo_url", "javascript:alert(1)"),
        ("timezone", "America"),
        ("timezone", "America/Mars"),
    ],
)
async def test_a_bad_value_is_refused_with_a_readable_message_and_nothing_is_written(
    sessionmaker: Sessionmaker, field: str, value: str
) -> None:
    """==The refusal is the point, and so is the fact that nothing lands.==

    Every one of these is a value that would end up in HTML on a public page. The operator gets a
    sentence they can act on; the row is untouched. (The rules themselves are the schema's — this
    asserts that the admin *consumes* them rather than owning a second, laxer copy.)
    """
    await _tenant(sessionmaker, slug="sol", name="Sol Holdings LLC")
    admin = _admin(sessionmaker)
    bad = {
        "public_name": "Sol",
        "logo_url": "",
        "accent_color": "",
        "timezone": "UTC",
        field: value,
    }

    with pytest.raises(AdminActionError) as caught:
        await update_branding_action(
            admin, principal=_OPERATOR, tenant_slug="sol", form=BrandingForm(**bad)
        )

    assert field in str(caught.value)
    view = await branding_view(admin, principal=_OPERATOR, tenant_slug="sol")
    assert view.public_name == ""
    assert view.timezone == "UTC"
