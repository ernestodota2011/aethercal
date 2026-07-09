/**
 * Shared types for the AetherCal calendar core.
 *
 * These mirror the props the Reflex wrapper (`aethercal.ui.calendar.Calendar`) passes in from
 * Python, and the payload shape it expects back on the `on_event_drop` callback.
 */

/** One calendar event as rendered by the grid. */
export interface CalendarEvent {
  /** Stable identifier, echoed back unchanged in drop payloads. */
  id: string;
  title: string;
  /** ISO 8601 datetime (local, no offset), e.g. "2026-07-09T14:00:00". */
  start: string;
  /** ISO 8601 datetime (local, no offset), exclusive. */
  end: string;
  /** Optional CSS color for the event chip. */
  color?: string;
}

export type CalendarView = "month" | "week";

/** Payload emitted by `onEventDrop` once a drag ends on a valid target day. */
export interface EventDropPayload {
  id: string;
  start: string;
  end: string;
}

export interface AetherCalendarProps {
  view?: CalendarView;
  events?: CalendarEvent[];
  onEventDrop?: (payload: EventDropPayload) => void;
}
