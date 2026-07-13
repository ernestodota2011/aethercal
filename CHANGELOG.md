# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every package in the repository shares one version number.

## [Unreleased]

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
- **Notification workflows are only partially landed.** The engine, its migration and the seeded
  24-hour reminder rule exist, but only the email channel has an adapter — WhatsApp and SMS are
  declared with no integration behind them, and there is no workflow CRUD API yet.

[Unreleased]: https://github.com/ernestodota2011/aethercal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ernestodota2011/aethercal/releases/tag/v0.1.0
