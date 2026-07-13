"""The ``aethercal-admin`` CLI (F1-11): create a tenant + first user, issue API keys.

The Typer commands are thin: they read env-sourced :class:`Settings`, build a sessionmaker, and
delegate to the ``run_*`` coroutines, which own a single transaction each. Those coroutines are the
testable seam — the offline suite drives them against an aiosqlite sessionmaker, proving a
CLI-issued key verifies through the same service the API uses.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from cryptography.fernet import Fernet
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.models import ApiKey, Outbox, OutboxStatus, Tenant
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
from aethercal.server.services.privacy import PurgeReport, purge_guest
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
            build_sessionmaker(build_async_engine(settings.database_config())),
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


if __name__ == "__main__":  # pragma: no cover
    app()
