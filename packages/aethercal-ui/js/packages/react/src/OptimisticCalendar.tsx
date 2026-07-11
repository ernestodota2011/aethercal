/**
 * A ready-to-use reconciling calendar: `AetherCalendar` wired to `useOptimisticEvents` (F2-D).
 *
 * Give it the authoritative `events` and an async `mutate`; it renders the events with any in-flight
 * optimistic change applied, drives drag/resize through the reconciliation loop (immediate update →
 * commit with the server's new `revision`, or rollback on rejection/timeout), and forwards the
 * non-mutation events (`on_range_select` / `on_event_click` / `on_context_menu`) straight to the
 * caller. Consumers who need finer control can compose `useOptimisticEvents` + `AetherCalendar`
 * themselves; this is the batteries-included surface for the demo playground and the admin.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import * as React from "react";
import { AetherCalendar, type AetherCalendarProps } from "./AetherCalendar";
import {
  type CalendarMutation,
  type MutationResult,
  useOptimisticEvents,
} from "./useOptimisticEvents";

export interface OptimisticCalendarProps
  extends Omit<
    AetherCalendarProps,
    "events" | "onEventDrop" | "onEventResize" | "pendingIds" | "rolledBackIds"
  > {
  /** Authoritative (server-confirmed) events. */
  events: readonly CalendarEvent[];
  /** Perform a drag/resize mutation server-side; resolve with the new revision, reject to roll back. */
  mutate: (mutation: CalendarMutation) => Promise<MutationResult>;
  /** Budget before an unanswered mutation is rolled back. Default 8000ms. */
  timeoutMs?: number;
  /** How long a rolled-back event stays flagged for the flash animation. Default 900ms. */
  rollbackFlashMs?: number;
  /** Idempotency-id generator (injectable for tests). */
  generateId?: () => string;
}

export function OptimisticCalendar({
  events,
  mutate,
  timeoutMs,
  rollbackFlashMs,
  generateId,
  ...rest
}: OptimisticCalendarProps): React.JSX.Element {
  const {
    events: displayEvents,
    pendingIds,
    rolledBackIds,
    submit,
  } = useOptimisticEvents({
    events,
    mutate,
    ...(timeoutMs !== undefined ? { timeoutMs } : {}),
    ...(rollbackFlashMs !== undefined ? { rollbackFlashMs } : {}),
    ...(generateId ? { generateId } : {}),
  });

  return (
    <AetherCalendar
      {...rest}
      events={displayEvents}
      pendingIds={pendingIds}
      rolledBackIds={rolledBackIds}
      onEventDrop={(payload) => submit("drop", payload)}
      onEventResize={(payload) => submit("resize", payload)}
    />
  );
}

export default OptimisticCalendar;
