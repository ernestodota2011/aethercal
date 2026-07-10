# Deploying AetherCal (self-host)

AetherCal ships as **one container + PostgreSQL**, configured entirely by environment variables
(RF-19 — no secrets in source). Migrations run automatically on boot, and the in-process scheduler
(reminders, outbound-webhook delivery, calendar busy-cache refresh) runs inside the app container.

## Quickstart (< 10 minutes on a clean machine)

Requires Docker with the Compose plugin.

```bash
cd deploy
cp .env.example .env

# Set the two required secrets (edit .env, or generate the app secret):
#   AETHERCAL_APP_SECRET   — python -c "import secrets; print(secrets.token_urlsafe(48))"
#   POSTGRES_PASSWORD      — pick a strong password, and put the SAME value into the password
#                            field of AETHERCAL_DATABASE_URL (…://aethercal:<pw>@postgres:5432/…)

docker compose up --build       # builds the image, starts Postgres, then the app
```

On startup the app waits for Postgres to be healthy, runs the Alembic migrations, then serves.
Verify it is up:

```bash
curl http://localhost:8000/api/v1/health      # -> {"status":"ok"}
```

Open the booking surface / API at `http://localhost:8000` (change the published port with
`HOST_PORT` in `.env`). Create a tenant and issue an API key with the bundled admin CLI:

```bash
docker compose exec app aethercal-admin --help
```

> The public booking **page** (`apps/booking`, FastHTML) lands in a later F1 wave; this unit ships
> the API + the background scheduler. The health endpoint above is the "it's up" check today.

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
| `AETHERCAL_AUTO_MIGRATE` | — | Run migrations on boot (default on) |
| `AETHERCAL_BOOKING_BASE_URL` | — | Public base for guest cancel/reschedule links |
| `AETHERCAL_SMTP_*` | — | Transactional email (absent → email skipped, app still boots) |
| `AETHERCAL_GOOGLE_*` | — | Google Calendar busy-check + Meet (absent → skipped) |

Unconfigured SMTP or Google **never** hard-fails boot: those effects degrade gracefully (a booking
still succeeds, it just skips the email / calendar sync).

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

## The scheduler: exactly one process

> [!IMPORTANT]
> The background scheduler must run in **exactly one process** across the whole deployment.

The scheduler fires per-booking reminders (persisted in a PostgreSQL jobstore so they survive a
restart) and runs the recurring webhook-delivery + busy-cache-refresh jobs. Running it in more than
one process means duplicate ticks and duplicate reminder pollers.

- **Single container (the default here):** the image sets `AETHERCAL_RUN_SCHEDULER=1` and runs one
  uvicorn worker — the scheduler and the request path share that one process. Nothing to do.
- **Scaling out:** if you run multiple uvicorn workers or replicas, keep `AETHERCAL_RUN_SCHEDULER=1`
  on **one** of them and set it to `0` on every other. Note that reminders are *scheduled* into the
  jobstore only by a process whose scheduler is started, so in a scaled-out topology run the web
  tier and the single scheduler in the **same** process (one worker) or give the scheduler process
  its own path to the booking flow. For the MVP, one container is the supported, tested shape.

## Notes for the integrator / CI

- **The Dockerfile is not build-verified in this change** (Docker was unavailable on the authoring
  machine). CI / the integrator should run `docker build -f deploy/Dockerfile -t aethercal-server .`
  from the **repository root** (the build context needs `uv.lock` + every workspace member).
- The build re-creates the venv from `uv.lock` inside the image; `.dockerignore` keeps the host
  `.venv` and caches out of the context.
- Data persists in the `aethercal-pgdata` named volume. Back it up like any PostgreSQL data dir.
