"""The ``aethercal-admin`` CLI (F1-11): create a tenant + first user, issue API keys.

The Typer commands are thin: they read env-sourced :class:`Settings`, build a sessionmaker, and
delegate to the ``run_*`` coroutines, which own a single transaction each. Those coroutines are the
testable seam — the offline suite drives them against an aiosqlite sessionmaker, proving a
CLI-issued key verifies through the same service the API uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from cryptography.fernet import Fernet
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aethercal.server.channels import Channel
from aethercal.server.db.config import OWNER_DATABASE_URL_ENV
from aethercal.server.db.engine import build_async_engine, build_sessionmaker, build_sync_engine
from aethercal.server.db.migrate import head_revision, run_migrations
from aethercal.server.db.models import ApiKey, Booking, Outbox, OutboxStatus, Tenant
from aethercal.server.db.roles import DbRole, assert_engine_role, assert_sync_engine_role
from aethercal.server.integrations.google.oauth import get_credentials
from aethercal.server.services.api_keys import (
    RevokeKeyOutcome,
    issue_api_key,
    list_api_keys,
    revoke_api_key,
)
from aethercal.server.services.calendars import (
    GoogleCredential,
    link_booking_calendar,
    store_google_connection,
)
from aethercal.server.services.key_rotation import (
    KeyRotationError,
    RotationReport,
    rotate_fernet_key,
)
from aethercal.server.services.notifications import record_booking_notification
from aethercal.server.services.outbox import PROVIDER_CALL_MARKER
from aethercal.server.services.privacy import PurgeReport, purge_guest
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialError,
    CredentialProvider,
    credential_class,
    delete_credential,
    list_credential_providers,
    required_fields,
    store_credential,
)
from aethercal.server.services.users import (
    UserData,
    UserNotFoundError,
    UserServiceError,
    create_user,
    get_user_by_email,
)
from aethercal.server.services.workflows import seed_default_workflows
from aethercal.server.settings import Settings

_logger = logging.getLogger(__name__)

app = typer.Typer(help="AetherCal admin CLI.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys (C7b).", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
outbox_app = typer.Typer(
    help="Inspect and repair the transactional outbox (R9).", no_args_is_help=True
)
app.add_typer(outbox_app, name="outbox")
guest_app = typer.Typer(help="Guest data: erasure (RNF-8).", no_args_is_help=True)
app.add_typer(guest_app, name="guest")
db_app = typer.Typer(help="Schema migrations (run as the OWNER role).", no_args_is_help=True)
app.add_typer(db_app, name="db")
credentials_app = typer.Typer(
    help="BYOK: each business's own provider credentials (RF-27).", no_args_is_help=True
)
app.add_typer(credentials_app, name="credentials")

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
    """Create a tenant and its first host in one transaction. Returns ``(tenant_id, user_id)``.

    ==The host is written through ``services/users``, like every other host in the product.== It
    used to be built here, inline against the model — a second copy of the CRUD the admin panel
    already had, and the two had diverged: this one validated NOTHING, so ``--email "not-an-email"
    --timezone "America/Mars"`` created that host in silence and the first symptom would have been a
    confirmation email that never arrived.

    A refusal raises :class:`~aethercal.server.services.users.InvalidUserError` and the whole
    transaction rolls back: no half-made tenant is left behind for the operator to collide with when
    they fix the typo and run it again.
    """
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=slug, name=name)
        session.add(tenant)
        await session.flush()
        # The first host is named after the business — ``create-tenant`` has a single ``--name``,
        # and a real host list is authored in the panel (RF-30's host CRUD).
        user = await create_user(
            session, tenant_id=tenant.id, data=UserData(name=name, email=email, timezone=timezone)
        )
        # A tenant with no workflow rules has no reminders. Migration 0005 seeds the default rule
        # for the tenants that already existed; this is the other half — every tenant created
        # AFTERWARDS. Miss it and a brand-new self-host silently never reminds anybody.
        await seed_default_workflows(session, tenant_id=tenant.id)
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


async def run_list_keys(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
) -> list[ApiKey]:
    """List the API keys (active and revoked) of the tenant identified by ``tenant_slug``."""
    async with sessionmaker() as session:
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise LookupError(f"no tenant with slug {tenant_slug!r}")
        return await list_api_keys(session, tenant_id=tenant.id)


async def run_revoke_key(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    api_key_id: uuid.UUID,
) -> tuple[RevokeKeyOutcome, str | None]:
    """Revoke the API key ``api_key_id`` iff it belongs to ``tenant_slug``. Returns ``(outcome,
    prefix)`` from the service's atomic revoke — ``prefix`` identifies the key (never the secret)
    and is ``None`` on :attr:`RevokeKeyOutcome.NOT_FOUND`.

    This coroutine only resolves the slug → tenant; the idempotency and the outcome are owned by
    :func:`revoke_api_key` (a single atomic conditional UPDATE decided by rowcount), so revoking a
    key twice — or concurrently — never re-stamps ``revoked_at`` nor double-reports ``REVOKED``. An
    unknown slug raises ``LookupError``; an unknown/cross-tenant id reports
    :attr:`RevokeKeyOutcome.NOT_FOUND` (the two id cases are indistinguishable on purpose).
    """
    async with sessionmaker() as session, session.begin():
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise LookupError(f"no tenant with slug {tenant_slug!r}")
        return await revoke_api_key(session, api_key_id=api_key_id, tenant_id=tenant.id)


async def run_connect_google(  # noqa: PLR0913 - the tenant/host pair + credential + target calendar
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    user_email: str,
    credential: GoogleCredential,
    fernet: Fernet,
    calendar_id: str | None = None,
) -> uuid.UUID:
    """Store a host's Google connection (RF-11) with the token JSON encrypted at rest.

    Resolves the tenant by slug and the host user by email, then delegates to
    ``store_google_connection`` (Fernet-encrypts before persisting). Returns the connection id. The
    ``credential`` (account email + token JSON) argument keeps this coroutine offline-testable; the
    live OAuth consent that produces it lives in the Typer command below.

    ``calendar_id`` designates the calendar this host's bookings are WRITTEN to (and whose freebusy
    blocks their slots) — the way to book into a dedicated secondary calendar rather than the
    account's ``primary``, which is what connecting a real account should always do. Omitted, the
    account's default calendar is used (zero-config, unchanged behaviour).
    """
    async with sessionmaker() as session, session.begin():
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug))
        ).one_or_none()
        if tenant is None:
            raise LookupError(f"no tenant with slug {tenant_slug!r}")
        try:
            user = await get_user_by_email(session, tenant_id=tenant.id, email=user_email)
        except UserNotFoundError as exc:
            raise LookupError(
                f"no user with email {user_email!r} in tenant {tenant_slug!r}"
            ) from exc
        connection = await store_google_connection(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            credential=credential,
            fernet=fernet,
        )
        if calendar_id is not None:
            await link_booking_calendar(session, connection=connection, calendar_id=calendar_id)
        return connection.id


# --------------------------------------------------------------------------------------
# The outbox (R9): read the dead-letter, and revive an intent — without opening psql.
# --------------------------------------------------------------------------------------


class ReplayOutcome(StrEnum):
    """What a replay actually did. Three different facts, never collapsed into "ok"."""

    REVIVED = "revived"
    NOT_FOUND = "not_found"
    NOT_DEAD = "not_dead"
    """It exists, but it is not parked — so replaying it would have been a mistake, not a fix."""


async def run_list_dead_intents(sessionmaker: async_sessionmaker[AsyncSession]) -> list[Outbox]:
    """The dead-letter: intents that exhausted their attempts and will never retry on their own.

    A replay command you cannot feed an id to is half a fix — finding the id was the very reason an
    operator had to open psql in the first place. Carries no guest data: id, effect, attempts,
    booking, and when it last tried.
    """
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.status == OutboxStatus.DEAD.value)
                    .order_by(Outbox.created_at)
                )
            ).all()
        )


async def run_replay_intent(
    sessionmaker: async_sessionmaker[AsyncSession], *, intent_id: uuid.UUID
) -> ReplayOutcome:
    """Revive a DEAD outbox intent: back to ``pending``, due now, attempts reset.

    ==Only a dead intent — and the refusal is a rowcount.== This is a command handed to a tired
    operator in the middle of an incident, so the dangerous cases are closed by construction rather
    than by care:

    * a ``delivered`` intent is not stuck: "replaying" it re-sends a message the guest already has;
    * a ``claimed`` one is in a worker's hands RIGHT NOW — resetting it would have two workers
      sending the same thing;
    * a ``failed`` one is already scheduled to retry on its own backoff;
    * a ``skipped`` / ``voided`` one was retired on purpose.

    Only ``dead`` is genuinely parked with nothing left to move it. The guard is a single
    conditional UPDATE gated on ``status = 'dead'``, arbitrated by ``rowcount`` — not a
    read-then-write, which a concurrent drain could slip between and then quietly stomp a live row.

    ``attempts`` is reset to 0 deliberately: revived with its six attempts intact, the intent sits
    one transient blip away from the dead-letter, so the next flicker re-parks it and the operator's
    replay bought nothing. The reset is what makes it a real second chance. ``next_retry_at`` is
    cleared, so it is due at the very next drain.
    """
    async with sessionmaker() as session, session.begin():
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(Outbox)
                .where(Outbox.id == intent_id, Outbox.status == OutboxStatus.DEAD.value)
                .values(
                    status=OutboxStatus.PENDING.value,
                    attempts=0,
                    next_retry_at=None,
                    claimed_by=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount == 1:
            _logger.warning(
                "outbox intent %s REPLAYED by an operator: dead → pending, attempts reset to 0. It "
                "is due at the next drain",
                intent_id,
            )
            return ReplayOutcome.REVIVED
        # It matched nothing, and WHY decides what the operator is told. "There is no such intent"
        # and "that intent is alive and I refuse to touch it" are entirely different facts; merging
        # them into one shrug sends somebody looking for the wrong problem.
        existing = await session.get(Outbox, intent_id)
        return ReplayOutcome.NOT_FOUND if existing is None else ReplayOutcome.NOT_DEAD


# --------------------------------------------------------------------------------------
# Guest erasure (RNF-8).
# --------------------------------------------------------------------------------------


async def run_guest_purge(
    sessionmaker: async_sessionmaker[AsyncSession], *, tenant_slug: str, email: str
) -> PurgeReport:
    """Erase a guest from ONE named tenant. ==An unresolvable tenant is a hard stop.==

    ``tenant_slug`` is required, and it is not a filter this may fall back from. ==The CLI has no
    isolation belt of its own== — it runs as the owner of the database, over every business on the
    instance — so if the scope cannot be resolved, the safe thing is to do NOTHING, loudly. An empty
    or unknown slug matches no tenant and raises ``LookupError``; it does not degrade into "purge
    everywhere", which would erase that person from businesses that never received the request.

    One transaction: the redactions and the deletes commit together, or not at all. A purge that
    half committed would leave the guest's data in exactly the tables nobody thought to check.
    """
    async with sessionmaker() as session, session.begin():
        tenant = (
            await session.scalars(select(Tenant).where(Tenant.slug == tenant_slug.strip()))
        ).one_or_none()
        if tenant is None:
            raise LookupError(
                f"no tenant with slug {tenant_slug!r}: refusing to purge. A guest purge is scoped "
                "to ONE business, and an unscoped one would erase this person from every other "
                "business on this instance"
            )
        return await purge_guest(session, tenant_id=tenant.id, email=email)


# --------------------------------------------------------------------------------------
# BYOK credentials (RF-27) — and the key rotation that keeps them readable.
# --------------------------------------------------------------------------------------


async def _tenant_id_for(session: AsyncSession, slug: str) -> uuid.UUID:
    """Resolve a business by slug, or STOP. ==An unresolvable business is never a filter.==

    The same rule ``guest purge`` runs on, and here it guards the other direction: a payment
    credential written to the wrong business is money arriving in the wrong account.
    """
    tenant = (
        await session.scalars(select(Tenant).where(Tenant.slug == slug.strip()))
    ).one_or_none()
    if tenant is None:
        raise LookupError(f"no tenant with slug {slug!r}")
    return tenant.id


async def run_credentials_set(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    key: bytes,
) -> None:
    """Store (or replace) one business's credential for ``provider``, encrypted at rest."""
    async with sessionmaker() as session, session.begin():
        tenant_id = await _tenant_id_for(session, tenant_slug)
        await store_credential(
            session, tenant_id=tenant_id, provider=provider, secrets=secrets, fernet_key=key
        )


async def run_credentials_list(
    sessionmaker: async_sessionmaker[AsyncSession], *, tenant_slug: str
) -> tuple[CredentialProvider, ...]:
    """Which providers this business has configured. ==Takes no key, so it can leak no secret.=="""
    async with sessionmaker() as session, session.begin():
        tenant_id = await _tenant_id_for(session, tenant_slug)
        return await list_credential_providers(session, tenant_id=tenant_id)


async def run_credentials_delete(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_slug: str,
    provider: CredentialProvider,
) -> bool:
    """Remove a business's credential. For a money provider: ==this business stops charging.=="""
    async with sessionmaker() as session, session.begin():
        tenant_id = await _tenant_id_for(session, tenant_slug)
        return await delete_credential(session, tenant_id=tenant_id, provider=provider)


async def run_rotate_key(
    sessionmaker: async_sessionmaker[AsyncSession], *, new_key: bytes, previous_key: bytes
) -> RotationReport:
    """Re-encrypt every stored secret onto the new key.

    ==One transaction: all of the rows, or none of them.==
    """
    async with sessionmaker() as session, session.begin():
        return await rotate_fernet_key(session, new_key=new_key, previous_key=previous_key)


class _CredentialInputError(ValueError):
    """A credential piped to the CLI was not an object of field → non-empty STRING."""


def _json_type(value: object) -> str:
    """The JSON type name of a parsed value — for a refusal that says WHAT was wrong without ever
    echoing the value itself."""
    match value:
        case bool():
            return "boolean"
        case int() | float():
            return "number"
        case None:
            return "null"
        case list():
            return "array"
        case dict():
            return "object"
        case _:
            return type(value).__name__


def _credential_fields(parsed: Mapping[str, Any], *, expected: frozenset[str]) -> dict[str, str]:
    """Marshal parsed JSON into the service's ``Mapping[str, str]`` contract, or REFUSE at the door.

    ``json.loads`` yields values of any JSON type; a credential field is a string. Coercing with
    ``str(value)`` — as this once did — turns ``{"secret_key": {"nested": "x"}}`` into the literal
    text ``"{'nested': 'x'}"`` and stores it as a credential that looks configured and fails only
    when a guest's money has already left their card. So every value must ALREADY be a non-empty
    string; a nested object, an array, a number, a boolean, ``null`` or a blank string is refused.

    ==The refusal never echoes the value NOR a caller-supplied field NAME.== The value is a secret,
    wrong shape or not — but so is a field NAME, because it is piped in exactly the same way: a
    mislabelled export or a copy-paste slip can put the secret in the KEY as easily as the value,
    and that key would then reach the terminal, the scrollback and the CI log. So a name is echoed
    ONLY when it is one the provider itself declares (``expected`` = ``required_fields(provider)``,
    a fixed set THIS code controls); any other key is referred to as "a credential field" and never
    printed. The JSON type is always safe to name and is kept, which is enough for the operator to
    fix their own input.

    ``expected`` gates only what the refusal may NAME — it does not reject unknown fields (an
    unexpected but well-formed field flows on to the service's ``_validate``, which reports the
    provider's own missing required fields, never the input's keys).

    This is the ONE place untyped JSON becomes a credential: ``store_credential`` is typed
    ``Mapping[str, str]``, so no other (typed, Python) caller can reach it with a non-string, and
    the service's own ``_validate`` still guards field COMPLETENESS for every caller. The
    value-shape rule therefore lives here, not duplicated in the service.
    """
    fields: dict[str, str] = {}
    for field, value in parsed.items():
        name = str(field)
        # Only a name the PROVIDER declares is a literal we control and safe to echo; a
        # caller-supplied key may itself be a secret, so it is named generically and never printed.
        shown = f"the credential field {name!r}" if name in expected else "a credential field"
        if not isinstance(value, str):
            raise _CredentialInputError(
                f"{shown} must be a JSON string, but it is a "
                f"{_json_type(value)}. Every field is a single string value; a nested object, an "
                "array, a number, a boolean or null is not a credential. (Neither the value nor an "
                "unexpected field name is shown — either may be a secret.)"
            )
        if not value.strip():
            raise _CredentialInputError(
                f"{shown} is empty. A blank value looks configured and fails "
                "at the moment it is used — which, for a payment provider, is after the guest's "
                "money has already left their card."
            )
        fields[name] = value
    return fields


@credentials_app.command("set")
def credentials_set_command(
    tenant_slug: Annotated[str, typer.Option(help="Slug of the business it belongs to.")],
    provider: Annotated[CredentialProvider, typer.Option(help="Which provider this is for.")],
) -> None:
    """Store a business's own credential for a provider, read as JSON from STDIN.

    ==The secret is read from STDIN and never from the command line.== An ``--api-key sk_live_…``
    option would put a live payment key into the process table (where every user on the box can read
    it with ``ps``), into the shell's history file, and into the terminal scrollback. There is no
    way to write that option safely, so it does not exist.

        cat stripe.json | aethercal-admin credentials set --tenant-slug acme --provider stripe

    where ``stripe.json`` is ``{"secret_key": "...", "webhook_secret": "..."}``. Every field that
    provider requires must be present: a credential that exists but cannot finish its job is worse
    than none at all — for a payment provider it fails at the moment the guest's money has already
    left their card.

    ==For a MONEY provider, storing this is what makes the business able to charge — into ITS OWN
    account.== Without it, it does not charge at all; it does NOT fall back to the instance's.
    """
    if sys.stdin.isatty():
        typer.echo(
            "refusing to read a credential from an interactive terminal: pipe it in as JSON.\n"
            "\n"
            f"  cat credential.json | aethercal-admin credentials set --tenant-slug {tenant_slug} "
            f"--provider {provider.value}\n"
            "\n"
            "The secret is never taken as a command-line option — it would land in `ps`, in the "
            "shell history and in the scrollback.",
            err=True,
        )
        raise typer.Exit(code=2)

    raw = sys.stdin.read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # The message names the PARSE failure and never echoes what was piped in — it is a secret.
        typer.echo(
            f"stdin is not valid JSON ({exc.msg}); expected an object of field → value.", err=True
        )
        raise typer.Exit(code=2) from exc
    if not isinstance(parsed, dict):
        typer.echo("stdin must be a JSON object of field → value.", err=True)
        raise typer.Exit(code=2)

    try:
        secrets = _credential_fields(
            cast(dict[Any, Any], parsed), expected=required_fields(provider)
        )
    except _CredentialInputError as exc:
        # Names the JSON type (and a provider field name); never the value nor a caller-supplied
        # key — either can be a secret. See _credential_fields.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    try:
        asyncio.run(
            run_credentials_set(
                _sessionmaker(),
                tenant_slug=tenant_slug,
                provider=provider,
                secrets=secrets,
                key=settings.fernet_key(),
            )
        )
    except (CredentialError, LookupError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    # Names the provider and the business. Never the value — not even a prefix of it.
    typer.echo(f"stored the {provider.value} credential for {tenant_slug}")


@credentials_app.command("list")
def credentials_list_command(
    tenant_slug: Annotated[str, typer.Option(help="Slug of the business.")],
) -> None:
    """List which providers a business has configured. ==Never prints a secret; never decrypts.=="""
    try:
        providers = asyncio.run(run_credentials_list(_sessionmaker(), tenant_slug=tenant_slug))
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if not providers:
        typer.echo(
            f"{tenant_slug} has no credentials of its own: it uses the instance defaults for "
            "sending, and it cannot charge at all."
        )
        return
    for provider in providers:
        typer.echo(f"{provider.value}  ({credential_class(provider).value})")


@credentials_app.command("delete")
def credentials_delete_command(
    tenant_slug: Annotated[str, typer.Option(help="Slug of the business.")],
    provider: Annotated[CredentialProvider, typer.Option(help="Which provider to remove.")],
) -> None:
    """Remove a business's credential. ==For a money provider, this business then STOPS CHARGING.==

    It does not fall back to the instance's account — that is the one thing it must never do.
    """
    try:
        removed = asyncio.run(
            run_credentials_delete(_sessionmaker(), tenant_slug=tenant_slug, provider=provider)
        )
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if not removed:
        typer.echo(f"{tenant_slug} had no {provider.value} credential")
        raise typer.Exit(code=1)
    typer.echo(f"removed the {provider.value} credential for {tenant_slug}")
    if credential_class(provider) is CredentialClass.MONEY:
        typer.echo(f"{tenant_slug} can no longer charge: it has no payment credential of its own.")


@credentials_app.command("rotate-key")
def credentials_rotate_key_command() -> None:
    """Re-encrypt every stored secret onto a new app secret. ==Loses nothing; exposes nothing.==

    The Fernet key is derived from ``AETHERCAL_APP_SECRET``, so rotating it means rotating that
    secret. Run this with the NEW secret in ``AETHERCAL_APP_SECRET`` and the retiring one in
    ``AETHERCAL_PREVIOUS_APP_SECRET``; then unset the latter.

    It runs as the OWNER, and it must: under row-level security every other role sees one business
    at a time — and a rotation that reached only one business is the failure it exists to prevent.

    Every stored secret moves in ONE transaction: every row, or none. It is resumable (a row already
    on the new key needs nothing), and a row that decrypts under NEITHER key stops it dead rather
    than being skipped — because a skipped row is a row nobody can read once the old secret is gone.

    It prints COUNTS. Never a secret.
    """
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    previous = settings.previous_fernet_key()
    if previous is None:
        typer.echo(
            "AETHERCAL_PREVIOUS_APP_SECRET is not set, so there is nothing to rotate FROM.\n"
            "\n"
            "A rotation needs both halves: the NEW secret in AETHERCAL_APP_SECRET, and the one "
            "being retired in AETHERCAL_PREVIOUS_APP_SECRET. Without the old one, every stored "
            "secret in the database is ciphertext this process cannot open — and a 'rotation' that "
            "silently did nothing would be far worse than this message.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        report = asyncio.run(
            run_rotate_key(_sessionmaker(), new_key=settings.fernet_key(), previous_key=previous)
        )
    except KeyRotationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.summary())
    typer.echo("now unset AETHERCAL_PREVIOUS_APP_SECRET: it opens nothing any more.")


@db_app.command("upgrade")
def db_upgrade_command() -> None:
    """Migrate the database to head, as the OWNER. ==The migration path that did not exist.==

    Before the isolation batch, migrations ran on ``AETHERCAL_AUTO_MIGRATE=1`` — inside the web
    process, on the web process's own URL. That single fact is *why* the app had to be the table
    owner, and therefore why row-level security would have been a placebo on this product: the role
    serving requests was the role that owned every table, and an owner bypasses its own policies.

    Separating the roles closes that hole and opens another one, immediately: with ``auto_migrate``
    retired, and the web running as ``aethercal_app`` (which owns nothing and cannot execute DDL),
    ==there was no supported way to migrate at all.== There is no ``alembic.ini`` in this repository
    and there was no CLI command. This is that command.

    It runs Alembic under ``AETHERCAL_OWNER_DATABASE_URL``, with the same PostgreSQL advisory lock
    the boot migrator used, so several instances starting at once still serialize instead of racing
    to create the same tables.

    Run it as a one-shot step BEFORE the web process starts (``deploy/docker-compose.yml`` does).
    The web then refuses to serve a schema behind head, so "running on a stale schema" is not a
    state
    the deployment can reach.
    """
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    config = settings.owner_database_config()
    engine = build_sync_engine(config)
    try:
        # The same assertion every other entry point makes, in its synchronous form: an owner URL
        # that is quietly the app role would fail the DDL here — loudly, which is fine — but it
        # could
        # also be a URL pointing at the WORKER role, which owns nothing and would fail confusingly.
        # Say which role was expected, and which one answered, before Alembic says anything at all.
        assert_sync_engine_role(engine, DbRole.OWNER, url_env=OWNER_DATABASE_URL_ENV)
        run_migrations(engine)
    finally:
        engine.dispose()
    typer.echo(f"schema is at head: {head_revision()}")


def _owner_engine(settings: Settings) -> AsyncEngine:
    """The CLI's engine — ``aethercal_owner``, ==asserted, on every single invocation.==

    ``AETHERCAL_OWNER_DATABASE_URL`` is FAIL-CLOSED (``settings.owner_database_config()`` raises
    when
    it is unset; there is deliberately no fallback to the app URL), and then the role is CHECKED.

    Both halves matter, and the reason they matter is ``guest purge``. It runs as the owner because
    it has to reach every row belonging to one business — including the rows a policy would hide
    from
    the app role. Point it at ``aethercal_app`` by accident and it does not fail: it matches **zero
    rows**, deletes nothing, prints its report, and exits **0**. ==Erasure of a real person's data,
    reporting success, having erased nothing== — on the one command path with a legal deadline
    attached to it.

    The check costs one round-trip per invocation. It is the only thing standing between that
    outcome
    and a green exit code.
    """
    engine = build_async_engine(settings.owner_database_config())
    asyncio.run(assert_engine_role(engine, DbRole.OWNER, url_env=OWNER_DATABASE_URL_ENV))
    return engine


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    return build_sessionmaker(_owner_engine(settings))


@app.command("create-tenant")
def create_tenant_command(
    slug: Annotated[str, typer.Option(help="URL-safe unique tenant slug.")],
    name: Annotated[str, typer.Option(help="Tenant display name.")],
    email: Annotated[str, typer.Option(help="Email of the tenant's first user.")],
    timezone: Annotated[str, typer.Option(help="IANA timezone of the first user.")] = "UTC",
) -> None:
    """Create a tenant and its first user, printing their ids.

    A malformed address or a timezone that is not a real IANA zone is REFUSED here — with the
    service's own sentence and a non-zero exit, not a traceback. It used to be accepted in silence,
    and the operator found out weeks later, from a guest.
    """
    try:
        tenant_id, user_id = asyncio.run(
            run_create_tenant(_sessionmaker(), slug=slug, name=name, email=email, timezone=timezone)
        )
    except UserServiceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
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


@keys_app.command("list")
def keys_list_command(
    tenant_slug: Annotated[str, typer.Option(help="Slug of the tenant to list keys for.")],
) -> None:
    """List a tenant's API keys — id, prefix, name, created_at, last_used_at, status.

    Never prints ``hashed_key`` or any plaintext secret; a key is identified by its ``prefix``.
    """
    keys = asyncio.run(run_list_keys(_sessionmaker(), tenant_slug=tenant_slug))
    if not keys:
        typer.echo(f"no API keys for tenant {tenant_slug!r}")
        return
    for key in keys:
        status = "revoked" if key.revoked_at is not None else "active"
        last_used = key.last_used_at.isoformat() if key.last_used_at is not None else "never"
        typer.echo(
            f"{key.id}  prefix={key.prefix}  name={key.name!r}  "
            f"created_at={key.created_at.isoformat()}  last_used_at={last_used}  status={status}"
        )


@keys_app.command("revoke")
def keys_revoke_command(
    api_key_id: Annotated[uuid.UUID, typer.Argument(help="Id of the API key to revoke.")],
    tenant_slug: Annotated[str, typer.Option(help="Slug of the tenant that owns the key.")],
) -> None:
    """Revoke an API key by id (sets ``revoked_at``; the row is never deleted).

    Idempotent-safe: revoking an already-revoked key reports that instead of failing. An unknown
    id (or one owned by a different tenant) prints a clean message and exits non-zero — never a
    traceback.
    """
    outcome, prefix = asyncio.run(
        run_revoke_key(_sessionmaker(), tenant_slug=tenant_slug, api_key_id=api_key_id)
    )
    if outcome is RevokeKeyOutcome.NOT_FOUND:
        typer.echo(f"no API key {api_key_id} for tenant {tenant_slug!r}", err=True)
        raise typer.Exit(code=1)
    if outcome is RevokeKeyOutcome.ALREADY_REVOKED:
        typer.echo(f"API key {prefix} (id={api_key_id}) was already revoked")
        return
    typer.echo(f"revoked API key {prefix} (id={api_key_id})")


@app.command("connect-google")
def connect_google_command(  # pragma: no cover - live OAuth (loopback browser consent)
    tenant_slug: Annotated[str, typer.Option(help="Slug of the tenant that owns the host.")],
    user_email: Annotated[str, typer.Option(help="Email of the host user to connect.")],
    account_email: Annotated[str, typer.Option(help="The Google account being connected.")],
    token_path: Annotated[
        Path,
        typer.Option(help="Where the OAuth token JSON is cached during the consent flow."),
    ] = _DEFAULT_GOOGLE_TOKEN_PATH,
    calendar_id: Annotated[
        str | None,
        typer.Option(
            help=(
                "Calendar the bookings are written to (e.g. a dedicated secondary calendar). "
                "Omit to use the account's default calendar."
            )
        ),
    ] = None,
) -> None:
    """Run the loopback Google OAuth consent and store the encrypted connection (RF-11).

    Opens a browser once for consent (or refreshes a cached token), then persists the token JSON
    encrypted with the app-secret-derived Fernet key. The consent flow requires the OAuth Desktop
    client env vars (``AETHERCAL_GOOGLE_CLIENT_ID`` / ``_SECRET``).

    Pass ``--calendar-id`` to send this host's bookings to a DEDICATED calendar instead of the
    account's ``primary`` — the recommended setup for any real account, and a hard rule for the
    agency's own instance.
    """
    settings = Settings()  # type: ignore[call-arg]  # fields sourced from the environment (RF-19)
    credentials = get_credentials(token_path)
    connection_id = asyncio.run(
        run_connect_google(
            # ==The SECOND engine-building site in this file, and it needs the same belt as the
            # first.== It was a copy of `_sessionmaker()` that grew its own settings lookup; under
            # RLS a copy that forgot the assertion would be a CLI invocation silently running as the
            # app role — writing an external connection that lands nowhere, and saying it worked.
            build_sessionmaker(_owner_engine(settings)),
            tenant_slug=tenant_slug,
            user_email=user_email,
            credential=GoogleCredential(
                account_email=account_email, token_json=credentials.to_json()
            ),
            fernet=Fernet(settings.fernet_key()),
            calendar_id=calendar_id,
        )
    )
    typer.echo(f"connection_id={connection_id}")


class ResolveOutcome(StrEnum):
    """What ``outbox resolve-unknown`` did."""

    RECORDED_AS_SENT = "recorded_as_sent"
    """The operator confirmed the provider DID send it. The ledger row is written."""
    REQUEUED = "requeued"
    """The operator confirmed it did NOT go out. Back to ``pending``, due now."""
    NOT_FOUND = "not_found"
    NOT_UNKNOWN = "not_unknown"
    """It exists, but it is not parked as ``unknown`` - so there is nothing to resolve."""


async def run_list_unknown_intents(sessionmaker: async_sessionmaker[AsyncSession]) -> list[Outbox]:
    """The intents we handed to a provider and never got an answer for.

    Each one is a message that MAY have reached a real person. Nothing will move them on its own -
    that is the entire point - so this is the queue a human works, provider console open."""
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.status == OutboxStatus.UNKNOWN.value)
                    .order_by(Outbox.created_at)
                )
            ).all()
        )


async def run_resolve_unknown_intent(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    intent_id: uuid.UUID,
    delivered: bool,
    now: datetime | None = None,
) -> ResolveOutcome:
    """Close out an ``unknown`` intent with what a HUMAN saw in the provider's console.

    ==This is the only thing in the system allowed to decide what happened to that message==, and
    that is deliberate: the machine genuinely does not know, and every automatic answer it could
    invent is a guess with a victim on the other end.

    Two honest outcomes, and each repairs a different half of the damage:

    * ``--delivered`` - the provider shows it went out. We write the ``sent_notifications`` row the
      crash swallowed. That prevents a duplicate AND **repairs the daily cap**: the per-phone
      ceiling is derived from that ledger, so until this row exists the guest's quota silently
      under-counts a message they have already received.
    * ``--not-delivered`` - the provider shows nothing. Back to ``pending``, attempts reset, due at
      the next drain. The in-flight marker is cleared, so the drain does not instantly re-park it.

    Gated on ``status = 'unknown'`` under ``FOR UPDATE``, like the replay: a read-then-write would
    let a concurrent drain slip in between.
    """
    moment = now if now is not None else datetime.now(UTC)
    async with sessionmaker() as session, session.begin():
        row = (
            await session.scalars(
                select(Outbox)
                .where(Outbox.id == intent_id, Outbox.status == OutboxStatus.UNKNOWN.value)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            existing = await session.get(Outbox, intent_id)
            return ResolveOutcome.NOT_FOUND if existing is None else ResolveOutcome.NOT_UNKNOWN

        payload = dict(row.payload)
        # The marker was the EVIDENCE that we were mid-flight. It has done its job, either way.
        resolved_payload = {k: v for k, v in payload.items() if k != PROVIDER_CALL_MARKER}

        if not delivered:
            row.payload = resolved_payload
            row.status = OutboxStatus.PENDING.value
            row.attempts = 0
            row.next_retry_at = None
            row.claimed_by = None
            row.lease_expires_at = None
            _logger.warning(
                "outbox intent %s RESOLVED by an operator as NOT delivered: unknown -> pending, "
                "attempts reset. It is due at the next drain",
                intent_id,
            )
            return ResolveOutcome.REQUEUED

        booking = await session.get(Booking, row.booking_id)
        if booking is not None:
            # The ledger row the crash swallowed. Writing it is what stops the duplicate - and what
            # repairs the per-phone daily cap, which is derived from this very table.
            await record_booking_notification(
                session,
                booking=booking,
                kind=str(payload.get("kind", "")),
                now=moment,
                channel=Channel(str(payload["channel"])),
                step_id=uuid.UUID(str(payload["step_id"])),
            )
        row.payload = resolved_payload
        row.status = OutboxStatus.DELIVERED.value
        row.next_retry_at = None
        _logger.warning(
            "outbox intent %s RESOLVED by an operator as DELIVERED: the ledger row was written, so "
            "it is not re-sent and the recipient's daily cap counts it again",
            intent_id,
        )
        return ResolveOutcome.RECORDED_AS_SENT


@outbox_app.command("list")
def outbox_list_command() -> None:
    """List the dead-lettered intents: what was never delivered, and is not retrying on its own.

    Prints no guest data — an id, an effect, a booking, an attempt count. Feed an id to
    `aethercal-admin outbox replay` to give it another go.
    """
    rows = asyncio.run(run_list_dead_intents(_sessionmaker()))
    if not rows:
        typer.echo("no dead intents")
        return
    for row in rows:
        last = row.last_attempt_at.isoformat() if row.last_attempt_at is not None else "never"
        typer.echo(
            f"{row.id}  effect={row.effect}  booking={row.booking_id}  "
            f"attempts={row.attempts}  last_attempt_at={last}"
        )


@outbox_app.command("replay")
def outbox_replay_command(
    intent_id: Annotated[uuid.UUID, typer.Argument(help="Id of the DEAD intent to revive.")],
) -> None:
    """Revive a dead outbox intent (back to `pending`, due now, attempts reset).

    Refuses anything that is not `dead` and exits non-zero: replaying a DELIVERED intent would mail
    a guest a message they already have, and replaying a CLAIMED one would fight the worker sending
    it right now. Until this command existed the only way to do either was by hand, in psql.
    """
    outcome = asyncio.run(run_replay_intent(_sessionmaker(), intent_id=intent_id))
    if outcome is ReplayOutcome.NOT_FOUND:
        typer.echo(f"no outbox intent {intent_id}", err=True)
        raise typer.Exit(code=1)
    if outcome is ReplayOutcome.NOT_DEAD:
        typer.echo(
            f"outbox intent {intent_id} is not dead, so it was NOT replayed — it is either still "
            "live (pending/claimed/failed, and will run on its own) or already terminal "
            "(delivered/skipped/voided). Replaying it would re-send or collide.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"replayed outbox intent {intent_id} (dead -> pending, attempts reset)")


@outbox_app.command("list-unknown")
def outbox_list_unknown_command() -> None:
    """List the intents we handed to a provider and never got an answer for.

    Each is a message that MAY be on a real person's phone. Nothing moves them on its own. Open the
    provider's console, find out what happened, then run `aethercal-admin outbox resolve-unknown`.
    """
    rows = asyncio.run(run_list_unknown_intents(_sessionmaker()))
    if not rows:
        typer.echo("no unknown intents")
        return
    for row in rows:
        started = row.payload.get(PROVIDER_CALL_MARKER, "?")
        channel = row.payload.get("channel", "?")
        typer.echo(
            f"{row.id}  effect={row.effect}  channel={channel}  booking={row.booking_id}  "
            f"handed_to_provider_at={started}"
        )


@outbox_app.command("resolve-unknown")
def outbox_resolve_unknown_command(
    intent_id: Annotated[uuid.UUID, typer.Argument(help="Id of the UNKNOWN intent to resolve.")],
    delivered: Annotated[
        bool | None,
        typer.Option(
            "--delivered/--not-delivered",
            help="What the PROVIDER's console shows: did this message actually go out?",
        ),
    ] = None,
) -> None:
    """Close out an `unknown` intent with what you saw in the provider's console.

    The system parked it because it genuinely does not know: it handed the message to the provider
    and never learned the outcome. It refuses to guess, because both guesses have a victim - a retry
    can message a real person twice (and under-count the daily cap protecting them, since that cap
    is derived from the ledger), and a write-off silently loses a message they never got.

    So you look, and you tell it:

      --delivered      it went out. The ledger row is written, so it is not re-sent AND the
                       recipient's daily cap counts it again.
      --not-delivered  it did not. Back to pending, due at the next drain.

    There is no default, on purpose. Guessing is the one thing this command exists to prevent.
    """
    if delivered is None:
        typer.echo(
            "say what the provider showed you: --delivered or --not-delivered. There is no "
            "default - the whole reason this intent is parked is that nobody knows, and a wrong "
            "guess either messages a real person twice or silently drops a message they never got.",
            err=True,
        )
        raise typer.Exit(code=2)

    outcome = asyncio.run(
        run_resolve_unknown_intent(_sessionmaker(), intent_id=intent_id, delivered=delivered)
    )
    if outcome is ResolveOutcome.NOT_FOUND:
        typer.echo(f"no outbox intent {intent_id}", err=True)
        raise typer.Exit(code=1)
    if outcome is ResolveOutcome.NOT_UNKNOWN:
        typer.echo(
            f"outbox intent {intent_id} is not parked as unknown, so there is nothing to resolve.",
            err=True,
        )
        raise typer.Exit(code=1)
    if outcome is ResolveOutcome.RECORDED_AS_SENT:
        typer.echo(
            f"outbox intent {intent_id} recorded as SENT: the ledger row is written, so it will "
            "not be re-sent and the recipient's daily cap counts it."
        )
        return
    typer.echo(f"outbox intent {intent_id} requeued (unknown -> pending, attempts reset)")


@guest_app.command("purge")
def guest_purge_command(
    tenant: Annotated[
        str,
        typer.Option(
            help="Slug of the business to erase the guest FROM. Required — a purge is never "
            "instance-wide."
        ),
    ],
    email: Annotated[str, typer.Option(help="The guest's email address.")],
) -> None:
    """Erase a guest's personal data from ONE business (RNF-8).

    ==`--tenant` is mandatory and this command fails without it.== The CLI runs as the owner of the
    database, over every business on the instance: one person can be a guest of several of them,
    with no relationship between those bookings, so an unscoped purge would erase them from
    businesses that never received the request.

    The bookings survive, redacted — the appointments happened, they occupied their slots, and they
    are the host's history. What goes is the PERSON: their name, email, phone, consent, notes and
    answers; the queued messages that would still have reached them; their guest links; the send
    ledger; and their data inside the webhook payloads already sent to subscribers.
    """
    try:
        report = asyncio.run(run_guest_purge(_sessionmaker(), tenant_slug=tenant, email=email))
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if report.bookings == 0:
        typer.echo(
            f"no bookings for that guest in tenant {tenant!r} "
            f"({report.webhook_deliveries} webhook payload(s) redacted). Check the tenant slug.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"purged the guest from tenant {tenant!r}: "
        f"{report.bookings} booking(s) redacted, "
        f"{report.outbox_intents} queued message(s), "
        f"{report.guest_tokens} guest link(s) and "
        f"{report.sent_notifications} ledger row(s) deleted, "
        f"{report.webhook_deliveries} webhook payload(s) redacted"
    )
    if report.outbox_retained:
        # Said out loud, never left to the log. An erasure is a thing an operator may have to PROVE
        # they performed, and a row this purge deliberately KEPT is exactly what they will be asked
        # about. It names nobody — it is money owed, or a slot to free — and they should hear that
        # from the command rather than discover it in the table.
        typer.echo(
            f"  kept {report.outbox_retained} queued intent(s) that are not messages (a refund "
            "owed, a hold to expire): they name nobody, and deleting them would have kept this "
            "guest's money"
        )


if __name__ == "__main__":  # pragma: no cover
    app()
