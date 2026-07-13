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

/**
 * The calendar surfaces (AetherCal-06 §5): the four original views plus the RF-28 resource
 * `timeline` (resources in rows, time on the horizontal axis).
 */
export type CalendarView = "month" | "week" | "day" | "list" | "timeline";

/**
 * One row of the resource timeline (RF-28).
 *
 * Deliberately GENERIC: the component knows nothing about what a resource *is*. AetherCal's backend
 * maps resource → host, but the component takes an arbitrary array, so the same timeline serves
 * rooms, chairs, or machines without a code change.
 *
 * `groupId` is both the grouping KEY and the group's display LABEL (there is no separate title
 * field): a collapsible group is exactly "the resources that share this string", so a host passes a
 * human-readable value ("Clinic A") and gets a human-readable header for free.
 */
export interface CalendarResource {
  /** Stable identifier; an event joins this row via its `resourceId`. */
  id: string;
  title: string;
  /** Groups this resource under a collapsible header. The string doubles as the header label. */
  groupId?: string;
  /** Optional CSS color for the row's accent. */
  color?: string;
}

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
  /**
   * Which timeline resource row this event belongs to (RF-28). Optional: the other four views have
   * no resource dimension, and an event may legitimately be unassigned — the timeline surfaces
   * those in their own row rather than silently dropping them.
   */
  resourceId?: string;
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
  /**
   * The TARGET resource row, when the drop landed on the timeline (RF-28) — the whole point of
   * dragging between rows is that the host learns which resource the event was moved to. Absent for
   * the month/week/day views, which have no resource dimension.
   */
  resourceId?: string;
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
  /**
   * The resource row the selection was drawn on (RF-28), so the host can create the event against
   * the right resource. A selection that spans rows keeps the ANCHOR row's resource — one new event
   * belongs to one resource. Absent on the views with no resource dimension.
   */
  resourceId?: string;
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
 * A point on a calendar surface, as the interaction machine sees it — a day, an optional
 * minute-of-day, and (on the timeline) the resource row it lands on. `minuteOfDay` is `null` for a
 * date-granular surface (month cell / all-day rail) and a 0..1440 minute for the time grid.
 * Framework-agnostic: the React layer maps a pointer position to one of these; the core never
 * touches the DOM (RF-23).
 *
 * `resourceId` is what lets a gesture express "I dragged from one resource to another" (RF-28) —
 * without it a grid point could only ever name a day and a time, so the machines had no way to
 * represent a move ACROSS rows. It is `undefined` on the four views that have no resource axis.
 */
export interface GridPoint {
  dateOnly: string;
  minuteOfDay: number | null;
  resourceId?: string;
}

/** The two mutation gestures the optimistic reconciliation layer tracks (§4/RF-21). */
export type MutationKind = "drop" | "resize";

/**
 * First day of the week, using JS `Date.getDay()` numbering: 0 = Sunday … 6 = Saturday.
 * AetherCal defaults to Monday (1).
 */
export type FirstDayOfWeek = 0 | 1 | 2 | 3 | 4 | 5 | 6;
