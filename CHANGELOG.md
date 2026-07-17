# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every package in the repository shares one version number.

## [Unreleased]

### Changed

**A business's messages now go out on that business's own account** — B-03 stored per-business
credentials; nothing sent with them.

> [!WARNING]
> **Breaking for multi-business instances that rely on `AETHERCAL_WHATSAPP_*` / `AETHERCAL_SMS_*`.**
> A business with no phone credential of its own used to send from the **instance operator's**
> number, silently. It no longer does: those steps are now `skipped`, with a reason.
>
> To restore the old behaviour on a **single-business self-host** (where the operator *is* the
> business), set `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=true`. On an instance serving more than one
> business, give each business its own credential instead —
> `aethercal-admin credentials set --provider whatsapp`.

- The senders are resolved **per business, per outbox item**, from that item's own `tenant_id`,
  inside the drain's existing per-item `tenant_scope`. They used to be built once at boot from the
  instance's environment and shared by every business the drain worked through.
- **Email is unchanged.** An SMTP relay is a transport, not an identity — the `From` header travels
  per message — so the instance relay is still lent to a business that has no SMTP of its own. A
  single-business self-hoster who set `AETHERCAL_SMTP_HOST` once keeps working exactly as before.
- The daily caps stay the **operator's** policy and now bound a business's own phone sender too: the
  recipient comes from the operator's public booking form regardless of whose API key pays the bill.
  A business that brings a phone credential to an instance with no caps declared keeps that channel
  off, and the worker logs which variables would turn it on.
- `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY` (default `false`) is warned about at boot when enabled.
- **A business's `base_url` is now guarded on egress.** Moving it out of the environment and into a
  per-business credential turned it from operator configuration into input a third party controls
  and the server obeys — so a tenant-supplied endpoint must be `https` and must resolve to a
  **public** address. Loopback, link-local (including `169.254.169.254`, the cloud metadata
  service), RFC1918, CGNAT and reserved ranges are refused, checked by **resolved IP** rather than
  hostname, with one bad record poisoning the whole target. The operator's own
  `AETHERCAL_*_BASE_URL` is unaffected — provenance decides — and the operator's private-target
  allowlist is deliberately not honoured for a tenant: it exists so the operator can reach their own
  LAN, not so a business can.
- **The egress guard is re-applied at connect, so DNS rebinding cannot move the socket.** A
  build-time check is a TOCTOU window — httpx re-resolves when it opens the connection, so a tenant
  controlling its own resolver could answer public for the guard and `127.0.0.1` for the socket. A
  tenant's sender is now built on a client whose transport re-validates and pins the address at
  connect (reusing the webhook path's `pinned_ip_for`), with `Host` and TLS certificate
  verification still bound to the real hostname.
- **Tenant egress keeps no idle connection.** Pinning rewrites the request's host to the validated
  IP, and a connection pool is keyed by origin — so two businesses whose hostnames resolve to the
  same address would collapse onto one pool key and could share a TLS connection established with
  the other's certificate. Keep-alive is off for tenant egress (HTTP/2 stays off too, and is now
  asserted): every send stands up its own connection, handshaked for its own hostname. The cost is
  a TLS handshake per send.
- **One broken channel no longer silences the others — whatever breaks it.** A failure while
  resolving one channel escaped the resolver entirely, so a business with a bad WhatsApp `base_url`
  (or simply a DNS outage on it) stopped receiving its *email*. Isolation is now scoped to the
  operation — resolving one channel — rather than to a list of error types, so it holds for failures
  nobody has enumerated. A channel that is configured and fails to resolve FAILS and retries,
  carrying its own reason; one that was never configured is still skipped. A cancellation or
  shutdown is not a channel failure and still rises.
- **A workflow step on email sees its channel's fault.** A business whose SMTP relay was refused
  had its email reminders retired as "the channel is off" — terminal — while the recorded reason
  went unread: emails failing silently, in a product whose job is sending reminders. Every
  sender-less path now goes through one door that tells OFF (terminal) from BROKEN (retryable).
- **A phone credential with no declared caps is a fault, not a switched-off channel.** It made the
  channel merely absent, which the drain reads as terminal — so a guest's reminder was discarded for
  ever because an operator had not set `AETHERCAL_<CHANNEL>_DAILY_CAP_*`, and setting it afterwards
  brought nothing back. The channel still cannot come up uncapped; it now fails and retries, so the
  message outlives the fix.
- **A tenant's SMTP relay host is guarded too, and pinned at connect.** `host: 127.0.0.1, port: 25`
  relayed a business's mail through the operator's own local MTA — an open relay on the operator's
  IP reputation. It is the same trust-boundary bug without the HTTP, and scoping it out was
  classifying by protocol when the rule is declared by provenance. A business's relay is now dialed
  through a connector that re-validates and pins the address at connect and hands the SMTP client an
  already-connected socket, so it performs no lookup of its own; `hostname` still drives TLS
  SNI/certificate verification. The operator's own `AETHERCAL_SMTP_HOST` is unaffected.
- Removed `app.build_email_sender` / `app.build_channel_senders`, replaced by
  `app.build_instance_sender_defaults` (configuration, not clients). The web process no longer
  builds senders at all — it never read the ones it was building. Its half-configured-phone-channel
  boot check is unchanged.

**Multi-business isolation is now enforced by PostgreSQL, not by application code** — every
tenant-scoped table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` plus one policy, and the process
serving requests no longer owns the tables (migration `0008_rls_roles_and_policies`).

> [!WARNING]
> **Breaking for every self-host.** The API used to run as a single database role that owned every
> table and migrated the schema on its own boot (`AETHERCAL_AUTO_MIGRATE`, default on). It now runs
> as **three** separate PostgreSQL roles across **three** separate processes, and two new environment
> variables (`AETHERCAL_OWNER_DATABASE_URL`, `AETHERCAL_WORKER_DATABASE_URL`) are required before the
> stack will start at all.
>
> Before pulling this: create the three roles with `deploy/sql/provision_roles.sql` (a superuser,
> one-time, human step — it cannot be a migration), add the two new database URLs to `.env`, and
> remove `AETHERCAL_AUTO_MIGRATE` / `AETHERCAL_RUN_SCHEDULER` from `.env` if either is set — both now
> **fail the boot** instead of being honoured. See [UPGRADING.md](UPGRADING.md).

- Migrations no longer run inside the web process. A one-shot `migrate` service runs
  `aethercal-admin db upgrade` as the new `aethercal_owner` role, to completion, before `app` or
  `worker` starts; the web process then refuses to serve a schema behind head.
- The background drain (reminders, outbound webhooks, the Google busy-cache refresh) is no longer a
  flag on the web process. It is its own `aethercal-worker` process, which must run in **exactly
  one** replica for the whole deployment (`deploy/README.md`).
- `aethercal_app` (the API + admin) never holds `BYPASSRLS`; an unbound session reads zero rows
  rather than every business's, and a write carrying another business's id is denied.
- `tenants` deliberately carries no policy (the public router resolves a business by slug before any
  session is bound to one); every other tenant-scoped table does, derived from the models rather than
  from a hand-written list, so a future table with no policy fails CI instead of shipping unprotected.

### Added

**The connected calendar** — a booking now creates the event in the host's Google Calendar,
cancelling deletes it, and rescheduling moves it. ==RF-11/12/13 were ticked and were not true: no
booking had ever reached a host's calendar.== The integration and the outbox effect were both built
and tested; the one missing link was resolving the host's connection, and its absence raised nothing
anywhere — every booking skipped the sync in silence.

- The **existence of a connected calendar** is the gate, and the two ways to read "no calendar" are
  now kept apart. A host who never linked Google enqueues no intent and the booking is complete
  (benign, the self-hoster). A host who *has* a connection enqueues the intent — and if the calendar
  cannot be resolved later, at drain time, the effect retries, dead-letters and lands in the visible
  outbox backlog. It is never quietly marked delivered. Both cases used to collapse into one silent
  `return`.
- **A chain that already has an event is always synced**, whatever the host's calendars look like
  today. A host who revokes their Google account between the confirmation and the cancellation has
  no active connection — gating on that alone would drop the intent, the guest would be cancelled,
  and the meeting would stay in the host's calendar forever with nobody told.
- The intent captures only the **host**, never a connection id: the target calendar is resolved at
  drain time from the live configuration, so there is one source of truth for "where does this event
  go" instead of a snapshot that can rot between enqueue and drain.
- ==Exercised against a **fake** Google transport only; no booking has yet reached a live Google
  account.== That the code asks for the right thing is proven; what the real API answers is not.

**No-show becomes observable** — `booking.no_show` joins `booking.created`, `booking.cancelled` and
`booking.rescheduled` on the outgoing webhook vocabulary. A subscriber's CRM learned about a
cancellation and about a reschedule, but a guest who simply never turned up was invisible to it.
Widening the vocabulary needs no data migration, and no existing subscriber starts receiving the new
event by accident — nobody can have subscribed to an event that did not exist.

**Payments** — a business can now charge for an appointment with its own Stripe or Mercado Pago
account (BYOK). ==Neither provider has been run against a live account in this cut.==

- `event_types` gains `price_cents` (`NULL` = free), `currency`, `refund_window_minutes` and
  `refund_kind`; `bookings` gains `hold_expires_at` and `confirmed_by_payment_id` (migration
  `0015_payments_and_holds`). A paid event type creates the booking as an unpaid **hold** that
  self-cancels if nobody pays within its window, never blocking the slot forever.
- The arbiter (`services/payments.py`) turns a provider's payment webhook into exactly one of six
  outcomes — confirm, refund (a double payment, a stale hold, a mismatched amount), park (the
  webhook arrived before the checkout's own commit), or mark a dispute — resolved by the provider's
  own reference, never by the event's metadata.
- Refunds are always the whole charge; partial refunds are not modelled (`F5`).
- A business with **two** money credentials configured (both Stripe and Mercado Pago) is refused with
  an explicit error rather than a silent pick — a per-tenant preference needs its own migration. See
  [docs/byok-credentials.md](docs/byok-credentials.md#which-payment-provider-a-business-charges-with).
- Neither adapter has been exercised against a live or sandbox account: Stripe's is unit-tested
  against a stubbed HTTP transport only; no Mercado Pago account exists for this project at all. Read
  [docs/byok-credentials.md](docs/byok-credentials.md#neither-payment-adapter-has-been-verified-against-a-live-account)
  before taking a real charge.

**Per-business branding** — a business's booking page is now *theirs*, not the product's.

- `tenants` gains `public_name`, `logo_url`, `accent_color` and `timezone` (migration
  `0014_tenant_branding`). Existing rows keep behaving exactly as before: no logo, no colour, and
  `UTC` — the zone the booking page was already hard-coding for everybody.
- The booking page renders the business's name and logo in its header, its accent colour as the
  page's accent, and its timezone as the display zone a visitor sees before choosing their own. A
  visitor's explicit `?tz=` still wins.
- `GET /api/v1/branding` returns the branding of the business the API key belongs to. It takes no
  parameters — the business is never an input — and the SDK exposes it as `client.get_branding()`.
- The admin gains a **Branding** page. A colour must be a hex triplet, a logo URL must be `https`,
  and a timezone must be a real IANA zone; anything else is refused with a readable message and
  nothing is written.
- The booking page's `img-src` content-security policy now permits `https:` so an operator's logo
  can actually load. Without it the feature would have failed silently in the visitor's browser.

## [0.1.0] — 2026-07-13

The first published release. The booking stack is in production for its first operator; the API
contract may still change before 1.0.

### Added

**The scheduling engine** (`aethercal-core`)

- RFC 5545 recurrence expansion (`RRULE` / `EXDATE` / `RDATE`), property-tested against DST edges and
  an independent oracle. An event's `dtstart` is naive wall-time with its timezone stored beside it,
  so a weekly series keeps its local hour across a DST change.
- Availability from a weekly schedule plus date overrides; slot computation applying duration,
  increment, buffers, minimum notice and maximum advance; conflict detection; iCalendar
  serialization.
- Pure: no I/O, no clock, no internal dependencies — enforced by import contracts in CI.

**The API and the self-host** (shipped in the container)

- API v1: event types, schedules (with date overrides), slots, bookings (book, cancel, reschedule)
  and webhook subscriptions. API-key authenticated.
- A double-booked slot is rejected with `409`, decided by the database rather than by an application
  race.
- Rescheduling creates a successor booking that inherits the calendar identity and cancels its
  predecessor.
- Signed outgoing webhooks (HMAC-SHA256 over the canonical body), an SSRF egress guard before every
  delivery, exponential backoff, and a dead-letter state.
- Transactional email with an `.ics` invitation. Missing SMTP or Google configuration degrades
  gracefully instead of failing the boot.
- Guest self-service: the cancel and reschedule links in the confirmation email carry a signed guest
  token, so a guest can act on their own booking without an API key — and cannot touch anyone
  else's.
- Notification workflows: a durable step lifecycle on the transactional outbox, with the 24-hour
  reminder seeded per tenant as a rule and delivered over email. A step is materialised, voided or
  skipped by an exhaustive transition table, so a rescheduled booking cannot still fire its
  predecessor's follow-up.
- No-show: a booking can be marked `no_show`. It keeps occupying its slot — the time has passed, and
  releasing it would permit a retroactive booking over it.
- Google Calendar **busy-check**: a real busy block on a host's calendar removes the slot, and an
  unreachable calendar withholds that host's slots rather than risk a double-booking.
- A public bilingual (ES/EN) booking page, an embeddable widget, and a minimal Reflex admin.
- Self-host as one container plus PostgreSQL, configured by environment variables, migrations run on
  boot.

**The SDK** (`aethercal-client`)

- Synchronous client: `list_event_types`, `get_slots`, `create_booking`, `cancel_booking`,
  `reschedule_booking`, `health`, `ping`. Typed responses; transport failures surface as
  `AetherCalTransportError` instead of leaking `httpx` exceptions.
- Asynchronous client: `health` and `ping` only, for now.

**The calendar component** (`aethercal-ui`, `@aethercal/calendar-react`, `@aethercal/calendar-core`)

- Five views: month, week, day, list, and a resource timeline (resources as rows, time across).
- Drag and resize with optimistic reconciliation, rolling back on rejection or timeout.
- Four theme presets plus `--ac-*` token overrides; `en` / `es` message packs with per-string
  overrides.
- Keyboard parity for every pointer gesture, with exactly one tab stop per grid.
- The React layer never bundles React (peer dependency); the headless core never imports it at all.

**Documentation**

- A self-host quickstart that ends in a real booking, an SDK guide, a component guide, a webhooks
  guide that publishes the **at-least-once** delivery contract, and a Spanish locale in `docs/es/`.
- Runnable examples in `examples/`.

### Notes for integrators

- **Webhook delivery is at-least-once, not exactly-once.** A crash between "your endpoint accepted"
  and "we committed that it accepted" replays the effect, so a handler *will* see duplicates: key on
  the booking `id` plus the `event`, and make a repeat a no-op. Delivery order is not guaranteed
  either. See [docs/webhooks.md](docs/webhooks.md).
- **A booking does not create an event in the host's Google Calendar.** The busy-check reads the
  calendar; the write-back leg is not connected yet.
- **Notification workflows run over email only.** The engine, its migration and the seeded 24-hour
  reminder are live, but WhatsApp and SMS are declared in the `Channel` enum with no adapter behind
  them, and there is no workflow CRUD API yet — the rules are seeded, not editable.
- **No-show emits no webhook.** A booking can be marked `no_show`, but the outgoing events remain
  `booking.created`, `booking.cancelled` and `booking.rescheduled`.

[Unreleased]: https://github.com/ernestodota2011/aethercal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ernestodota2011/aethercal/releases/tag/v0.1.0
