import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  computeDroppedRange,
  formatLocalDateTime,
  getMonthGridDays,
  getWeekGridDays,
  parseLocalDateTime,
  toDateOnly,
} from "./dateMath";
import type { CalendarEvent } from "./types";

// A generator for calendar-plausible local dates (no DST-corner obsession: display geometry
// only works on wall-time, per RF-23 — real scheduling math lives in aethercal-core Python).
const anyDate = fc
  .record({
    year: fc.integer({ min: 1970, max: 2099 }),
    month: fc.integer({ min: 0, max: 11 }),
    day: fc.integer({ min: 1, max: 28 }),
  })
  .map(({ year, month, day }) => new Date(year, month, day, 12, 0, 0));

const firstDayOfWeek = fc.integer({ min: 0, max: 6 });

function dayDiff(a: string, b: string): number {
  const da = parseLocalDateTime(a).getTime();
  const db = parseLocalDateTime(b).getTime();
  return Math.round((db - da) / 86_400_000);
}

describe("getMonthGridDays invariants", () => {
  it("always yields exactly 42 day-only strings", () => {
    fc.assert(
      fc.property(anyDate, firstDayOfWeek, (anchor, fdow) => {
        expect(getMonthGridDays(anchor, fdow)).toHaveLength(42);
      }),
    );
  });

  it("yields strictly consecutive calendar days", () => {
    fc.assert(
      fc.property(anyDate, firstDayOfWeek, (anchor, fdow) => {
        const days = getMonthGridDays(anchor, fdow);
        for (let i = 1; i < days.length; i += 1) {
          expect(dayDiff(days[i - 1]!, days[i]!)).toBe(1);
        }
      }),
    );
  });

  it("starts the grid on the configured first day of week", () => {
    fc.assert(
      fc.property(anyDate, firstDayOfWeek, (anchor, fdow) => {
        const first = parseLocalDateTime(getMonthGridDays(anchor, fdow)[0]!);
        expect(first.getDay()).toBe(fdow);
      }),
    );
  });

  it("contains every day of the anchor's month", () => {
    fc.assert(
      fc.property(anyDate, firstDayOfWeek, (anchor, fdow) => {
        const days = new Set(getMonthGridDays(anchor, fdow));
        const y = anchor.getFullYear();
        const m = anchor.getMonth();
        const daysInMonth = new Date(y, m + 1, 0).getDate();
        for (let d = 1; d <= daysInMonth; d += 1) {
          const key = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
          expect(days.has(key)).toBe(true);
        }
      }),
    );
  });
});

describe("getWeekGridDays invariants", () => {
  it("always yields 7 consecutive days starting on the configured first day", () => {
    fc.assert(
      fc.property(anyDate, firstDayOfWeek, (anchor, fdow) => {
        const days = getWeekGridDays(anchor, fdow);
        expect(days).toHaveLength(7);
        expect(parseLocalDateTime(days[0]!).getDay()).toBe(fdow);
        for (let i = 1; i < 7; i += 1) {
          expect(dayDiff(days[i - 1]!, days[i]!)).toBe(1);
        }
      }),
    );
  });
});

describe("computeDroppedRange invariants", () => {
  const eventArb = fc
    .record({
      startDate: anyDate,
      startHour: fc.integer({ min: 0, max: 23 }),
      startMin: fc.integer({ min: 0, max: 59 }),
      durationMin: fc.integer({ min: 0, max: 60 * 24 * 3 }),
    })
    .map(({ startDate, startHour, startMin, durationMin }): CalendarEvent => {
      const start = new Date(startDate);
      start.setHours(startHour, startMin, 0, 0);
      const end = new Date(start.getTime() + durationMin * 60_000);
      return {
        id: "evt",
        title: "t",
        start: formatLocalDateTime(start),
        end: formatLocalDateTime(end),
      };
    });

  // Whole-day span between two ISO strings, DST-safe (midnight-to-midnight, rounded to days).
  const daySpan = (aIso: string, bIso: string): number => {
    const a = parseLocalDateTime(aIso);
    const b = parseLocalDateTime(bIso);
    const am = new Date(a.getFullYear(), a.getMonth(), a.getDate());
    const bm = new Date(b.getFullYear(), b.getMonth(), b.getDate());
    return Math.round((bm.getTime() - am.getTime()) / 86_400_000);
  };

  it("lands the start on the target day (any timezone)", () => {
    fc.assert(
      fc.property(eventArb, anyDate, (event, target) => {
        const targetDay = toDateOnly(formatLocalDateTime(target));
        expect(toDateOnly(computeDroppedRange(event, targetDay).start)).toBe(targetDay);
      }),
    );
  });

  it("preserves the whole-day span between start and end (DST-safe, no ms drift)", () => {
    fc.assert(
      fc.property(eventArb, anyDate, (event, target) => {
        const targetDay = toDateOnly(formatLocalDateTime(target));
        const dropped = computeDroppedRange(event, targetDay);
        expect(daySpan(dropped.start, dropped.end)).toBe(daySpan(event.start, event.end));
      }),
    );
  });

  it("keeps the event id stable", () => {
    fc.assert(
      fc.property(eventArb, anyDate, (event, target) => {
        const targetDay = toDateOnly(formatLocalDateTime(target));
        expect(computeDroppedRange(event, targetDay).id).toBe(event.id);
      }),
    );
  });
});
