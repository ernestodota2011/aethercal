import { describe, expect, it } from "vitest";
import { computeDroppedRange, getMonthGridDays, getWeekGridDays, toDateOnly } from "../dateMath";
import type { CalendarEvent } from "../types";

describe("toDateOnly", () => {
  it("extracts the date portion of a full ISO datetime", () => {
    expect(toDateOnly("2026-07-09T14:30:00")).toBe("2026-07-09");
  });

  it("passes through a bare date string unchanged", () => {
    expect(toDateOnly("2026-07-09")).toBe("2026-07-09");
  });
});

describe("getWeekGridDays", () => {
  it("returns 7 consecutive days starting on Monday", () => {
    // 2026-07-09 is a Thursday.
    const days = getWeekGridDays(new Date(2026, 6, 9));
    expect(days).toEqual([
      "2026-07-06",
      "2026-07-07",
      "2026-07-08",
      "2026-07-09",
      "2026-07-10",
      "2026-07-11",
      "2026-07-12",
    ]);
  });
});

describe("getMonthGridDays", () => {
  it("returns 42 days (6 Monday-first weeks) covering the whole month", () => {
    const days = getMonthGridDays(new Date(2026, 6, 9));
    expect(days).toHaveLength(42);
    // July 2026 starts on a Wednesday, so the grid must lead in with the last
    // Monday of June.
    expect(days[0]).toBe("2026-06-29");
    // ... and every day of July must be present somewhere in the grid.
    for (let day = 1; day <= 31; day += 1) {
      expect(days).toContain(`2026-07-${String(day).padStart(2, "0")}`);
    }
  });
});

describe("computeDroppedRange", () => {
  const event: CalendarEvent = {
    id: "evt-1",
    title: "Consult",
    start: "2026-07-09T14:00:00",
    end: "2026-07-09T14:30:00",
  };

  it("shifts start/end to the target day, keeping time-of-day and duration", () => {
    const dropped = computeDroppedRange(event, "2026-07-15");
    expect(dropped).toEqual({
      id: "evt-1",
      start: "2026-07-15T14:00:00",
      end: "2026-07-15T14:30:00",
    });
  });

  it("preserves duration across a month boundary", () => {
    const dropped = computeDroppedRange(event, "2026-07-31");
    expect(dropped.start).toBe("2026-07-31T14:00:00");
    expect(dropped.end).toBe("2026-07-31T14:30:00");
  });

  it("is a no-op when dropped back on its own day", () => {
    expect(computeDroppedRange(event, "2026-07-09")).toEqual({
      id: "evt-1",
      start: event.start,
      end: event.end,
    });
  });
});
