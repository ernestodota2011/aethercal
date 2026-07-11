import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { buildAgenda } from "./agenda";
import { formatLocalDateTime, parseLocalDateTime, toDateOnly } from "./dateMath";
import type { CalendarEvent } from "./types";

// Calendar-plausible local dates (mirrors dateMath.property.test.ts: display geometry works on
// wall-time only, RF-23). Duration is bounded to a few days so the generated agendas stay small.
const anyDate = fc
  .record({
    year: fc.integer({ min: 1970, max: 2099 }),
    month: fc.integer({ min: 0, max: 11 }),
    day: fc.integer({ min: 1, max: 28 }),
  })
  .map(({ year, month, day }) => new Date(year, month, day, 0, 0, 0));

const eventArb: fc.Arbitrary<CalendarEvent> = fc
  .record({
    startDate: anyDate,
    startMinuteOfDay: fc.integer({ min: 0, max: 24 * 60 - 1 }),
    durationMin: fc.integer({ min: 0, max: 60 * 24 * 4 }),
    id: fc.uuid(),
    allDay: fc.boolean(),
  })
  .map(({ startDate, startMinuteOfDay, durationMin, id, allDay }): CalendarEvent => {
    const start = new Date(startDate);
    start.setMinutes(startMinuteOfDay);
    const end = new Date(start.getTime() + durationMin * 60_000);
    return { id, title: id, start: formatLocalDateTime(start), end: formatLocalDateTime(end), allDay };
  });

const eventsArb = fc.array(eventArb, { maxLength: 12 });

/** Local calendar day (0-padded "YYYY-MM-DD") of an ISO datetime — the trusted F2-A primitive. */
const startKeyOf = (event: CalendarEvent): string => toDateOnly(event.start);

/** Whole calendar-day difference between two "YYYY-MM-DD" keys. */
function dayDiff(a: string, b: string): number {
  const da = parseLocalDateTime(a).getTime();
  const db = parseLocalDateTime(b).getTime();
  return Math.round((db - da) / 86_400_000);
}

describe("buildAgenda invariants", () => {
  it("emits days in strictly ascending, unique chronological order", () => {
    fc.assert(
      fc.property(eventsArb, (events) => {
        const dates = buildAgenda(events).map((d) => d.date);
        for (let i = 1; i < dates.length; i += 1) {
          expect(dayDiff(dates[i - 1]!, dates[i]!)).toBeGreaterThan(0);
        }
      }),
    );
  });

  it("orders entries within a day by non-decreasing start time", () => {
    fc.assert(
      fc.property(eventsArb, (events) => {
        for (const day of buildAgenda(events)) {
          const starts = day.entries.map((e) => parseLocalDateTime(e.event.start).getTime());
          for (let i = 1; i < starts.length; i += 1) {
            expect(starts[i]!).toBeGreaterThanOrEqual(starts[i - 1]!);
          }
        }
      }),
    );
  });

  it("places every event on its local start day exactly once, flagged as non-continuation", () => {
    fc.assert(
      fc.property(eventsArb, (events) => {
        const agenda = buildAgenda(events);
        for (const event of events) {
          const startKey = startKeyOf(event);
          const day = agenda.find((d) => d.date === startKey);
          expect(day).toBeDefined();
          const startEntries = day!.entries.filter(
            (e) => e.event.id === event.id && !e.isContinuation,
          );
          expect(startEntries).toHaveLength(1);
        }
      }),
    );
  });

  it("never places an event before its start day, and flags match the day position", () => {
    fc.assert(
      fc.property(eventsArb, (events) => {
        const byId = new Map(events.map((e) => [e.id, e]));
        for (const day of buildAgenda(events)) {
          for (const entry of day.entries) {
            const startKey = startKeyOf(byId.get(entry.event.id)!);
            expect(dayDiff(startKey, day.date)).toBeGreaterThanOrEqual(0);
            expect(entry.isContinuation).toBe(day.date !== startKey);
          }
        }
      }),
    );
  });

  it("occupies a contiguous run of calendar days starting on the start day, no gaps", () => {
    fc.assert(
      fc.property(eventsArb, (events) => {
        const agenda = buildAgenda(events);
        for (const event of events) {
          const days = agenda
            .filter((d) => d.entries.some((e) => e.event.id === event.id))
            .map((d) => d.date);
          expect(days.length).toBeGreaterThanOrEqual(1);
          expect(days[0]).toBe(startKeyOf(event)); // agenda days are ascending -> first is earliest
          for (let i = 1; i < days.length; i += 1) {
            expect(dayDiff(days[i - 1]!, days[i]!)).toBe(1);
          }
          // Exactly one placement is flagged as the tail (continuesAfter === false).
          const tails = agenda.flatMap((d) =>
            d.entries.filter((e) => e.event.id === event.id && !e.continuesAfter),
          );
          expect(tails).toHaveLength(1);
        }
      }),
    );
  });

  it("keeps a same-day event on exactly one day", () => {
    const sameDayArb = fc
      .record({
        startDate: anyDate,
        startMinuteOfDay: fc.integer({ min: 0, max: 24 * 60 - 1 }),
        extraMin: fc.integer({ min: 0, max: 30 }),
        id: fc.uuid(),
      })
      .map(({ startDate, startMinuteOfDay, extraMin, id }): CalendarEvent => {
        const start = new Date(startDate);
        start.setMinutes(startMinuteOfDay);
        // Clamp the end to the same local day (before next midnight) so it can never spill over.
        const endOfDay = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate(), 23, 59, 0);
        const end = new Date(Math.min(start.getTime() + extraMin * 60_000, endOfDay.getTime()));
        return { id, title: id, start: formatLocalDateTime(start), end: formatLocalDateTime(end) };
      });
    fc.assert(
      fc.property(fc.array(sameDayArb, { maxLength: 8 }), (events) => {
        const agenda = buildAgenda(events);
        for (const event of events) {
          const days = agenda.filter((d) => d.entries.some((e) => e.event.id === event.id));
          expect(days).toHaveLength(1);
        }
      }),
    );
  });
});
