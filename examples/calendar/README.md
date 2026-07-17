# Calendar example — the component in a Reflex app

A minimal [Reflex](https://reflex.dev) app that renders the AetherCal calendar: a controlled
calendar (your state owns `view` and `anchor`), a drag that reaches your handler, the resource
timeline, theming and i18n.

## Run it

```bash
pip install "aethercal-ui[reflex]" reflex

reflex init          # once, in this directory
reflex run
```

Open <http://localhost:3000>. Drag an event and the status line reports where it went — that is the
`on_event_drop` handler receiving the recomputed `start`/`end` (and, on the timeline, the target
`resourceId`).

> **On Windows**, importing `aethercal.ui` makes Reflex symlink the component's shared asset, which
> Windows refuses without symlink privilege (`WinError 1314`). Enable **Developer Mode** first.
> Linux and macOS are unaffected.

## What it demonstrates

- **Annotate state with `CalendarEvent` / `CalendarResource`, never `dict`.** Reflex type-checks
  each prop against its declared type: a field annotated `list[dict]` is rejected outright with
  *"Invalid var passed for prop Calendar.events"*. Both TypedDicts are exported from `aethercal.ui`
  for exactly this.
- **Naive local wall-time.** Events use `"2026-07-13T09:00:00"` — no offset, no `Z`. The API speaks
  UTC; convert at that boundary, not in the component.
- **Controlled navigation.** With `navigation=True` the toolbar *emits* the new period
  (`on_range_change`, `on_view_change`) and your state decides. Ignore the events and it will not
  move.
- **`editable: False`** makes an event undraggable — enforce it server-side too, because a
  client-side flag is a hint, not a permission.
- **The timeline is generic.** Resources are rows; AetherCal maps one to a host, but rooms or
  machines work identically.

The full prop reference — including the React package — is in the
[component guide](../../docs/calendar-component.md).
