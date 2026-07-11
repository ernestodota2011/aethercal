# AetherCal

> **Pre-alpha — not ready for production.** APIs, schemas, and storage are unstable and will
> change without notice until the first tagged release. Watch the repository for the `v0.1.0`
> milestone.

Open-source calendar and appointment-scheduling infrastructure — Python-first and self-hostable
in a single container plus a database.

AetherCal fills a real gap: the best appointment-scheduling project in the Python ecosystem has
under 300 stars, the dominant JavaScript calendar charges an annual license for its highest-value
views, and the leading hosted booking platform went closed-source in 2026. AetherCal is a
permissively licensed (MIT) alternative built around one correct scheduling engine.

## What it is (and will be)

- **A pure scheduling engine** (`aethercal-core`): RFC 5545 recurrence, timezone-correct
  availability, and slot computation with no I/O — property-tested against DST edges and an
  independent oracle.
- **An appointment platform**: a public booking page, transactional email with calendar
  invitations, an authenticated API with signed outgoing webhooks, and a minimal admin.
- **An embeddable calendar component**: month/week/day/list views with fluid drag-and-drop,
  theming, and internationalization.
- **Self-host first**: one deployable unit plus a database, configured by environment variables,
  with no proprietary dependency in the core.

## Status

Under active initial construction — phase **F0**, the domain engine. See [ROADMAP.md](ROADMAP.md).

## Repository layout

A [uv](https://docs.astral.sh/uv/) workspace monorepo:

```
packages/
  aethercal-core/      # pure domain engine (recurrence, tz, availability, slots, ical, conflicts)
  aethercal-schemas/   # Pydantic API contract
  aethercal-client/    # httpx SDK (sync + async)
  aethercal-ui/        # Reflex calendar component wrapping a custom TSX core
  aethercal/           # metapackage + CLI
apps/
  server/              # FastAPI API v1 + Reflex admin
  booking/             # public booking page (FastHTML + HTMX)
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
and commit the regenerated bundle (CI's rebuild-and-diff drift guard fails a stale commit). Run
`uv run poe fetch-js-bundle` to verify the committed bundle is present.

## Documentation

English documentation lives in `docs/`; a first-class Spanish locale lives in `docs/es/`.

## License

[MIT](LICENSE).
