# AetherCal

Open-source calendar and appointment-scheduling infrastructure — Python-first, self-hostable as
one container plus a database.

AetherCal fills a real gap: the best appointment-scheduling project in the Python ecosystem has
under 300 stars, the dominant JavaScript calendar charges an annual license for its highest-value
views, and the leading hosted booking platform went closed-source in 2026. AetherCal is a
permissively licensed (MIT) alternative built around one correct scheduling engine.

## Status — v0.1.0, the first release

The booking stack is built, tested, and running in production for its first operator. It is a
**0.1.0**, not a 1.0: the API contract can still change, and the areas below marked *not wired* are
exactly that. This section is checked against the code, not against the roadmap.

**What works today**

| Capability | State |
|---|---|
| Scheduling engine — RFC 5545 recurrence, timezone-correct availability, slot computation | Property-tested against DST edges and an independent oracle |
| API v1 — event types, schedules (+ date overrides), slots, bookings, webhooks | Working; API-key authenticated |
| Booking — book, cancel, reschedule; a double-booked slot is rejected with `409` | Working |
| Public booking page (FastHTML + HTMX), bilingual ES/EN, embeddable widget | Working — see [docs/embedding.md](docs/embedding.md) |
| Transactional email with an `.ics` invitation | Working (SMTP; absent SMTP degrades gracefully) |
| Guest self-service — the cancel / reschedule links in the confirmation email | Working |
| The 24-hour reminder, seeded per tenant as a workflow rule | Working, over email |
| No-show — mark a booking `no_show` (it keeps its slot; the time has passed) | Working |
| Signed outgoing webhooks | Working — **at-least-once**, see [docs/webhooks.md](docs/webhooks.md) |
| Calendar component — month, week, day, list **and resource timeline** views | Working: drag/resize with optimistic reconciliation, 4 theme presets + token overrides, ES/EN i18n, keyboard parity |
| Google Calendar **busy-check** — a real busy block removes the slot | Working; degrades safely when the calendar is unreachable |
| Self-host — one container + PostgreSQL, env-configured, migrations on boot | Working — see [docs/quickstart.md](docs/quickstart.md) |
| Minimal admin (Reflex) | Working |

**What is _not_ wired yet — do not plan around it**

- **A booking does not create an event in the host's Google Calendar.** The busy-check reads the
  host's calendar, but the write-back leg is not connected (see `_build_effects` in
  `apps/server/src/aethercal/server/api/bookings.py`). Cancelling and rescheduling likewise do not
  touch Google.
- **Notification workflows run, but only over email.** The engine, its migration and the seeded
  24-hour reminder are live — and the reminder does reach the guest. But **WhatsApp and SMS are
  declared in the `Channel` enum with no adapter behind them**, and there is no workflow CRUD API or
  admin screen yet: the rules are seeded, not editable.
- **No-show has no webhook.** You can mark a booking `no_show`, but the outgoing events are still
  only `booking.created`, `booking.cancelled` and `booking.rescheduled`.
- **No payments, no multi-business isolation, no round-robin or collective bookings.**

See [ROADMAP.md](ROADMAP.md) for what comes next.

## Install

```bash
pip install aethercal          # the engine + the API contract + the SDK
pip install aethercal-core     # or just the pure scheduling engine (zero I/O)
pip install aethercal-client   # or just the HTTP SDK
pip install aethercal-ui       # the calendar component (Reflex)
```

```bash
npm install @aethercal/calendar-react   # the React calendar component
```

The **server** and the **booking page** are not published to PyPI: they ship as the self-host
container built from this repository (see the quickstart).

## Quickstart

Self-host and book a test appointment: **[docs/quickstart.md](docs/quickstart.md)** —
`docker compose up`, one database, two required variables.

## Documentation

| Guide | What it covers |
|---|---|
| [Quickstart](docs/quickstart.md) | Self-host from a clean machine and book a test appointment |
| [Python SDK](docs/sdk.md) | `aethercal-client` — the sync and async HTTP clients |
| [Calendar component](docs/calendar-component.md) | `@aethercal/calendar-react` and the `aethercal-ui` Reflex wrapper: props, the five views, theming, i18n, events, accessibility |
| [Webhooks](docs/webhooks.md) | Signature verification and the **at-least-once** delivery contract |
| [Embedding](docs/embedding.md) | Drop the booking widget onto any site with one `<script>` tag |
| [Español](docs/es/) | Locale español: guía de inicio y quickstart |

Runnable examples live in [`examples/`](examples/).

## Repository layout

A [uv](https://docs.astral.sh/uv/) workspace monorepo:

```
packages/
  aethercal-core/      # pure domain engine (recurrence, tz, availability, slots, ical, conflicts)
  aethercal-schemas/   # Pydantic API contract
  aethercal-client/    # httpx SDK (sync + async)
  aethercal-ui/        # Reflex calendar component wrapping a custom TSX core
  aethercal/           # metapackage
apps/
  server/              # FastAPI API v1 + Reflex admin         (ships in the container)
  booking/             # public booking page (FastHTML + HTMX)  (ships in the container)
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync              # create the environment and install all workspace members
uv run poe check     # format-check, lint, typecheck, import contracts, tests
uv run poe test      # tests only
```

The prebuilt UI bundle (`packages/aethercal-ui/src/aethercal/ui/assets/aethercal-calendar.js`) is
committed to the repo, so contributors without Node need no extra step — a fresh `uv sync` already
has it. If you change the calendar's TSX, rebuild it with `pnpm build` in `packages/aethercal-ui/js`
and commit the regenerated bundle (CI's rebuild-and-diff drift guard fails a stale commit).

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
