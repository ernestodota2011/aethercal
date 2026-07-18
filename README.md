# AetherCal

Open-source calendar and appointment-scheduling infrastructure — Python-first, self-hostable as
one container plus a database.

AetherCal fills a real gap: the best appointment-scheduling project in the Python ecosystem has
under 300 stars, the dominant JavaScript calendar charges an annual license for its highest-value
views, and the leading hosted booking platform went closed-source in 2026. AetherCal is a
permissively licensed (MIT) alternative built around one correct scheduling engine.

## Status

**v0.1.0**, the first release, shipped 2026-07-13 (see [CHANGELOG.md](CHANGELOG.md)). The table below
is checked against the code on this branch — not against the roadmap, and not only against v0.1.0's
own release notes: it also covers what has been added since, including payments and multi-business
isolation. The booking stack is built and tested; the only public deployment today is
[demo.aetherlogik.com](https://demo.aetherlogik.com/), which serves the calendar-component demo (F2),
not the booking API. It is a **0.1.0**, not a 1.0: the API contract can still change, and the areas
below marked *not wired* are exactly that.

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
| Payments — Stripe and Mercado Pago checkout, unpaid holds that self-cancel, refunds and disputes, each business on its own (BYOK) account | Working in **provider test mode only**: the Stripe adapter is exercised against a stubbed transport, never a live `sk_test_` key; the Mercado Pago adapter has never run against a real or sandbox account. Partial refunds are not modelled. See [docs/byok-credentials.md](docs/byok-credentials.md) |
| Signed outgoing webhooks — `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.no_show` | Working — **at-least-once**, see [docs/webhooks.md](docs/webhooks.md) |
| Calendar component — month, week, day, list **and resource timeline** views | Working: drag/resize with optimistic reconciliation, 4 theme presets + token overrides, ES/EN i18n, keyboard parity |
| Google Calendar — **busy-check** (a real busy block removes the slot) and **write-back** (a booking creates the event; cancelling deletes it, rescheduling moves it) | Working; degrades safely when the calendar is unreachable. Exercised against a **fake** Google transport, never a live account — like the payment adapters above, treat your first real account as an integration nobody has run yet |
| Multi-business isolation — PostgreSQL row-level security (`FORCE ROW LEVEL SECURITY` + one policy per business-scoped table), enforced by the database rather than by every query remembering its own `WHERE tenant_id` | Working — one instance safely serves more than one business; see [deploy/README.md](deploy/README.md) |
| Self-host — one image, four processes (a one-shot migrator, the API, the worker and the booking page) plus PostgreSQL, entirely env-configured | Working — see [docs/quickstart.md](docs/quickstart.md) |
| Minimal admin (Reflex) | Working |

**What is _not_ wired yet — do not plan around it**

- **WhatsApp and SMS work — but nothing verifies that the phone number belongs to the person
  booking.** The adapters, the consent checkbox and the mandatory daily caps are live, and both
  channels stay **off until you configure them**. The number, however, is typed into a *public*
  form, and a ticked consent box only proves that *somebody* ticked it — not that the **owner of
  the number** agreed. A stranger can book with someone else's phone and make your business message
  them, under your brand. Verifying possession of the number (an OTP, or a confirmation link) is a
  **declared gap**, not a thing that quietly works.
  ==Read [docs/phone-channels.md](docs/phone-channels.md) before switching a phone channel on.==
- **Neither payment provider has been verified against a live account, and partial refunds are not
  modelled** — a refund is always the whole charge. Read
  [docs/byok-credentials.md](docs/byok-credentials.md) before taking a real charge.
- **No round-robin or collective bookings.**

See [ROADMAP.md](ROADMAP.md) and [UPGRADING.md](UPGRADING.md) for what comes next and what breaks
getting there.

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

Self-host and book a test appointment: **[docs/quickstart.md](docs/quickstart.md)** — one database, a
signing secret, three PostgreSQL roles created once by hand, then `docker compose up --build`.

## Documentation

| Guide | What it covers |
|---|---|
| [Quickstart](docs/quickstart.md) | Self-host from a clean machine and book a test appointment |
| [Python SDK](docs/sdk.md) | `aethercal-client` — the sync and async HTTP clients |
| [Calendar component](docs/calendar-component.md) | `@aethercal/calendar-react` and the `aethercal-ui` Reflex wrapper: props, the five views, theming, i18n, events, accessibility |
| [Webhooks](docs/webhooks.md) | Signature verification and the **at-least-once** delivery contract |
| [Embedding](docs/embedding.md) | Drop the booking widget onto any site with one `<script>` tag |
| [BYOK credentials](docs/byok-credentials.md) | Per-business payment/email/phone credentials: precedence, what the encryption protects, and which payment provider is unverified |
| [Phone channels](docs/phone-channels.md) | The declared gap in phone-number verification before you turn WhatsApp/SMS on |
| [Privacy & retention](docs/privacy.md) | What guest data is handled, how long it is kept, and how a right-to-erasure request is fulfilled |
| [Español](docs/es/) | Locale español: guía de inicio y quickstart |
| [Upgrading](UPGRADING.md) | Breaking changes since v0.1.0 and what to do about each one |

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
