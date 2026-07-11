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
 * `revision` is echoed from the dragged event when present; `client_mutation_id` is filled by
 * the reconciliation layer in F2-D (idempotency) — omitted in F2-A.
 */
export interface EventDropPayload {
  id: string;
  start: string;
  end: string;
  revision?: number;
  client_mutation_id?: string;
}

/**
 * First day of the week, using JS `Date.getDay()` numbering: 0 = Sunday … 6 = Saturday.
 * AetherCal defaults to Monday (1).
 */
export type FirstDayOfWeek = 0 | 1 | 2 | 3 | 4 | 5 | 6;
