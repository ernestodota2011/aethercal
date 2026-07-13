# AetherCal documentation

| Guide | What it covers |
|---|---|
| [Quickstart](quickstart.md) | Self-host from a clean machine and book a test appointment |
| [Python SDK](sdk.md) | `aethercal-client` — the sync and async HTTP clients |
| [Calendar component](calendar-component.md) | `@aethercal/calendar-react` and the `aethercal-ui` Reflex wrapper |
| [Webhooks](webhooks.md) | Signature verification and the **at-least-once** delivery contract |
| [Embedding](embedding.md) | Drop the booking widget onto any site with one `<script>` tag |

Operator reference: [`deploy/README.md`](../deploy/README.md) — every setting, the scheduler rule,
and hardening the admin surface.

Runnable examples: [`examples/`](../examples/).

## Otros idiomas

- **[Español](es/)** — guía de inicio y quickstart.

## Design notes

`design/` and `spikes/` hold the working design records — how a decision was reached and what was
de-risked before it was built. They are historical, not a user guide: where they disagree with the
guides above, the guides describe what the code actually does.
