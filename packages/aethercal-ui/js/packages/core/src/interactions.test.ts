import { describe, expect, it } from "vitest";
import { resolveTimeGridConfig } from "./timeGrid";
import {
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  fractionToMinuteOfDay,
} from "./interactions";
import type { CalendarEvent, GridPoint } from "./types";

const full = resolveTimeGridConfig({});
const business = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 });

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start" | "end">): CalendarEvent {
  return { title: partial.title ?? partial.id, ...partial };
}

describe("fractionToMinuteOfDay", () => {
  it("maps the middle of a full day to noon", () => {
    expect(fractionToMinuteOfDay(0.5, full)).toBe(720);
  });

  it("maps within a narrowed business-hours window (start offset applies)", () => {
    // 8:00 + 0.5 * 10h = 13:00 -> 780 minutes.
    expect(fractionToMinuteOfDay(0.5, business)).toBe(780);
    expect(fractionToMinuteOfDay(0, business)).toBe(480);
  });

  it("snaps to the nearest 15-minute step by default", () => {
    // 0.51 * 1440 = 734.4 -> nearest 15 = 735 (12:15).
    expect(fractionToMinuteOfDay(0.51, full)).toBe(735);
  });

  it("honors a custom snap step", () => {
    // 0.51 * 1440 = 734.4 -> nearest 30 = 720.
    expect(fractionToMinuteOfDay(0.51, full, 30)).toBe(720);
  });

  it("clamps an out-of-range fraction to the window bounds", () => {
    expect(fractionToMinuteOfDay(-1, business)).toBe(480);
    expect(fractionToMinuteOfDay(2, business)).toBe(1080);
  });

  it("snaps relative to the window start with a step that does not divide it (45-min on 08:00)", () => {
    // fraction 0 must land exactly on the window start (08:00 = 480), not be pulled onto a
    // midnight-anchored 45-min grid point (which would give 08:15).
    expect(fractionToMinuteOfDay(0, business, 45)).toBe(480);
    // fraction 1 snaps to the last 45-min grid point within the window (480 + 13*45 = 1065 = 17:45).
    expect(fractionToMinuteOfDay(1, business, 45)).toBe(1065);
    // an interior fraction stays on the window-anchored grid: 08:00 + 90min = 09:30.
    expect(fractionToMinuteOfDay(0.15, business, 45)).toBe(570);
  });
});

describe("computeMovedRange", () => {
  const e = evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", revision: 4 });

  it("with a null minute preserves the time-of-day (day-only move)", () => {
    expect(computeMovedRange(e, "2026-07-17", null)).toEqual({
      id: "e1",
      start: "2026-07-17T10:00:00",
      end: "2026-07-17T11:00:00",
      revision: 4,
    });
  });

  it("with a minute changes BOTH the day and the time-of-day, preserving duration", () => {
    // Dropped on 2026-07-17 at 13:00 -> keeps the 1h duration.
    expect(computeMovedRange(e, "2026-07-17", 780)).toEqual({
      id: "e1",
      start: "2026-07-17T13:00:00",
      end: "2026-07-17T14:00:00",
      revision: 4,
    });
  });

  it("omits revision when the event has none", () => {
    const bare = evt({ id: "x", start: "2026-07-15T10:00:00", end: "2026-07-15T10:30:00" });
    expect(computeMovedRange(bare, "2026-07-16", 600)).toEqual({
      id: "x",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T10:30:00",
    });
  });

  it("snaps the moved start to the grid, dropping sub-minute seconds on both endpoints", () => {
    const secs = evt({ id: "s", start: "2026-07-15T10:00:37", end: "2026-07-15T10:45:37" });
    expect(computeMovedRange(secs, "2026-07-16", 720)).toEqual({
      id: "s",
      start: "2026-07-16T12:00:00",
      end: "2026-07-16T12:45:00",
    });
  });

  it("preserves the LOCAL (wall-clock) duration across a spring-forward DST day", () => {
    // The vitest env runs in America/New_York; 2026-03-08 skips 02:00->03:00. A 2h event moved to
    // start at 01:00 that day keeps its visible 2h length (01:00->03:00) — a raw ms-add would render
    // it 01:00->04:00 (absolute 2h, DST hour shifted onto the wall clock).
    const e = evt({ id: "e", start: "2026-03-07T10:00:00", end: "2026-03-07T12:00:00" });
    expect(computeMovedRange(e, "2026-03-08", 60)).toEqual({
      id: "e",
      start: "2026-03-08T01:00:00",
      end: "2026-03-08T03:00:00",
    });
  });
});

describe("computeResize", () => {
  const e = evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", revision: 7 });

  it("drags the END edge to a later minute, keeping the start", () => {
    expect(computeResize(e, "end", "2026-07-15", 750)).toEqual({
      id: "e1",
      start: "2026-07-15T10:00:00",
      end: "2026-07-15T12:30:00",
      revision: 7,
    });
  });

  it("drags the START edge to an earlier minute, keeping the end", () => {
    expect(computeResize(e, "start", "2026-07-15", 570)).toEqual({
      id: "e1",
      start: "2026-07-15T09:30:00",
      end: "2026-07-15T11:00:00",
      revision: 7,
    });
  });

  it("enforces a minimum duration when the END is dragged above the start", () => {
    // Target minute 600 (10:00) == start; clamp end to start + 15 min.
    expect(computeResize(e, "end", "2026-07-15", 600).end).toBe("2026-07-15T10:15:00");
  });

  it("enforces a minimum duration when the START is dragged past the end", () => {
    // Target minute 660 (11:00) == end; clamp start to end - 15 min.
    expect(computeResize(e, "start", "2026-07-15", 660).start).toBe("2026-07-15T10:45:00");
  });

  it("honors a custom minimum duration", () => {
    expect(computeResize(e, "end", "2026-07-15", 600, { minDurationMinutes: 30 }).end).toBe(
      "2026-07-15T10:30:00",
    );
  });

  it("clamps the minimum duration in LOCAL minutes on a spring-forward DST day", () => {
    // America/New_York skips 02:00->03:00 on 2026-03-08. Dragging the end back to the start clamps
    // to start + 15 LOCAL minutes; the result is wall-clock 01:15, not a raw-millisecond artifact.
    const dst = evt({ id: "e", start: "2026-03-08T01:00:00", end: "2026-03-08T04:00:00" });
    const out = computeResize(dst, "end", "2026-03-08", 60); // drag end down to 01:00 == start
    expect(out).toMatchObject({ start: "2026-03-08T01:00:00", end: "2026-03-08T01:15:00" });
  });
});

describe("computeRangeSelection", () => {
  it("selects a single all-day cell as a one-day exclusive range", () => {
    const p: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: null };
    expect(computeRangeSelection(p, p)).toEqual({
      start: "2026-07-15T00:00:00",
      end: "2026-07-16T00:00:00",
      allDay: true,
    });
  });

  it("selects an all-day range across days, order-independent", () => {
    const a: GridPoint = { dateOnly: "2026-07-17", minuteOfDay: null };
    const b: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: null };
    const expected = { start: "2026-07-15T00:00:00", end: "2026-07-18T00:00:00", allDay: true };
    expect(computeRangeSelection(a, b)).toEqual(expected);
    expect(computeRangeSelection(b, a)).toEqual(expected);
  });

  it("selects a timed range, order-independent", () => {
    const a: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: 660 };
    const b: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: 540 };
    const expected = { start: "2026-07-15T09:00:00", end: "2026-07-15T11:00:00", allDay: false };
    expect(computeRangeSelection(a, b)).toEqual(expected);
    expect(computeRangeSelection(b, a)).toEqual(expected);
  });

  it("gives a zero-length timed click a default minimum slot", () => {
    const p: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: 540 };
    expect(computeRangeSelection(p, p)).toEqual({
      start: "2026-07-15T09:00:00",
      end: "2026-07-15T09:15:00",
      allDay: false,
    });
  });
});
