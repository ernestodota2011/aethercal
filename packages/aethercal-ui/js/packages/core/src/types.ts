/**
 * Contract types for the AetherCal calendar core (headless).
 *
 * These mirror the props the Reflex wrapper (`aethercal.ui.calendar.Calendar`) passes in from
 * Python and the payload shapes it expects back. The schema is the cross-language source of
 * truth (AetherCal-06 §4, `calendar-props.schema.json`); Python↔TS drift is caught in CI.
 *
 * F2-A scope note: `revision` / `client_mutation_id` are declared here so the contract is
 * forward-compatible, but the optimistic reconciliation that makes them load-bearing (server-
 * assigned monotonic `revision`, rollback on stale) is F2-D. In F2-A they are optional and the
 * month view simply echoes an event's `revision` through a drop payload when present.
 */

/** The four calendar surfaces (AetherCal-06 §5). Only `month` renders in F2-A; the rest are F2-B/C. */
export type CalendarView = "month" | "week" | "day" | "list";

/** One calendar event as rendered by a view. */
export interface CalendarEvent {
  /** Stable identifier, echoed back unchanged in mutation payloads. */
  id: string;
  title: string;
  /** ISO 8601 datetime, naive local wall-time (no offset), e.g. "2026-07-09T14:00:00". */
  start: string;
  /** ISO 8601 datetime, naive local wall-time, exclusive. */
  end: string;
  /** All-day event (rendered without a time-of-day); default false. */
  allDay?: boolean;
  /** Optional CSS color for the event chip accent. */
  color?: string;
  /** Whether the user may drag/resize this event; default true. Enforced server-side too. */
  editable?: boolean;
  /**
   * Monotonic-increasing per-event integer, server-assigned on each accepted mutation
   * (AetherCal-06 §4). Optional in F2-A; the reconciliation that requires it is F2-D.
   */
  revision?: number;
}

/**
 * Payload emitted when an event is dropped on a new day/time (`on_event_drop`, §4).
 * `revision` is echoed from the dragged event when present; `client_mutation_id` is set by the
 * reconciliation layer (F2-D) so the server can dedupe a retried mutation idempotently.
 */
export interface EventDropPayload {
  id: string;
  start: string;
  end: string;
  revision?: number;
  client_mutation_id?: string;
}

/**
 * Payload emitted when an event's duration is changed by dragging a resize handle
 * (`on_event_resize`, §4). Structurally identical to `EventDropPayload` (one endpoint moved,
 * the other fixed) — kept a distinct type so the contract, the wrapper handler, and the schema
 * name the two gestures separately.
 */
export interface EventResizePayload {
  id: string;
  start: string;
  end: string;
  revision?: number;
  client_mutation_id?: string;
}

/**
 * Payload emitted when the user drags across empty space to create a new event
 * (`on_range_select`, §4). No `id` yet (nothing exists) and no `revision` (creation, not a
 * mutation of an existing event). `allDay` distinguishes an all-day/date-granular selection
 * (month cell, all-day rail) from a timed time-grid selection.
 */
export interface RangeSelectPayload {
  start: string;
  end: string;
  allDay: boolean;
}

/** Payload emitted when an event is clicked (`on_event_click`, §4). */
export interface EventClickPayload {
  id: string;
}

/**
 * Payload emitted on a right-click / context-menu gesture (`on_context_menu`, §4). `id` is present
 * when the gesture landed on an event, `start` when it landed on empty space (a day/slot) — so a
 * host can offer "edit this event" vs. "create here". Modeled as an at-least-one union so the empty
 * object is not a valid payload (the schema enforces the same with `minProperties: 1`).
 */
export type ContextMenuPayload =
  | { id: string; start?: string }
  | { start: string; id?: string };

/**
 * Payload emitted when the visible view or range changes (`on_view_change` / `on_range_change`,
 * §4). Declared here as the forward-compatible navigation contract; the F2-D interaction layer
 * emits the mutation/selection events above, while the navigation chrome that fires these is F2-E/F
 * (kept a typed seam, not a live affordance, consistent with F2-A/B).
 */
export interface ViewChangePayload {
  view: CalendarView;
  from: string;
  to: string;
}

/** Which edge of an event a resize gesture is dragging. */
export type Edge = "start" | "end";

/**
 * A point on a calendar surface, as the interaction machine sees it — a day plus an optional
 * minute-of-day. `minuteOfDay` is `null` for a date-granular surface (month cell / all-day rail)
 * and a 0..1440 minute for the time grid. Framework-agnostic: the React layer maps a pointer
 * position to one of these; the core never touches the DOM (RF-23).
 */
export interface GridPoint {
  dateOnly: string;
  minuteOfDay: number | null;
}

/** The two mutation gestures the optimistic reconciliation layer tracks (§4/RF-21). */
export type MutationKind = "drop" | "resize";

/**
 * First day of the week, using JS `Date.getDay()` numbering: 0 = Sunday … 6 = Saturday.
 * AetherCal defaults to Monday (1).
 */
export type FirstDayOfWeek = 0 | 1 | 2 | 3 | 4 | 5 | 6;
