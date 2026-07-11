# AetherCal — public calendar demo

A static React playground for [`@aethercal/calendar-react`](../../packages/react) (AetherCal-06 §9,
task F2-G). It mounts the batteries-included `OptimisticCalendar` with realistic sample data and
lets a visitor drive it live:

- **View switcher** — `month` / `week` / `day` / `list`, showing overlapping events (lanes),
  all-day and multi-day bands, and a cross-midnight event.
- **Theme toggle** — the four shipped presets (`light` / `dark` / `midnight` / `high_contrast`).
  The whole page follows the selected preset (it reuses the same `--ac-*` tokens).
- **Language toggle** — ES / EN, localizing both the calendar and the page chrome.
- **Mock-server toggle** — `Accept` vs `Reject`. Drag or resize an event and watch the optimistic
  update either commit with the server's new revision or roll back with the flash animation. There
  is **no backend**: the mutation is a client-side promise.

It also carries a 3-step quickstart and a link to the repository.

## How it consumes the component

The demo is a package in the same pnpm workspace (`packages/aethercal-ui/js`) and depends on
`@aethercal/calendar-react` via `workspace:*`. It consumes the component **as source** (the package
`exports` point at its TypeScript entry), so there is no separate build step for the component and
**the demo never touches the component** — it only imports its public API
(`OptimisticCalendar`, `PRESETS`, `PRESET_NAMES`, and the payload types).

## Develop

From the workspace root `packages/aethercal-ui/js`:

```bash
pnpm install
pnpm --filter @aethercal/calendar-demo dev      # http://localhost:5173
```

## Build (for deployment)

From the workspace root `packages/aethercal-ui/js`:

```bash
pnpm --filter @aethercal/calendar-demo build
```

- **Output directory:** `packages/aethercal-ui/js/examples/demo/dist/`
- **Portable:** built with `base: "./"` (relative asset URLs), so it serves from any subdomain or
  sub-path with a plain static file server — no runtime, no backend.
- Preview the built output locally with `pnpm --filter @aethercal/calendar-demo preview`.

> Deploy (subdomain on core01 via NPM host + Cloudflare DNS) and the `web-qa-auditor` GO gate are
> **separate, later steps** (owned by devops-aetherlogik-homelab). This package only builds the
> static bundle.

## Test / typecheck

Both run as part of the workspace-wide `pnpm -r test` / `pnpm -r typecheck`:

```bash
pnpm --filter @aethercal/calendar-demo test        # sample-data invariants (vitest)
pnpm --filter @aethercal/calendar-demo typecheck   # tsc --noEmit
```
