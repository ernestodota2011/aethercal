import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { formatLocalDateTime, parseLocalDateTime } from "./dateMath";
import {
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  fractionToMinuteOfDay,
} from "./interactions";
import { resolveTimeGridConfig } from "./timeGrid";
import type { CalendarEvent, GridPoint } from "./types";

// A fixed set of July 2026 days (no DST transition) keeps wall-clock duration math exact.
const DAYS = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"];
const dayArb = fc.constantFrom(...DAYS);

/** An event on a given day at a given start minute with a given duration (minutes). */
function makeEvent(dateOnly: string, startMinute: number, durationMinutes: number, revision?: number): CalendarEvent {
  const midnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
  const start = new Date(midnight.getFullYear(), midnight.getMonth(), midnight.getDate(), 0, startMinute, 0);
  const end = new Date(start.getTime() + durationMinutes * 60_000);
  return {
    id: "e",
    title: "e",
    start: formatLocalDateTime(start),
    end: formatLocalDateTime(end),
    ...(revision !== undefined ? { revision } : {}),
  };
}

const configArb = fc
  .record({ dayStartHour: fc.integer({ min: 0, max: 12 }), span: fc.integer({ min: 1, max: 12 }) })
  .map(({ dayStartHour, span }) =>
    resolveTimeGridConfig({ dayStartHour, dayEndHour: Math.min(24, dayStartHour + span) }),
  );

describe("fractionToMinuteOfDay — invariants", () => {
  it("always lands within the visible window, for any fraction", () => {
    fc.assert(
      fc.property(fc.double({ min: -5, max: 5, noNaN: true }), configArb, (fraction, config) => {
        const minute = fractionToMinuteOfDay(fraction, config);
        expect(minute).toBeGreaterThanOrEqual(config.dayStartHour * 60);
        expect(minute).toBeLessThanOrEqual(config.dayEndHour * 60);
      }),
    );
  });
});

describe("computeResize — invariants", () => {
  const eventArb = fc.tuple(
    dayArb,
    fc.integer({ min: 0, max: 20 * 60 }),
    fc.integer({ min: 15, max: 3 * 60 }),
  );

  it("dragging the END keeps the start fixed and never inverts the event", () => {
    fc.assert(
      fc.property(eventArb, fc.integer({ min: 0, max: 24 * 60 }), ([day, s, d], minute) => {
        const event = makeEvent(day, s, d);
        const out = computeResize(event, "end", day, minute);
        expect(out.start).toBe(event.start);
        expect(parseLocalDateTime(out.end).getTime()).toBeGreaterThan(
          parseLocalDateTime(out.start).getTime(),
        );
      }),
    );
  });

  it("dragging the START keeps the end fixed and never inverts the event", () => {
    fc.assert(
      fc.property(eventArb, fc.integer({ min: 0, max: 24 * 60 }), ([day, s, d], minute) => {
        const event = makeEvent(day, s, d);
        const out = computeResize(event, "start", day, minute);
        expect(out.end).toBe(event.end);
        expect(parseLocalDateTime(out.end).getTime()).toBeGreaterThan(
          parseLocalDateTime(out.start).getTime(),
        );
      }),
    );
  });
});

describe("computeMovedRange — invariants", () => {
  it("preserves the exact duration and echoes revision, on any timed drop", () => {
    fc.assert(
      fc.property(
        dayArb,
        fc.integer({ min: 0, max: 20 * 60 }),
        fc.integer({ min: 15, max: 3 * 60 }),
        dayArb,
        fc.integer({ min: 0, max: 24 * 60 }),
        (srcDay, s, d, dstDay, minute) => {
          const event = makeEvent(srcDay, s, d, 9);
          const out = computeMovedRange(event, dstDay, minute);
          const origDur =
            parseLocalDateTime(event.end).getTime() - parseLocalDateTime(event.start).getTime();
          const newDur = parseLocalDateTime(out.end).getTime() - parseLocalDateTime(out.start).getTime();
          expect(newDur).toBe(origDur);
          expect(out.revision).toBe(9);
        },
      ),
    );
  });
});

describe("computeRangeSelection — invariants", () => {
  const pointArb: fc.Arbitrary<GridPoint> = fc.record({
    dateOnly: dayArb,
    minuteOfDay: fc.oneof(fc.constant<number | null>(null), fc.integer({ min: 0, max: 1439 })),
  });

  it("start is never after end, regardless of drag direction", () => {
    fc.assert(
      fc.property(pointArb, pointArb, (a, b) => {
        const out = computeRangeSelection(a, b);
        expect(parseLocalDateTime(out.start).getTime()).toBeLessThanOrEqual(
          parseLocalDateTime(out.end).getTime(),
        );
      }),
    );
  });

  it("is order-independent (swapping anchor and current yields the same range)", () => {
    fc.assert(
      fc.property(pointArb, pointArb, (a, b) => {
        expect(computeRangeSelection(a, b)).toEqual(computeRangeSelection(b, a));
      }),
    );
  });
});
