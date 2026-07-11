import { describe, expect, it } from "vitest";
import {
  computeDroppedRange,
  formatLocalDateTime,
  getMonthGridDays,
  getWeekGridDays,
  parseLocalDateTime,
  startOfWeek,
  toDateOnly,
} from "./dateMath";
import type { CalendarEvent } from "./types";

const event: CalendarEvent = {
  id: "evt-1",
  title: "Consult",
  start: "2026-07-09T14:00:00",
  end: "2026-07-09T14:30:00",
};

describe("toDateOnly", () => {
  it("extracts the date portion of a full ISO datetime", () => {
    expect(toDateOnly("2026-07-09T14:30:00")).toBe("2026-07-09");
  });

  it("passes through a bare date string unchanged", () => {
    expect(toDateOnly("2026-07-09")).toBe("2026-07-09");
  });
});

describe("parseLocalDateTime / formatLocalDateTime", () => {
  it("parses as LOCAL wall-time, not UTC", () => {
    const dt = parseLocalDateTime("2026-07-09T14:00:00");
    expect(dt.getFullYear()).toBe(2026);
    expect(dt.getMonth()).toBe(6);
    expect(dt.getDate()).toBe(9);
    expect(dt.getHours()).toBe(14);
  });

  it("round-trips a datetime string", () => {
    expect(formatLocalDateTime(parseLocalDateTime("2026-07-09T14:00:00"))).toBe(
      "2026-07-09T14:00:00",
    );
  });

  it("throws on an unparseable string", () => {
    expect(() => parseLocalDateTime("not-a-date")).toThrow();
  });

  it("accepts bare dates and space-separated datetimes", () => {
    expect(formatLocalDateTime(parseLocalDateTime("2026-07-09"))).toBe("2026-07-09T00:00:00");
    expect(formatLocalDateTime(parseLocalDateTime("2026-07-09 14:30:00"))).toBe(
      "2026-07-09T14:30:00",
    );
  });

  it("rejects trailing garbage (anchored pattern)", () => {
    expect(() => parseLocalDateTime("2026-07-09T14:00:00Z")).toThrow();
    expect(() => parseLocalDateTime("2026-07-09xxx")).toThrow();
  });

  it("rejects out-of-range time components", () => {
    expect(() => parseLocalDateTime("2026-07-09T25:00:00")).toThrow();
    expect(() => parseLocalDateTime("2026-07-09T14:99:00")).toThrow();
  });

  it("rejects nonexistent calendar dates instead of silently rolling them over", () => {
    expect(() => parseLocalDateTime("2026-02-30")).toThrow();
    expect(() => parseLocalDateTime("2026-13-01")).toThrow();
    expect(() => parseLocalDateTime("2026-07-32")).toThrow();
  });
});

describe("getWeekGridDays", () => {
  it("returns 7 consecutive days starting on Monday by default", () => {
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

  it("honors a Sunday-first week (firstDayOfWeek = 0)", () => {
    const days = getWeekGridDays(new Date(2026, 6, 9), 0);
    expect(days[0]).toBe("2026-07-05"); // the Sunday before Thu Jul 9
    expect(days).toHaveLength(7);
    expect(days[6]).toBe("2026-07-11");
  });
});

describe("startOfWeek", () => {
  it("defaults to the Monday of the week", () => {
    const s = startOfWeek(new Date(2026, 6, 9));
    expect(toDateOnly(formatLocalDateTime(s))).toBe("2026-07-06");
  });

  it("returns the anchor itself when it is already the first day", () => {
    const s = startOfWeek(new Date(2026, 6, 6)); // a Monday
    expect(toDateOnly(formatLocalDateTime(s))).toBe("2026-07-06");
  });
});

describe("getMonthGridDays", () => {
  it("returns 42 days (6 Monday-first weeks) covering the whole month", () => {
    const days = getMonthGridDays(new Date(2026, 6, 9));
    expect(days).toHaveLength(42);
    // July 2026 starts on a Wednesday, so the grid leads in with the last Monday of June.
    expect(days[0]).toBe("2026-06-29");
    for (let day = 1; day <= 31; day += 1) {
      expect(days).toContain(`2026-07-${String(day).padStart(2, "0")}`);
    }
  });

  it("leads in with the correct Sunday when firstDayOfWeek = 0", () => {
    const days = getMonthGridDays(new Date(2026, 6, 9), 0);
    expect(days).toHaveLength(42);
    // July 1 2026 is a Wednesday; Sunday-first grid leads in with Sun Jun 28.
    expect(days[0]).toBe("2026-06-28");
  });
});

describe("computeDroppedRange", () => {
  it("shifts start/end to the target day, keeping time-of-day and duration", () => {
    expect(computeDroppedRange(event, "2026-07-15")).toEqual({
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

  it("echoes the event's revision when present", () => {
    const dropped = computeDroppedRange({ ...event, revision: 7 }, "2026-07-15");
    expect(dropped.revision).toBe(7);
  });
});
