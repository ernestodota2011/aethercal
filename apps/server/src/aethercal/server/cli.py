"""The ``aethercal-admin`` CLI (F1-11): create a tenant + first user, issue API keys.

The Typer commands are thin: they read env-sourced :class:`Settings`, build a sessionmaker, and
delegate to the ``run_*`` coroutines, which own a single transaction each. Those coroutines are the
testable seam — the offline suite drives them against an aiosqlite sessionmaker, proving a
CLI-issued key verifies through the same service the API uses.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.models import Tenant, User
from aethercal.server.integrations.google.oauth import get_credentials
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.settings import Settings

app = typer.Typer(help="AetherCal admin CLI.", no_args_is_help=True)

# Default cache location for the Google OAuth token during the loopback consent flow (matches the
# F0-11 spike). Outside the repo, so a token never lands in version control.
_DEFAULT_GOOGLE_TOKEN_PATH = (
    Path.home() / ".aetherlogik" / "secrets" / "aethercal-google-token.json"
)


async def run_create_tenant(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    name: str,
    email: str,
    timezone: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a tenant and its first user in one transaction. Returns ``(tenant_id, user_id)``."""
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=slug, name=name)
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email=email, name=name, timezone=timezone)
        session.add(user)
        await session.flush()
        return tenant.id, user.id


async def run_issue_key(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    name: str,
) -> str:
    """Issue an API key for the tenant identified by ``tenant_slug``. Returns the plaintext key."""
    async with sessionmaker() as session, session.begin():
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise LookupError(f"no tenant with slug {tenant_slug!r}")
        _, full_key = await issue_api_key(session, tenant_id=tenant.id, name=name)
        return full_key


async def run_connect_google(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    user_email: str,
    credential: GoogleCredential,
    fernet: Fernet,
) -> uuid.UUID:
    """Store a host's Google connection (F1-07) with the token JSON encrypted at rest.

    Resolves the tenant by slug and the host user by email, then delegates to
    ``store_google_connection`` (Fernet-encrypts before persisting). Returns the connection id. The
    ``credential`` (account email + token JSON) argument keeps this coroutine offline-testable; the
    live OAuth consent that produces it lives in the Typer command below.
    """
    async with sessionmaker() as session, session.begin():
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise LookupError(f"no tenant with slug {tenant_slug!r}")
        user = (
            await session.scalars(
                select(User).where(User.tenant_id == tenant.id, User.email == user_email)
            )
        ).one_or_none()
        if user is None:
            raise LookupError(f"no user with email {user_email!r} in tenant {tenant_slug!r}")
        connection = await store_google_connection(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            credential=credential,
            fernet=fernet,
        )
        return connection.id


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    return build_sessionmaker(build_async_engine(settings.database_config()))


@app.command("create-tenant")
def create_tenant_command(
    slug: Annotated[str, typer.Option(help="URL-safe unique tenant slug.")],
    name: Annotated[str, typer.Option(help="Tenant display name.")],
    email: Annotated[str, typer.Option(help="Email of the tenant's first user.")],
    timezone: Annotated[str, typer.Option(help="IANA timezone of the first user.")] = "UTC",
) -> None:
    """Create a tenant and its first user, printing their ids."""
    tenant_id, user_id = asyncio.run(
        run_create_tenant(_sessionmaker(), slug=slug, name=name, email=email, timezone=timezone)
    )
    typer.echo(f"tenant_id={tenant_id}")
    typer.echo(f"user_id={user_id}")


@app.command("issue-api-key")
def issue_api_key_command(
    tenant_slug: Annotated[str, typer.Option(help="Slug of the tenant to issue the key for.")],
    name: Annotated[str, typer.Option(help="Human label for the key.")],
) -> None:
    """Issue an API key for a tenant and print the plaintext key ONCE (it is not recoverable)."""
    full_key = asyncio.run(run_issue_key(_sessionmaker(), tenant_slug=tenant_slug, name=name))
    typer.echo(full_key)


@app.command("connect-google")
def connect_google_command(  # pragma: no cover - live OAuth (loopback browser consent)
    tenant_slug: Annotated[str, typer.Option(help="Slug of the tenant that owns the host.")],
    user_email: Annotated[str, typer.Option(help="Email of the host user to connect.")],
    account_email: Annotated[str, typer.Option(help="The Google account being connected.")],
    token_path: Annotated[
        Path,
        typer.Option(help="Where the OAuth token JSON is cached during the consent flow."),
    ] = _DEFAULT_GOOGLE_TOKEN_PATH,
) -> None:
    """Run the loopback Google OAuth consent and store the encrypted connection (RF-11).

    Opens a browser once for consent (or refreshes a cached token), then persists the token JSON
    encrypted with the app-secret-derived Fernet key. The consent flow requires the agency OAuth
    Desktop client env vars (``AETHERCAL_GOOGLE_CLIENT_ID`` / ``_SECRET``).
    """
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    credentials = get_credentials(token_path)
    connection_id = asyncio.run(
        run_connect_google(
            build_sessionmaker(build_async_engine(settings.database_config())),
            tenant_slug=tenant_slug,
            user_email=user_email,
            credential=GoogleCredential(
                account_email=account_email, token_json=credentials.to_json()
            ),
            fernet=Fernet(settings.fernet_key()),
        )
    )
    typer.echo(f"connection_id={connection_id}")


if __name__ == "__main__":  # pragma: no cover
    app()
