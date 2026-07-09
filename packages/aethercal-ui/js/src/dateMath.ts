/**
 * Pure date-grid math for the calendar core: month/week grid generation and the
 * start/end recomputation used when an event is dropped on a new day.
 *
 * Deliberately dependency-free (no date library) and display-only — timezone-correct
 * scheduling math lives in `aethercal-core` (Python); this only rearranges what's already
 * on screen in the browser's local time.
 */
import type { CalendarEvent, EventDropPayload } from "./types";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Parse "YYYY-MM-DD[THH:mm[:ss]]" as local time (never as UTC, unlike bare `new Date(iso)`). */
function parseLocalDateTime(iso: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
  if (!match) {
    throw new Error(`invalid ISO datetime: ${iso}`);
  }
  const [, y, mo, d, h, mi, s] = match;
  return new Date(
    Number(y),
    Number(mo) - 1,
    Number(d),
    Number(h ?? "0"),
    Number(mi ?? "0"),
    Number(s ?? "0"),
  );
}

function formatLocalDateTime(dt: Date): string {
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}T${pad2(dt.getHours())}:${pad2(dt.getMinutes())}:${pad2(dt.getSeconds())}`;
}

/** The date-only portion ("YYYY-MM-DD") of an ISO datetime string. */
export function toDateOnly(iso: string): string {
  const dt = parseLocalDateTime(iso);
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
}

/** Monday-first day-of-week index: 0 = Monday ... 6 = Sunday. */
function mondayIndex(dt: Date): number {
  return (dt.getDay() + 6) % 7;
}

/** Midnight on the Monday of the week containing `dt`. */
export function startOfWeek(dt: Date): Date {
  const start = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  start.setDate(start.getDate() - mondayIndex(start));
  return start;
}

function dateRange(start: Date, count: number): string[] {
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return toDateOnly(formatLocalDateTime(d));
  });
}

/** The 7 date-only strings (Mon..Sun) for the week containing `anchor`. */
export function getWeekGridDays(anchor: Date): string[] {
  return dateRange(startOfWeek(anchor), 7);
}

/** The 42 date-only strings (6 full weeks, Mon-first) covering the month containing `anchor`. */
export function getMonthGridDays(anchor: Date): string[] {
  const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  return dateRange(startOfWeek(firstOfMonth), 42);
}

/**
 * Recompute an event's start/end after it is dropped on `targetDateOnly`, preserving both
 * its time-of-day and its duration.
 */
export function computeDroppedRange(
  event: CalendarEvent,
  targetDateOnly: string,
): EventDropPayload {
  const originalStart = parseLocalDateTime(event.start);
  const originalEnd = parseLocalDateTime(event.end);
  const durationMs = originalEnd.getTime() - originalStart.getTime();

  const target = parseLocalDateTime(targetDateOnly);
  const newStart = new Date(
    target.getFullYear(),
    target.getMonth(),
    target.getDate(),
    originalStart.getHours(),
    originalStart.getMinutes(),
    originalStart.getSeconds(),
  );
  const newEnd = new Date(newStart.getTime() + durationMs);

  return {
    id: event.id,
    start: formatLocalDateTime(newStart),
    end: formatLocalDateTime(newEnd),
  };
}
