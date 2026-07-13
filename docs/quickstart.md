# Quickstart — self-host AetherCal and book a test appointment

From a clean machine to a real, confirmed booking. AetherCal runs as **one container plus
PostgreSQL**, configured entirely by environment variables.

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

Two values are required. Generate the app secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`:

- `AETHERCAL_APP_SECRET` — paste the value you just generated.
- `POSTGRES_PASSWORD` — pick a strong password, and put the **same** value into the password field
  of `AETHERCAL_DATABASE_URL` (`postgresql://aethercal:<password>@postgres:5432/aethercal`).

Everything else has a working default. SMTP and Google are optional: leave them blank and the app
still boots — bookings work, they just skip the confirmation email and the calendar busy-check.

## 2. Start

```bash
docker compose up --build
```

The app waits for PostgreSQL to be healthy, runs the migrations, then serves. In another terminal:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

## 3. Create a tenant and an API key

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

## 4. Define when you are available

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

## 5. Define what can be booked

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

## 6. Ask for the free slots

```bash
curl -G "$AETHERCAL_URL/slots/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" \
  --data-urlencode "event_type=$EVENT_TYPE_ID" \
  --data-urlencode "from=2026-07-13" \
  --data-urlencode "to=2026-07-20" \
  --data-urlencode "tz=America/New_York"
```

```json
{
  "availability": "ok",
  "slots": [
    {"start": "2026-07-13T13:00:00Z", "end": "2026-07-13T13:30:00Z"},
    {"start": "2026-07-13T13:30:00Z", "end": "2026-07-13T14:00:00Z"}
  ]
}
```

Slot bounds are **UTC**; `tz` is the display zone you asked in (9:00 in New York is 13:00Z in July).

`availability` is `ok` when the external busy set was known and complete for that window. It is
`unavailable` when a connected calendar could not be reached — and then AetherCal deliberately
offers **no** slots for that host rather than risk a double-booking.

## 7. Book it

```bash
curl -X POST "$AETHERCAL_URL/bookings/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "event_type_id": "'"$EVENT_TYPE_ID"'",
        "start": "2026-07-13T13:00:00Z",
        "guest_name": "Jane Doe",
        "guest_email": "jane@example.com",
        "guest_timezone": "America/New_York"
      }'
```

```json
{
  "id": "5a13f24c-8e79-4240-b661-b8d4846fe01a",
  "status": "confirmed",
  "start": "2026-07-13T13:00:00",
  "end": "2026-07-13T13:30:00"
}
```

That is a real appointment. POST the same slot again and AetherCal answers **`409 Conflict`** — the
conflict is decided by the database, not by a race in the application.

## 8. The booking page

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
- **[deploy/README.md](../deploy/README.md)** — every setting, the rule that the scheduler must run
  in exactly one process, and hardening the admin surface.

## Troubleshooting

**`AETHERCAL_DATABASE_URL is not set`.** The container is running without your `.env`. Run
`docker compose up` from the `deploy/` directory, where that file lives.

**The app cannot reach PostgreSQL.** The password inside `AETHERCAL_DATABASE_URL` must be
byte-for-byte the `POSTGRES_PASSWORD` in the same file. They are two separate variables and nothing
checks that they agree.

**A slot you expected is missing.** The usual causes: the day is outside the schedule's weekly
rules; the slot falls inside `min_notice_seconds`; it is beyond `max_advance_seconds`; or the host
is already busy then — AetherCal treats a host as busy across *all* of their event types, so a
booking on one event type blocks the same time on another.

**Everything returns `401`.** The API key goes in an `Authorization: Bearer <key>` header. Keys are
not recoverable: if you lost it, issue another one (`aethercal-admin issue-api-key`) and revoke the
old one (`aethercal-admin keys revoke`).
