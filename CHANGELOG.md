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
- Removed `app.build_email_sender` / `app.build_channel_senders`, replaced by
  `app.build_instance_sender_defaults` (configuration, not clients). The web process no longer
  builds senders at all — it never read the ones it was building. Its half-configured-phone-channel
  boot check is unchanged.

### Added

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
