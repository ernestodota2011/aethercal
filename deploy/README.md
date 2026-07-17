# Deploying AetherCal (self-host)

AetherCal ships as **one image, four processes + PostgreSQL**, configured entirely by environment
variables (RF-19 — no secrets in source): a one-shot `migrate`, the `app` (API + admin), the `worker`
(outbox drain, outbound-webhook delivery, calendar busy-cache refresh, `/metrics`), and the public
`booking` page. The split is not cosmetic — see
[The three database roles](#the-three-database-roles--create-them-before-the-first-boot) and
[The worker](#the-worker-exactly-one-process).

## Quickstart

The guided version — configure, boot, and book a real test appointment — is
[`docs/quickstart.md`](../docs/quickstart.md). What follows is the operator's reference.

Requires Docker with the Compose plugin.

```bash
cd deploy
cp .env.example .env

# 1. Fill in .env:
#   AETHERCAL_APP_SECRET   — python -c "import secrets; print(secrets.token_urlsafe(48))"
#   POSTGRES_PASSWORD      — the BOOTSTRAP superuser of the postgres container. The application
#                            never connects as it; it is the identity you provision the roles with.
#   the THREE role passwords, each pasted into its own URL:
#     AETHERCAL_DATABASE_URL         → aethercal_app     (the API + admin, under RLS)
#     AETHERCAL_OWNER_DATABASE_URL   → aethercal_owner   (Alembic + the CLI)
#     AETHERCAL_WORKER_DATABASE_URL  → aethercal_worker  (the worker's scan pool)

# 2. Start PostgreSQL alone and CREATE THE THREE ROLES. A human runbook step, once — and it cannot
#    be automated away: `CREATE ROLE ... BYPASSRLS` needs SUPERUSER, so Alembic (which runs as the
#    owner) cannot mint them, and an init script in the postgres image only ever fires on an EMPTY
#    volume — i.e. perfectly in CI, and never on the instance that actually matters.
docker compose up -d postgres

# `.env` is read by docker-compose, NOT by your shell — so $POSTGRES_USER and $POSTGRES_DB are
# empty here unless you load it yourself. Skip this line and psql connects as your own username,
# failing with `role "<you>" does not exist`: an error that sends you to debug a database that is
# perfectly fine.
set -a; source .env; set +a

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v db="$POSTGRES_DB" -v pw_owner=... -v pw_app=... -v pw_worker=... \
    < sql/provision_roles.sql

# 3. Everything else. `migrate` runs to completion as the owner; `app` and `worker` wait on it.
docker compose up --build -d
```

Migrations no longer run on the app's boot — `AETHERCAL_AUTO_MIGRATE` is a retired tripwire that now
**fails the boot**. The one-shot `migrate` service runs `aethercal-admin db upgrade` as
`aethercal_owner`, and both `app` and `worker` wait for it to complete. The app then **refuses to
serve a schema behind head**, so "running against a stale schema" is not a state it can reach.

Verify it is up:

```bash
curl http://localhost:8000/api/v1/health      # -> {"status":"ok"}
```

Open the API at `http://localhost:8000` (change the published port with `HOST_PORT` in `.env`).
Create a tenant and issue an API key with the bundled admin CLI:

```bash
docker compose exec app aethercal-admin --help
```

> The public booking **page** (`apps/booking`, FastHTML) runs as its own `booking` service in the
> compose file: the same image with a different command (`python -m aethercal.booking`), reaching the
> API over the compose network. It is published on `http://localhost:5001` (change the port with
> `BOOKING_HOST_PORT` in `.env`) — and it is the only surface a reverse proxy should expose publicly
> (`book.<domain>`).

## Running without Docker

The image just runs the app factory under uvicorn. The equivalent bare-metal command is:

```bash
uvicorn --factory aethercal.server.app:create_app_from_env --host 0.0.0.0 --port 8000
# or the console script (reads AETHERCAL_HOST / AETHERCAL_PORT):
aethercal-serve
```

Export the same `AETHERCAL_*` variables from `.env.example` first (and point
`AETHERCAL_DATABASE_URL` at a reachable PostgreSQL).

## Configuration

Every setting is an `AETHERCAL_*` variable — see [`.env.example`](./.env.example) for the full list
with per-line comments. The essentials:

| Variable | Required | Purpose |
|---|---|---|
| `AETHERCAL_DATABASE_URL` | yes | PostgreSQL URL (psycopg driver auto-normalized) |
| `AETHERCAL_APP_SECRET` | yes | Signs guest tokens + derives the credential-encryption key |
| `AETHERCAL_RUN_SCHEDULER` | — | Run the background scheduler in **this** process (see below) |
| `AETHERCAL_AUTO_MIGRATE` | — | **RETIRED.** A truthy value now FAILS the boot; the one-shot `migrate` service owns migrations. See below. |
| `AETHERCAL_BOOKING_BASE_URL` | — | Public base for guest cancel/reschedule links |
| `AETHERCAL_TENANT_SLUG` | for `/e/…` | The business the **booking page** serves when the URL does not name one — your `create-tenant --slug`. Blank → the page has no default and answers every unprefixed `/e/{slug}` with **404** (only `/t/{slug}/e/{slug}` works), while `/healthz` still reports 200 |
| `AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS` | — | Origins allowed to iframe `/embed/*` (the [embeddable widget](../docs/embedding.md)). Blank → `*` (any origin) |
| `AETHERCAL_BOOKING_TRUSTED_PROXIES` | — | CIDRs of reverse proxies trusted to set `CF-Connecting-IP` for the rate limiter. Blank → use the transport peer address |
| `AETHERCAL_SMTP_*` | — | Transactional email — the instance **relay**, lent to any business with no SMTP of its own (absent → email skipped, app still boots) |
| `AETHERCAL_GOOGLE_*` | — | Google Calendar busy-check + Meet (absent → skipped) |
| `AETHERCAL_WHATSAPP_*` | — | The **operator's** WhatsApp (Evolution API). Off unless set. ⚠️ A business does **not** send from this — it sends from its own credential, or not at all. **Read [phone channels](../docs/phone-channels.md) first** |
| `AETHERCAL_SMS_*` | — | The **operator's** SMS (Twilio). Same rule as WhatsApp — **read [phone channels](../docs/phone-channels.md) first** |
| `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY` | `false` | Single-business self-hosts: lend the operator's WhatsApp/SMS account to a business that brought none. Warned at boot when on. Leave off on any multi-business instance ([BYOK](../docs/byok-credentials.md#the-rule-and-where-it-is-asymmetric)) |

Unconfigured SMTP or Google **never** hard-fails boot: those effects degrade gracefully (a booking
still succeeds, it just skips the email / calendar sync).

> [!WARNING]
> **The phone channels are different, and there is a decision to make before you enable one.** A
> phone channel refuses to activate without its daily caps (`*_DAILY_CAP_PER_PHONE` /
> `*_DAILY_CAP_PER_IP` — fail-closed by design). More importantly: the guest's number is typed into
> a **public** form, and **nothing verifies that it belongs to the person booking**. The consent
> checkbox proves somebody ticked it — not that the number's **owner** agreed. So a stranger can
> make your business message a third party, under your brand. Verifying possession of the number is
> a **declared gap**.
> ==Read [docs/phone-channels.md](../docs/phone-channels.md) before you switch one on.==

## The admin surface (`/admin`)

The single-user Reflex admin mounts at `/admin` on the API app **only** when both
`AETHERCAL_ADMIN_ENABLED=1` and admin credentials are configured (it is a no-op otherwise). Keep the
API container (and therefore `/admin`) on an **internal** interface — do not expose it publicly for a
shadow/MVP deployment; only the public booking page (`book.<domain>`) needs a route.

> [!IMPORTANT]
> If you ever expose `/admin` publicly, the in-app login lockout is a *secondary* layer only (it is
> per-session and can be reset by opening a new session). The **primary** brute-force defense must be
> per-IP rate-limiting + fail2ban on the reverse proxy in front of `/admin`. The stored password is
> hashed with a slow PBKDF2, but that is not a substitute for proxy-level rate limiting.

## The three database roles — create them BEFORE the first boot

AetherCal isolates the businesses sharing one instance with PostgreSQL **row-level security**. That
needs three roles, three URLs, and one thing you have to do by hand, exactly once:

| Role | URL | Who connects as it | RLS |
|---|---|---|---|
| `aethercal_app` | `AETHERCAL_DATABASE_URL` | the API + the admin (`app`) | **subject to it** |
| `aethercal_owner` | `AETHERCAL_OWNER_DATABASE_URL` | Alembic + the CLI (`migrate`) | owns the tables; `BYPASSRLS` |
| `aethercal_worker` | `AETHERCAL_WORKER_DATABASE_URL` | the worker's *scan* pool only | `BYPASSRLS` |

```sh
psql "$SUPERUSER_URL" -v ON_ERROR_STOP=1 -v db=aethercal \
     -v pw_owner=... -v pw_app=... -v pw_worker=... \
     -f deploy/sql/provision_roles.sql
```

> [!IMPORTANT]
> `CREATE ROLE ... BYPASSRLS` needs **superuser**, so it cannot live in a migration (Alembic runs as
> the owner). It must not live in the `postgres` image's init scripts either: those only run when the
> data volume is **empty**, so they would work perfectly in CI and never run on the instance that
> matters. It is a runbook step, executed once, with the smoke output pasted back.

The two new URLs are **fail-closed**: without the worker's, the worker does not start; without the
owner's, the CLI and Alembic do not run. There is no fallback to `AETHERCAL_DATABASE_URL`, and that is
deliberate — under RLS a connection on the wrong role does not *fail*, it reads **zero rows**. A CLI
that quietly fell back would run `guest purge --tenant X` against nothing and exit **0**: erasure of
personal data, reporting success, having erased nothing. So every process runs `SELECT current_user`
on every engine it builds, at startup, and **refuses to boot** on the wrong answer.

## Migrations: a one-shot step, as the owner

`AETHERCAL_AUTO_MIGRATE` is **retired**, and a truthy value now fails the boot. It used to run Alembic
inside the web process, on the web process's own URL — which is exactly why the app had to be the
table owner, and therefore why row-level security on this product would have been a placebo.

Compose runs the `migrate` service (`aethercal-admin db upgrade`) to completion first; `app` and
`worker` both wait on it. The web process then **refuses to serve a schema behind head**, so "running
on a stale schema" is not a state this deployment can reach.

## The worker: exactly ONE process

> [!IMPORTANT]
> `aethercal-worker` must run in **exactly one process** across the whole deployment. The compose file
> pins `replicas: 1`, and that line is load-bearing.

The worker drains the transactional outbox, delivers the outbound webhooks, and refreshes the busy
caches of the connected calendars. It is a **separate process**, not a flag: `AETHERCAL_RUN_SCHEDULER`
is retired and now fails the boot, because it never separated anything — it left the API and the admin
mounted and bound every tick to the *request path's* sessionmaker. A background tick carries no
request, therefore no bound business, therefore an empty GUC — and under RLS an empty GUC selects
**zero rows**. Every outbound effect would have stopped, in silence.

It holds two pools: a `BYPASSRLS` one to *find* work whose business cannot be known until the row has
been read, and the app role to *execute* each item under RLS, bound to that item's own business.

**Why exactly one matters:** the webhook delivery and the busy-cache refresh carry no claim/lease.
Only `outbox` has the columns to hold one (`claimed_by`, `lease_expires_at`), and adding them to the
other tables would have meant new DDL in the batch every other wave is queued behind. What makes going
without one safe is precisely this invariant. **Run two workers and those two passes will double-send.**

The worker also serves the operator surface — `GET /metrics` and the `GET /health/ready` that reports
the outbox backlog (port `8001`, guarded by `AETHERCAL_METRICS_TOKEN`; unset means the endpoint is
CLOSED, not open). Both used to be served by the web process, where, under RLS, they could only ever
have reported **zeros**: a permanently green readiness probe over a permanently burning queue.

## Notes for the integrator / CI

- **The Dockerfile is build-verified in CI.** The `docker-build` job runs
  `docker build -f deploy/Dockerfile -t aethercal-server .` from the **repository root** on every
  push and pull request, so a broken image fails the build. Build it the same way by hand — the
  context must be the repo root (it needs `uv.lock` + every workspace member).
- The build re-creates the venv from `uv.lock` inside the image; `.dockerignore` keeps the host
  `.venv` and caches out of the context.
- Data persists in the `aethercal-pgdata` named volume. Back it up like any PostgreSQL data dir.

## Rollback and restore

Two different things fail, and they roll back differently.

**A bad deploy (new image, same schema).** Migrations are forward-only (expand-and-contract, see
[Migrations](#migrations-a-one-shot-step-as-the-owner)), so a new release rarely changes the schema
in a way the previous image cannot read. To roll back, redeploy the previous image tag:

```bash
# Pin the image you are deploying (never :latest in production) so "the previous one" is a real tag.
docker compose pull      # or: docker compose up -d with the older tag in .env / compose
docker compose up -d app worker booking
```

Do **not** run the previous release's `migrate` to "downgrade" the schema. Alembic downgrades exist
and are tested, but production is forward-only by design: a downgrade that drops a column the newer
rows still need loses data. If a release shipped a migration you must undo, treat it as a
**roll-forward** — a new migration that reverses it — not a downgrade against live data.

**Data loss or corruption (restore from backup).** The whole database lives in the `aethercal-pgdata`
volume. Take logical backups on a schedule and test the restore — a backup you have never restored is
a hope, not a backup:

```bash
# Back up (logical dump — portable across PostgreSQL patch versions):
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > aethercal-$(date +%F).dump

# Restore into a FRESH database (never over a live one). Stop the app/worker first so nothing writes:
docker compose stop app worker booking
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < aethercal-YYYY-MM-DD.dump
docker compose up -d
```

After a restore, the running image may be **ahead of** the restored schema. That is the one case the
`migrate` service is for on a rollback: it brings the restored database up to the image's head before
`app`/`worker` are allowed to serve (they refuse a schema behind head, so this is enforced, not
optional). The three database roles are **not** in a logical dump — re-create them first if you
restored into a brand-new PostgreSQL instance (see
[The three database roles](#the-three-database-roles--create-them-before-the-first-boot)).
