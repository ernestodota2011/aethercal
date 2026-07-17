# Design: workflows, the connected calendar, the resource timeline, and the debt backlog

Status: approved for implementation · Scope: **Batch A**

## Why this is Batch A

An earlier draft bundled this work with **payments** and **multi-tenant row-level security** and an **unauthenticated public API**. Design review rejected it four times, and the defects clustered almost entirely in that trio — three coupled problems that amplify each other's failure modes. Everything else drew no architectural objection at all.

So the work was split. **Batch A** is everything that touches neither money nor tenant isolation. **Batch B** — payments, multi-business RLS/RBAC, and the public-API auth change — gets its own design and its own review.

## The rule that governs this design: the silent no-op

Every serious defect review caught shares one signature: **the system does nothing, raises no error, and every test passes.**

- Row-level security that protects nothing, because the application connects as the **owner** of its tables — and in Postgres the owner **bypasses RLS**.
- A reminder that fires **twice**, because two schedulers are live and their idempotency barriers do not know about each other.
- An effect marked `delivered` and never sent, because a staleness guard returns `False` for exactly the case the effect exists to handle.
- A drainer that drains **zero rows** and reports nothing.

**Therefore:** anything whose absence or misconfiguration produces "nothing happens, no error" is **fail-closed**, and its test asserts the **effective state** — the real role of the real connection, the real row that was written — never the apparent behaviour.

---

## Scope

- **RF-24** — notification rules (trigger + offset) with per-channel actions (email, WhatsApp, SMS), templates per language, **each step exactly once per booking**.
- **RF-25** — no-show: transition, webhook, metrics.
- **RF-10** (re-opened) — the 24-hour reminder is absorbed into the workflow engine; the second scheduler is retired.
- **RF-11 / RF-12 / RF-13** (re-opened) — `api/bookings.py:122` states *"Google sync is not wired yet"*. **A booking has never created an event in the host's calendar.** These are P1 requirements, ticked as done, and untrue.
- **RF-30** — multi-host. Availability is *already* computed per host; what is missing is `schedules.user_id`, a silent `.first()` that drops a host's second connection, and a dead `external_calendar_links` table.
- **RF-28** — resource timeline in the calendar component (the resource **is** the host).
- **RNF-8** — guest data erasure, covering the PII this batch introduces.
- **Debt** — R8, R9, end-to-end tests, documentation, and the first public release.

## Architecture

The transactional outbox doubles as the durable scheduler. No broker, no worker queue: the self-host stays **one unit + one database**, which RNF-9 and the ten-minute quickstart both depend on. A workflow step is an outbox row whose `next_retry_at` is its send time, and the existing `UniqueConstraint(tenant_id, booking_id, dedupe_key)` (`db/models/outbox.py:52`) is what makes RF-24's "exactly once" true.

**Stack:** Python 3.11–3.13, FastAPI, SQLAlchemy 2.0 + Alembic, Postgres 16, Reflex (admin), FastHTML (booking page), TypeScript + React (calendar component), pytest + Hypothesis, vitest + fast-check, Playwright.

---

## Contracts

### Migration `0005` (the only migration of this batch — one owner, serial)

| Table / column | Notes |
|---|---|
| `workflows` | `tenant_id`, `event_type_id` (nullable = all), `name`, `trigger`, `offset_minutes`, `active` |
| `workflow_steps` | `tenant_id`, `workflow_id`, `channel`, `kind`, `position` |
| `workflow_templates` | `tenant_id`, `channel`, `kind`, `locale`, `subject` (nullable), `body` |
| `bookings.guest_phone` | E.164, nullable |
| `bookings.guest_phone_consent_at` | **Persist the consent, or it did not happen.** A checkbox whose answer is discarded is not consent |
| `bookings.no_show_at` | |
| `bookings.status` CHECK | widened for `no_show` |
| `schedules.user_id` | nullable FK → `users`. **Required by RF-30**; without it two hosts silently share a schedule |
| `sent_notifications` | generalised (see below) |
| `outbox.claimed_by`, `outbox.lease_expires_at` | for the lease (R8) |
| seed rows | the RF-10 rule, per tenant |

Forward-only / expand: NOT NULL columns get a `server_default` so `ADD COLUMN` needs no backfill, then the default is dropped. `downgrade` is symmetric via `batch_alter_table` so it also runs on SQLite. `BookingStatus` is `native_enum=False` (VARCHAR + CHECK, `db/models/booking.py:15-23`), which avoids the `ALTER TYPE … ADD VALUE`-inside-a-transaction trap.

Update `EXPECTED_TABLES` (`apps/server/tests/db/test_models_metadata.py:23-39,54-55`) — it pins the exact table set.

### Enums

```python
# packages/aethercal-core/src/aethercal/core/model/booking.py
class BookingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"      # NEW

# apps/server/src/aethercal/server/channels.py
class Channel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"

# apps/server/src/aethercal/server/services/workflows.py
class WorkflowTrigger(StrEnum):
    ON_BOOKING = "on_booking"
    BEFORE_START = "before_start"
    AFTER_END = "after_end"
    ON_CANCEL = "on_cancel"
    ON_NO_SHOW = "on_no_show"

# apps/server/src/aethercal/server/services/outbox.py
class OutboxEffect(StrEnum):
    EMAIL = "email"     # existing
    GOOGLE = "google"   # existing
    NOTIFY = "notify"   # NEW — one workflow step on one channel
```

**`NO_SHOW` does not free the slot.** The appointment time has passed; releasing it would corrupt history and permit a retroactive booking over it. `Booking.occupies` is `status is not CANCELLED` (`core/model/booking.py:29-31`), so `no_show` occupies automatically and the partial index predicate `WHERE status <> 'cancelled'` is **unchanged**. Prove it against Postgres.

### Step lifecycle — the transition table (read this twice)

**Rescheduling does not mutate a booking. It creates a new row** (`services/bookings.py:477-529`): the successor inherits the `ical_uid`, and the predecessor is marked `CANCELLED`. The outbox uniqueness is keyed on `booking_id`, so **a successor's steps can never conflict with a predecessor's** — any design that leans on `ON CONFLICT … DO UPDATE` to "move" a step matches zero rows, raises nothing, and passes every test. That is the silent no-op, and it is why the lifecycle is an explicit table rather than an upsert:

| Transition | Steps materialised | Steps voided |
|---|---|---|
| **Confirm** | all triggers for the new booking | — |
| **Reschedule** (predecessor → successor) | fresh steps for the **successor** (plain INSERT — new `booking_id`) | **every pending step of the predecessor, including the exempt ones.** Otherwise the predecessor's `AFTER_END` follow-up still fires — at the hour of a meeting that never happened |
| **Cancel** | the `ON_CANCEL` step | every other pending step |
| **No-show** | the `ON_NO_SHOW` step | **the pending `AFTER_END` step** — otherwise the guest who did not show up receives "thanks for meeting with us" |

The mapping is exhaustive over `WorkflowTrigger` × transition. **A missing entry fails at startup** (`assert_never` / pyright exhaustiveness) — never a silent default.

**`ON_CANCEL` fires only from `cancel_booking()`, never from the internal swap that a reschedule performs.** The swap marks the predecessor `CANCELLED` as an implementation detail; hooking the trigger to the state change instead of to the operation would send a cancellation notice on **every reschedule** — and `ON_CANCEL` is exempt from the staleness guard, so it would be delivered for real.

**A `BEFORE_START` step whose send time is already in the past** (booked less than 24 h out, or rescheduled inside the window) is **skipped**, not sent: `next_retry_at` in the past drains immediately, and a "reminder" that arrives after the fact is noise. Record the skip.

### Effect staleness

`_is_chain_current()` (`services/outbox.py:450-464`) is True only when the booking is the single live member of its `ical_uid` chain. ⚠️ **For a CANCELLED booking it returns False, always** — so any effect that legitimately acts on a cancelled booking would be marked `delivered` and never sent.

| Effect / trigger | Subject to the guard? | Why |
|---|---|---|
| `EMAIL` confirmation / reschedule | Subject | Never write about a superseded booking |
| `EMAIL` cancellation | **Exempt** | Terminal (already true today) |
| `GOOGLE` | Subject | Already true today |
| `NOTIFY` from `ON_BOOKING`, `BEFORE_START` | Subject | Informational |
| `NOTIFY` from `ON_CANCEL`, `ON_NO_SHOW`, `AFTER_END` | **Exempt** | Terminal — the cancellation notice acts on a cancelled booking *by definition* |

Every exempt case ships a test proving it runs against a **CANCELLED** booking.

### Retiring the second scheduler (RF-10)

`jobs/reminders.py` runs a **separate** scheduler — `scheduler.py:330` says so in as many words — whose idempotency barrier, `SentNotification(booking, kind=REMINDER)`, knows nothing about the outbox dedupe key. Alongside a tenant's "email 24h before" rule, the guest gets **two emails**.

Remove it. RF-10 becomes a **seeded workflow rule** per tenant (`BEFORE_START`, `-1440 min`, `EMAIL`, `kind=REMINDER`). Delete the module's private engine too (`jobs/reminders.py:99` builds its own).

**`sent_notifications`:** today the unique is `(tenant_id, booking_id, kind)` (`db/models/notifications.py:29`). It becomes `(tenant_id, booking_id, kind, channel, step_id)` — a rule may legitimately send the same *kind* over email **and** WhatsApp, and a barrier on `kind` alone would silently swallow the second channel, which is precisely what RF-24 forbids.

⚠️ Rows written by the retired scheduler have `step_id` and `channel` **NULL**, so they would no longer collide with the seeded step's row — and a live booking whose reminder already went out would receive a **second** one. **Backfill those rows** with the seeded step's `channel` and `step_id` as part of `0005`. Ship the test: a live booking whose reminder already went out receives nothing.

### Dedupe key

```python
def workflow_step_dedupe_key(workflow_id, step_id, channel) -> str:
    return f"wf:{workflow_id}:{step_id}:{channel.value}"
```

Enqueue is `ON CONFLICT DO NOTHING`. **No second idempotency mechanism.**

### R8 — the outbox must not hold row locks across network I/O

`services/outbox.py:217-245` selects `FOR UPDATE SKIP LOCKED` and then performs SMTP/Google I/O **inside the same open transaction**, holding row locks and a pool connection for the length of every send. Replace with **claim** (mark `claimed`, set `lease_expires_at`, commit) → **execute outside any transaction** → **settle** (`delivered` / `failed` + backoff / `dead`), plus a recovery pass that returns expired leases to `pending`.

Declare the **lease TTL** and the **recovery interval** explicitly: a lease longer than the recovery interval turns a dead worker into hours of silence. Keep everything already proven — exponential backoff, `dead` at six attempts, `OutboxDeferred`, causal ordering (Google before email), `_is_chain_current`.

### Effect dispatch

`make_booking_effect_executor` (`services/outbox.py:505-526`) branches `if EMAIL … else GOOGLE` — the `else` **assumes** Google. Make it exhaustive over `OutboxEffect`. A new effect must never be silently treated as a Google call.

### Channels

```python
@runtime_checkable
class ChannelSender(Protocol):
    channel: Channel
    async def send(self, *, to: str, subject: str | None, body: str) -> None: ...
```

Wrap the existing `SmtpEmailSender`; do not rewrite it.

| Channel | Adapter | Verification |
|---|---|---|
| Email | existing SMTP | live (works today) |
| WhatsApp | Evolution API (self-hostable, so it serves the self-hoster too) | one live send to an operator-owned number |
| SMS | Twilio | contract tests against the documented API — **no account: shipped unverified-live, and documented as such** |

> ⚠️ **The recipient is untrusted input.** The phone number comes from the **public booking form**: anyone can book with a stranger's number and make the system message them. The messaging account you connect may be one other systems of yours depend on, and a spam complaint against it is not recoverable.
>
> WhatsApp and SMS are therefore **fail-closed**: a channel refuses to activate unless its **per-IP and per-phone daily caps** are configured. Opt-out is honoured. The channel is off by default — which here means simply "no environment variables configured"; there is no separate `_ENABLED` flag.
>
> **The channel's base URL is operator configuration**, read only from the environment. It must never be derived from inbound data — which is why it legitimately bypasses the SSRF guard that protects user-configured webhook URLs.

### Templates — the body is data, never instructions

Allowlist: `{{guest_name}}` `{{guest_email}}` `{{event_title}}` `{{start_local}}` `{{end_local}}` `{{timezone}}` `{{host_name}}` `{{cancel_url}}` `{{reschedule_url}}` `{{meeting_url}}`

Strict substitution. **No Jinja, no `eval`, no expression evaluation.**

⚠️ The allowlist governs **which** variables exist, not **what they contain**. `{{guest_name}}` and the form answers come from the guest and are rendered into email **to the host**, into WhatsApp/SMS, and into the admin panel. Mandatory: **escape per channel** (HTML for email, plain text for SMS/WhatsApp), **cap each variable's length**, **neutralise URLs and markup** in free-text fields. Otherwise a guest phishes the host under this product's own branding.

### Where guest PII lives (RNF-8 — enumerate it, or the purge is partial and silent)

`aethercal-admin guest purge --tenant <slug> --email <addr>`. **`--tenant` is mandatory and the command fails without it** — the CLI has no isolation belt, and an unscoped purge would erase that person's data in every other business on the instance.

A purge that sweeps only `bookings` passes its test and leaves the guest's name and email behind. The PII is in:

| Location | What |
|---|---|
| `bookings` | `guest_name`, `guest_email`, `guest_phone`, `guest_notes`, `answers` |
| `outbox.payload` (JSON) | `guest_email` (`services/bookings.py:763-777`) |
| `webhook_deliveries.payload` (JSON) | the full serialised booking — name, email, notes, answers (`services/bookings.py:239-245`) |
| `guest_tokens` | rows hanging off the booking |
| `sent_notifications` | the ledger |
| rendered bodies | introduced by this batch |

The test asserts each one, by name.

### Observability (R9)

Authenticated `GET /metrics` (Prometheus text). **It must not expose per-business data** — this is a public repository, and an exposed instance would leak its customers' pipeline. `GET /health/ready` reports the outbox backlog (`pending` / `failed` / `dead`, plus the age of the oldest). `aethercal-admin outbox replay` revives a dead intent.

This closes R9: today, if the scheduler process dies, **emails stop going out silently and nothing reports it**.

The `-m db` CI job must **fail, not skip**, when Postgres is absent. A `pytest -m db` that skips everything exits 0 — the silent no-op aimed at our own safety net. Assert collection > 0.

### The connected calendar (RF-11/12/13) — never actually wired

`api/bookings.py:122`: *"Google sync (F1-07) is not wired yet (resolving the host's `ExternalConnection` is the last step)."* The integration exists and is tested; the last link does not. Resolve the host's active `ExternalConnection` in the booking path and enqueue the Google effect.

- **RF-11** — a booking creates the event with its meeting link; cancelling deletes it; rescheduling moves it.
- **RF-12** — a real busy block on the host's calendar **disappears from the offered slots**.
- **RF-13** — if the calendar is unreachable **and no cached copy exists**, offer **no** slots for that host rather than risk a double-booking. ⚠️ **Distinguish "this host has no connected calendar" from "the connected calendar is unreachable."** A self-hoster with no calendar account has neither connection nor cache; a literal reading of RF-13 would leave them with **zero slots** and the product dead on arrival — which RNF-9 forbids ("no core function depends on proprietary services").
- ⚠️ `_enqueue_google` **returns silently** when the connection is `None` (`services/bookings.py:761-762`). Harmless today, because it is *always* None. Once the resolution is wired, a **failed** lookup becomes indistinguishable from "this host has no calendar": the booking confirms, no event is created, nobody finds out. Emit a distinct signal for "expected a connection, found none".

Fix at the root, on the way: `_load_active_connection(...).first()` (`services/calendars.py:379-392`) **silently ignores a host's second active connection**; and `external_calendar_links` is **dead** — `_DEFAULT_CALENDAR_ID = "primary"` is a hard-coded constant (`services/calendars.py:71`), so nobody reads or writes those rows. Make the calendar configurable per connection.

Use a **dedicated** calendar account and a **secondary, dedicated** calendar — never the primary calendar of a personal account. Mail always goes over SMTP.

### Resource timeline (RF-28)

The component takes a generic `resources` array and a `resourceId` per event, and renders a timeline view: resources as rows, time horizontal, with grouping, collapse, and drag across rows. It reuses the axis-agnostic lane-packing sweep in `js/packages/core/src/timeGrid.ts:145-225`. In the backend the resource **is the host** — `event_types.host_id` already exists (`db/models/scheduling.py:52`); a generic rooms/equipment table is a later phase.

⚠️ **Drag across rows has no backend operation yet.** `bookings` has no `host_id`: the host lives on the event type, so moving a booking between hosts means changing its event type — a different duration and a different slug. The component supports the interaction; the admin ships it **disabled** until that semantics is designed.

**Cross the anti-drift lock in order:** edit the Python `TypedDict`s (`packages/aethercal-ui/src/aethercal/ui/calendar.py` — the source of truth) → `uv run poe gen-schema` → update the type and key locks in `js/packages/core/src/calendarProps.contract.test.ts`.

---

## Delivery order

**Wave 0 (serial, blocks everything).** Migration `0005` · the booking state machine · the transition table · the staleness mapping · the exhaustive effect dispatch · retiring the second scheduler with the `sent_notifications` backfill · R8 · the `ChannelSender` protocol.

**Wave 1 — five tracks in parallel; none of them touches `server/admin/` or `apps/booking/`.**
1. Workflow engine (RF-24) — `services/workflows.py`, `api/workflows.py`.
2. Channels and templates (RF-24) — `integrations/whatsapp/`, `integrations/sms/`, the template store and renderer. Fixes a real defect on the way: the email composer ignores the event-type translations that already live in the database (`db/models/scheduling.py:61-70` vs `services/notifications.py:93-98`).
3. No-show, observability, guest erasure (RF-25, RNF-8, R9).
4. Multi-host and the connected calendar (RF-30, RF-11/12/13) — `services/calendars.py`, OAuth.
5. Resource timeline (RF-28) — `packages/aethercal-ui/` only.

**Wave 2 — parallel; one intent per pull request, because the admin is a live surface.** Admin: workflow and template CRUD · no-show button and metrics panel · host selector (today `admin/service.py:142-152` grabs the tenant's first user, and the form has no host field at all) · timeline view. Booking page: guest phone with an explicit consent checkbox, asked **only** when a WhatsApp or SMS rule is active. End-to-end tests (`e2e/`, Playwright) plus `e2e` and accessibility jobs in CI — today the axe run that caught the contrast bug was manual and not repeatable. Documentation and release: the README still says "phase F0" and "pre-alpha", `docs/es/` is promised and **empty**, nothing is published, and every Python package still reads `version = "0.0.0"`. Publish the **at-least-once** contract too — it lives in an internal docstring, and a webhook integrator needs to know that a crash between "provider accepted" and "row committed" replays the effect.

## Definition of done

`uv run poe check` green end to end · `uv run pytest -m db` green against real Postgres, and the job **fails** rather than skips when Postgres is absent · CI green on every job including `docker-build`, `e2e` and accessibility · independent review passed on every cut · the quickstart reproduced from a clean machine in under ten minutes · packages published · what is deployed equals a pushed commit.

## Out of scope

**Batch B** — payments, multi-business RLS/RBAC, and the public-API auth change. Live verification of SMS (no account). Round-robin, collective bookings, seats, Outlook/CalDAV, mobile interactions, RTL.
