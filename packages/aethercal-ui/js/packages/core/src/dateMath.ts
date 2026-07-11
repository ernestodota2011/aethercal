/**
 * Pure date-grid geometry for the calendar core: month/week grid generation and the
 * start/end recomputation used when an event is dropped on a new day.
 *
 * Deliberately dependency-free (no date library) and DISPLAY-ONLY — timezone-correct scheduling
 * math lives in `aethercal-core` (Python, RF-23). This only rearranges what is already on screen,
 * in the browser's local wall-time. Ported from the F0-10 spike's `dateMath.ts` and generalized
 * so the week can start on any day (F2-A: default Monday; other views/locales reuse it).
 */
import type { CalendarEvent, EventDropPayload } from "./types";

/** Default first day of the week (Monday), using `Date.getDay()` numbering (0 = Sunday). */
const DEFAULT_FIRST_DAY_OF_WEEK = 1;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * Parse "YYYY-MM-DD[THH:mm[:ss]]" as local time (never as UTC, unlike bare `new Date(iso)`).
 *
 * Strict: the pattern is anchored at both ends (no trailing garbage), time components are
 * range-checked, and the constructed date is verified against its parts so calendar overflow
 * (e.g. "2026-02-30", "2026-13-01") is rejected rather than silently normalized.
 */
export function parseLocalDateTime(iso: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(iso.trim());
  if (!match) {
    throw new Error(`invalid ISO datetime: ${iso}`);
  }
  const [, ys, mos, ds, hs, mis, ss] = match;
  const y = Number(ys);
  const mo = Number(mos);
  const d = Number(ds);
  const h = Number(hs ?? "0");
  const mi = Number(mis ?? "0");
  const s = Number(ss ?? "0");
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) {
    throw new Error(`out-of-range ISO datetime: ${iso}`);
  }
  const dt = new Date(y, mo - 1, d, h, mi, s);
  // Reject a date that does not exist on the calendar (Date would otherwise roll it forward).
  // Only the date parts are checked: a wall time that a DST spring-forward skips is normalized
  // by the platform and is left as-is (display-only; scheduling correctness is server-side, RF-23).
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
    throw new Error(`nonexistent calendar date: ${iso}`);
  }
  return dt;
}

/** Format a `Date` back to a naive local "YYYY-MM-DDTHH:mm:ss" string (inverse of parse). */
export function formatLocalDateTime(dt: Date): string {
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}T${pad2(dt.getHours())}:${pad2(dt.getMinutes())}:${pad2(dt.getSeconds())}`;
}

/** The date-only portion ("YYYY-MM-DD") of an ISO datetime string. */
export function toDateOnly(iso: string): string {
  const dt = parseLocalDateTime(iso);
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
}

/** Offset (0..6) of `dt` from the configured first day of the week. */
function dayOffsetFromWeekStart(dt: Date, firstDayOfWeek: number): number {
  return (dt.getDay() - firstDayOfWeek + 7) % 7;
}

/** Midnight on the first-day-of-week of the week containing `dt` (default Monday). */
export function startOfWeek(dt: Date, firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK): Date {
  const start = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  start.setDate(start.getDate() - dayOffsetFromWeekStart(start, firstDayOfWeek));
  return start;
}

function dateRange(start: Date, count: number): string[] {
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  });
}

/** The 7 date-only strings for the week containing `anchor` (first day configurable). */
export function getWeekGridDays(
  anchor: Date,
  firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK,
): string[] {
  return dateRange(startOfWeek(anchor, firstDayOfWeek), 7);
}

/** The 42 date-only strings (6 full weeks) covering the month containing `anchor`. */
export function getMonthGridDays(
  anchor: Date,
  firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK,
): string[] {
  const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  return dateRange(startOfWeek(firstOfMonth, firstDayOfWeek), 42);
}

/** Whole calendar-day difference between two dates, DST-safe (a day is 23–25h across DST). */
function calendarDayDelta(from: Date, to: Date): number {
  const fromMidnight = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const toMidnight = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((toMidnight.getTime() - fromMidnight.getTime()) / 86_400_000);
}

/**
 * Recompute an event's start/end after it is dropped on `targetDateOnly`, preserving each
 * endpoint's wall-clock time-of-day and the whole-day span between them.
 *
 * DST-safe by construction: it shifts BOTH endpoints by the same number of *calendar days*
 * (component-based date arithmetic) rather than adding a raw millisecond duration — so it never
 * gains or loses the DST hour. This is display-only wall-time rearrangement; authoritative
 * scheduling (and any zone-aware duration semantics) live server-side in aethercal-core (RF-23).
 * Echoes the event's `revision` through when present (the server assigns the *new* revision on
 * accept — F2-D).
 */
export function computeDroppedRange(
  event: CalendarEvent,
  targetDateOnly: string,
): EventDropPayload {
  const originalStart = parseLocalDateTime(event.start);
  const originalEnd = parseLocalDateTime(event.end);
  const target = parseLocalDateTime(targetDateOnly);
  const dayDelta = calendarDayDelta(originalStart, target);

  const newStart = new Date(
    originalStart.getFullYear(),
    originalStart.getMonth(),
    originalStart.getDate() + dayDelta,
    originalStart.getHours(),
    originalStart.getMinutes(),
    originalStart.getSeconds(),
  );
  const newEnd = new Date(
    originalEnd.getFullYear(),
    originalEnd.getMonth(),
    originalEnd.getDate() + dayDelta,
    originalEnd.getHours(),
    originalEnd.getMinutes(),
    originalEnd.getSeconds(),
  );

  const payload: EventDropPayload = {
    id: event.id,
    start: formatLocalDateTime(newStart),
    end: formatLocalDateTime(newEnd),
  };
  if (event.revision !== undefined) {
    payload.revision = event.revision;
  }
  return payload;
}
