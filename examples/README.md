# Examples

Small, runnable programs — each one exercises the real thing, not a snippet.

| Example | What it does |
|---|---|
| [`sdk/`](sdk/) | Books a meeting with `aethercal-client`: list event types → find a free slot → book it → prove the slot cannot be booked twice. |
| [`calendar/`](calendar/) | Renders the calendar component in a Reflex app: controlled navigation, drag-to-move, the resource timeline, theming and i18n. |

The SDK example needs a running AetherCal — the [quickstart](../docs/quickstart.md) gets you one in
a few commands.

A React playground for the same component (all five views, drag/resize with optimistic
reconciliation, token theming, ES/EN) lives in `packages/aethercal-ui/js/examples/demo`.
