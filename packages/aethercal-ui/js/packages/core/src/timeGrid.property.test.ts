import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { formatLocalDateTime, parseLocalDateTime } from "./dateMath";
import {
  buildTimeGrid,
  layoutDayColumn,
  resolveTimeGridConfig,
  splitAllDay,
} from "./timeGrid";
import type { CalendarEvent } from "./types";

const DAY = "2026-07-15";
const MIDNIGHT = parseLocalDateTime(`${DAY}T00:00:00`).getTime();
const FULL_DAY = resolveTimeGridConfig();

function makeEvent(id: number, startMin: number, endMin: number, allDay = false): CalendarEvent {
  const start = formatLocalDateTime(new Date(MIDNIGHT + startMin * 60_000));
  const end = formatLocalDateTime(new Date(MIDNIGHT + endMin * 60_000));
  return allDay ? { id: `e${id}`, title: "t", start, end, allDay: true } : { id: `e${id}`, title: "t", start, end };
}

function minutesOf(iso: string): number {
  return (parseLocalDateTime(iso).getTime() - MIDNIGHT) / 60_000;
}

function overlaps(a: CalendarEvent, b: CalendarEvent): boolean {
  return minutesOf(a.start) < minutesOf(b.end) && minutesOf(b.start) < minutesOf(a.end);
}

// Arbitrary same-day timed events (start within the day, positive-ish duration).
const timedEvents = fc
  .array(
    fc.record({ startMin: fc.integer({ min: 0, max: 1380 }), durMin: fc.integer({ min: 1, max: 180 }) }),
    { maxLength: 10 },
  )
  .map((specs) => specs.map((s, i) => makeEvent(i, s.startMin, s.startMin + s.durMin)));

// A run of strictly sequential (pairwise non-overlapping) events: each starts at/after the last end.
const sequentialEvents = fc
  .array(
    fc.record({ gapMin: fc.integer({ min: 0, max: 60 }), durMin: fc.integer({ min: 1, max: 60 }) }),
    { maxLength: 6 },
  )
  .map((specs) => {
    let cursor = 0;
    return specs.map((s, i) => {
      cursor += s.gapMin;
      const start = cursor;
      const end = cursor + s.durMin;
      cursor = end;
      return makeEvent(i, start, end);
    });
  });

describe("layoutDayColumn invariants", () => {
  it("never puts two overlapping events in the same lane", () => {
    fc.assert(
      fc.property(timedEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        for (let i = 0; i < blocks.length; i += 1) {
          for (let j = i + 1; j < blocks.length; j += 1) {
            if (blocks[i]!.lane === blocks[j]!.lane) {
              expect(overlaps(blocks[i]!.event, blocks[j]!.event)).toBe(false);
            }
          }
        }
      }),
    );
  });

  it("collapses a fully non-overlapping day into a single lane", () => {
    fc.assert(
      fc.property(sequentialEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        for (const block of blocks) {
          expect(block.lane).toBe(0);
          expect(block.laneCount).toBe(1);
        }
      }),
    );
  });

  it("keeps every lane index within its cluster's lane count", () => {
    fc.assert(
      fc.property(timedEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        for (const block of blocks) {
          expect(block.lane).toBeGreaterThanOrEqual(0);
          expect(block.lane).toBeLessThan(block.laneCount);
        }
      }),
    );
  });

  it("uses at least as many lanes as the peak simultaneous overlap", () => {
    fc.assert(
      fc.property(timedEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        if (blocks.length === 0) return;
        // Peak concurrency = max number of events live at any event's start instant.
        let peak = 0;
        for (const probe of events) {
          const at = minutesOf(probe.start);
          const live = events.filter((e) => minutesOf(e.start) <= at && at < minutesOf(e.end)).length;
          peak = Math.max(peak, live);
        }
        const maxLaneCount = Math.max(...blocks.map((b) => b.laneCount));
        expect(maxLaneCount).toBeGreaterThanOrEqual(peak);
      }),
    );
  });

  it("keeps vertical position monotonic with start time", () => {
    fc.assert(
      fc.property(timedEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        const byStart = [...blocks].sort(
          (a, b) => minutesOf(a.event.start) - minutesOf(b.event.start),
        );
        for (let i = 1; i < byStart.length; i += 1) {
          expect(byStart[i]!.topFraction).toBeGreaterThanOrEqual(byStart[i - 1]!.topFraction - 1e-9);
        }
      }),
    );
  });

  it("keeps every block inside the window (0 <= top, top + height <= 1)", () => {
    fc.assert(
      fc.property(timedEvents, (events) => {
        const blocks = layoutDayColumn(events, DAY, FULL_DAY);
        for (const block of blocks) {
          expect(block.topFraction).toBeGreaterThanOrEqual(0);
          expect(block.heightFraction).toBeGreaterThanOrEqual(0);
          expect(block.topFraction + block.heightFraction).toBeLessThanOrEqual(1 + 1e-9);
        }
      }),
    );
  });
});

describe("buildTimeGrid invariants", () => {
  const mixed = fc
    .array(
      fc.record({
        startMin: fc.integer({ min: 0, max: 1380 }),
        durMin: fc.integer({ min: 1, max: 120 }),
        allDay: fc.boolean(),
      }),
      { maxLength: 12 },
    )
    .map((specs) => specs.map((s, i) => makeEvent(i, s.startMin, s.startMin + s.durMin, s.allDay)));

  it("never lets an all-day event enter the time grid", () => {
    fc.assert(
      fc.property(mixed, (events) => {
        const grid = buildTimeGrid([DAY], events, FULL_DAY);
        const timedIds = new Set(grid.columns.flatMap((c) => c.timed.map((b) => b.event.id)));
        const { allDay } = splitAllDay(events);
        for (const e of allDay) expect(timedIds.has(e.id)).toBe(false);
      }),
    );
  });

  it("places every in-view event exactly once (all-day bucket XOR time grid)", () => {
    fc.assert(
      fc.property(mixed, (events) => {
        const grid = buildTimeGrid([DAY], events, FULL_DAY);
        for (const e of events) {
          const inAllDay = grid.columns.some((c) => c.allDay.some((a) => a.id === e.id));
          const inTimed = grid.columns.some((c) => c.timed.some((b) => b.event.id === e.id));
          expect(inAllDay !== inTimed).toBe(true); // exactly one is true
        }
      }),
    );
  });
});
