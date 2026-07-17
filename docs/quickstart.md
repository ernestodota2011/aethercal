# Quickstart — self-host AetherCal and book a test appointment

From a clean machine to a real, confirmed booking. AetherCal runs as **one image, four processes
plus PostgreSQL**, configured entirely by environment variables.

Nearly all the elapsed time is the first `docker compose up --build`, which compiles the image;
every step after it takes seconds.

**You need:** Docker with the Compose plugin, and this repository.

---

## 1. Configure

```bash
git clone https://github.com/ernestodota2011/aethercal.git
cd aethercal/deploy
cp .env.example .env
```

Generate the app secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`:

- `AETHERCAL_APP_SECRET` — paste the value you just generated.
- `POSTGRES_PASSWORD` — the **bootstrap superuser** of the PostgreSQL container. AetherCal never
  connects as it: it is the identity you create the three roles with, in step 2.
- **The three role passwords.** AetherCal runs as three different PostgreSQL users, each with its own
  URL in `.env`: `AETHERCAL_DATABASE_URL` (`aethercal_app` — the API and admin, subject to row-level
  security), `AETHERCAL_OWNER_DATABASE_URL` (`aethercal_owner` — the migrations and the CLI) and
  `AETHERCAL_WORKER_DATABASE_URL` (`aethercal_worker` — the background worker). Choose a password for
  each and paste it into its URL.

> **Why three?** One business's data is kept away from another's **by the database**, not by everyone
> remembering to filter their queries. That only works if the process serving requests is *not* the
> one that owns the tables — an owner sails straight through its own policies. Hence three users and
> three URLs. There is deliberately no fallback between them: a URL pointing at the wrong user would
> not raise an error, it would silently read nothing — so each process asks the database who it is at
> boot and refuses to start on the wrong answer.

Everything else has a working default. SMTP and Google are optional: leave them blank and the app
still boots — bookings work, they just skip the confirmation email and the calendar busy-check.

## 2. Create the three database roles

Once, before the first full boot. `docker compose` reads `.env` automatically for the *containers* —
but the command below runs `psql` from **your own shell**, which has never seen that file. Load it
first:

```bash
set -a; source .env; set +a
```

Start PostgreSQL on its own and run the shipped script:

```bash
docker compose up -d postgres

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v pw_owner='<the owner password from .env>' \
  -v pw_app='<the app password from .env>' \
  -v pw_worker='<the worker password from .env>' \
  < sql/provision_roles.sql
```

This is a human step rather than a migration, for a dull reason with a sharp edge: creating a user
allowed to bypass the security policies requires a superuser, and the migrations do not run as one.

## 3. Start

```bash
docker compose up --build
```

Four processes come up out of the one image: a one-shot `migrate` (which brings the schema to head,
as the owner), then the `app`, the `worker` — reminders and outbound webhooks; **it is the process
that actually sends** — and the public `booking` page. In another terminal:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

## 4. Create a tenant and an API key

```bash
docker compose exec app aethercal-admin create-tenant \
  --slug demo --name "Demo Clinic" --email owner@example.com --timezone America/New_York
# tenant_id=035683a9-...
# user_id=54639215-...        <- this user is the host

docker compose exec app aethercal-admin issue-api-key --tenant-slug demo --name quickstart
# ack_....
```

The key is printed **once**. It is stored hashed and cannot be read back — copy it now.

```bash
export AETHERCAL_KEY="ack_...."                        # the key just printed
export AETHERCAL_URL="http://localhost:8000/api/v1"
export HOST_ID="54639215-..."                          # the user_id from create-tenant
```

## 5. Define when you are available

A **schedule** is a weekly pattern. The weekday keys are `0` = Monday … `6` = Sunday.

```bash
curl -X POST "$AETHERCAL_URL/schedules/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "name": "Weekdays 9-5",
        "timezone": "America/New_York",
        "rules": {
          "0": [{"start": "09:00", "end": "17:00"}],
          "1": [{"start": "09:00", "end": "17:00"}],
          "2": [{"start": "09:00", "end": "17:00"}],
          "3": [{"start": "09:00", "end": "17:00"}],
          "4": [{"start": "09:00", "end": "17:00"}]
        }
      }'
```

Keep the returned `id` as `SCHEDULE_ID`.

## 6. Define what can be booked

An **event type** is a bookable meeting: a duration, a host, and a schedule.

```bash
curl -X POST "$AETHERCAL_URL/event-types/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "host_id": "'"$HOST_ID"'",
        "schedule_id": "'"$SCHEDULE_ID"'",
        "slug": "intro-call",
        "title": "Intro call",
        "duration_seconds": 1800,
        "max_advance_seconds": 2592000
      }'
```

Keep the returned `id` as `EVENT_TYPE_ID`. (`max_advance_seconds` is how far ahead guests may
book — 30 days here.)

## 7. Ask for the free slots

`from`/`to` must be a window that has not already passed, so compute it instead of typing a fixed
date — a hardcoded window is the one thing in this guide that goes stale on its own:

```bash
curl -G "$AETHERCAL_URL/slots/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" \
  --data-urlencode "event_type=$EVENT_TYPE_ID" \
  --data-urlencode "from=$(date -u +%Y-%m-%d)" \
  --data-urlencode "to=$(date -u -d '+7 days' +%Y-%m-%d)" \
  --data-urlencode "tz=America/New_York"
```

(macOS ships BSD `date`, which has no `-d`: `brew install coreutils` and use `gdate`, or just type a
`to` a week or so out.)

```json
{
  "availability": "ok",
  "slots": [
    {"start": "2026-07-20T13:00:00Z", "end": "2026-07-20T13:30:00Z"},
    {"start": "2026-07-20T13:30:00Z", "end": "2026-07-20T14:00:00Z"}
  ]
}
```

The dates above are illustrative — yours will fall inside the window you asked for. Slot bounds are
**UTC**; `tz` is only the zone you asked in (9:00 in New York is 13:00Z in July).

`availability` is `ok` when the external busy set was known and complete for that window. It is
`unavailable` when a connected calendar could not be reached — and then AetherCal deliberately
offers **no** slots for that host rather than risk a double-booking.

## 8. Book it

Pick one of the `start` values **your** step 7 response just returned — not the example above, which
is already in the past by the time you read this — and export it:

```bash
export SLOT_START="2026-07-20T13:00:00Z"   # a "start" from YOUR step 7 response
```

```bash
curl -X POST "$AETHERCAL_URL/bookings/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "event_type_id": "'"$EVENT_TYPE_ID"'",
        "start": "'"$SLOT_START"'",
        "guest_name": "Jane Doe",
        "guest_email": "jane@example.com",
        "guest_timezone": "America/New_York"
      }'
```

```json
{
  "id": "5a13f24c-8e79-4240-b661-b8d4846fe01a",
  "event_type_id": "a15dfe22-9146-4e11-9e04-3b6cf1e57742",
  "start": "2026-07-20T13:00:00Z",
  "end": "2026-07-20T13:30:00Z",
  "status": "confirmed",
  "guest_name": "Jane Doe",
  "guest_email": "jane@example.com",
  "guest_timezone": "America/New_York",
  "guest_notes": null,
  "answers": {},
  "meeting_url": null,
  "rescheduled_from_id": null,
  "cancelled_at": null,
  "created_at": "2026-07-20T12:58:04Z"
}
```

That is a real appointment. `start`/`end` come back with the `Z` (UTC) suffix, same as the slots
response above — every timestamp in the API is UTC. POST the same `SLOT_START` again and AetherCal
answers **`409 Conflict`** — the conflict is decided by the database, not by a race in the
application.

## 9. The booking page

The public booking page runs as its own service in the same compose file, on
<http://localhost:5001>. Put the API key into `AETHERCAL_API_KEY` in `.env` (that is the server-side
key the page presents on the guest's behalf — the guest never sees a key), restart, and the event
type is bookable at `/e/intro-call`.

To put it on your own site instead, see [embedding](embedding.md).

---

## Where to go next

- **[Python SDK](sdk.md)** — the same flow in a few lines of Python, with a runnable
  [example](../examples/sdk/).
- **[Calendar component](calendar-component.md)** — render the bookings in a real calendar.
- **[Webhooks](webhooks.md)** — be notified when a booking is created, cancelled or rescheduled.
  Read the **at-least-once** contract before you write a handler.
- **[BYOK credentials](byok-credentials.md)** — give an event type a price with a business's own
  Stripe or Mercado Pago account, before you take a real charge.
- **[deploy/README.md](../deploy/README.md)** — every setting, the rule that the worker must run
  in exactly one process, and hardening the admin surface.

## Troubleshooting

**`AETHERCAL_DATABASE_URL is not set`.** The container is running without your `.env`. Run
`docker compose up` from the `deploy/` directory, where that file lives.

**`psql: FATAL: role "..." does not exist` in step 2.** You ran that command without loading `.env`
into this shell first. `docker compose` reads it for the containers; your own shell never saw
`$POSTGRES_USER` / `$POSTGRES_DB` until you run `set -a; source .env; set +a`.

**A process refuses to start, naming a role.** A message like *"AETHERCAL_DATABASE_URL connects as
PostgreSQL role 'x', but this engine must run as 'aethercal_app'"* means a URL points at the wrong
user. That refusal is the feature: under row-level security the wrong user does not raise, it simply
reads nothing — so each process asks the database who it is, and stops.

**`migrate` exits non-zero, or the app never starts.** The one-shot `migrate` runs as
`aethercal_owner`; if that role does not exist, step 2 was skipped or ran against a different
database. `docker compose logs migrate` says which.

**The app cannot reach PostgreSQL.** Each of the three URLs carries its own password, and each must
match byte-for-byte the one you passed to `provision_roles.sql` in step 2. They are separate
variables and nothing checks that they agree.

**Bookings work, but no email or webhook ever arrives.** Those are sent by the `worker` process, not
by the API. `docker compose ps worker` — if it is not running, nothing will ever be delivered, and
the API will keep looking perfectly healthy while it does not happen.

**A slot you expected is missing.** The usual causes: the day is outside the schedule's weekly
rules; the slot falls inside `min_notice_seconds`; it is beyond `max_advance_seconds`; or the host
is already busy then — AetherCal treats a host as busy across *all* of their event types, so a
booking on one event type blocks the same time on another.

**Everything returns `401`.** The API key goes in an `Authorization: Bearer <key>` header. Keys are
not recoverable: if you lost it, issue another one (`aethercal-admin issue-api-key`) and revoke the
old one (`aethercal-admin keys revoke`).
