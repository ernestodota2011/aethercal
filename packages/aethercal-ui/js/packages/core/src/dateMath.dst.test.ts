import { describe, expect, it } from "vitest";
import { computeDroppedRange } from "./dateMath";
import type { CalendarEvent } from "./types";

// These tests need a DST-observing timezone. vitest.config.ts sets TZ=America/New_York, where
// 2026-03-08 is the spring-forward day (clocks jump 02:00 -> 03:00). Feature-detect it so the
// suite self-skips on a runtime that does not honor TZ (e.g. a bare UTC box) instead of failing.
const springForwardHonored = new Date(2026, 2, 8, 2, 30, 0).getHours() === 3;

describe.skipIf(!springForwardHonored)("computeDroppedRange across a DST spring-forward", () => {
  it("keeps each endpoint's wall-clock time (day-shift, not physical-duration drift)", () => {
    const event: CalendarEvent = {
      id: "e",
      title: "t",
      start: "2026-03-07T01:00:00",
      end: "2026-03-07T04:00:00",
    };
    const dropped = computeDroppedRange(event, "2026-03-08");
    // Correct: the wall times 01:00 and 04:00 are preserved. A raw-millisecond shift (the old
    // bug) would push the end to 05:00 because the 02:00->03:00 hour is skipped that day.
    expect(dropped.start).toBe("2026-03-08T01:00:00");
    expect(dropped.end).toBe("2026-03-08T04:00:00");
  });

  it("preserves wall times and day-span for a multi-day event spanning the transition", () => {
    const event: CalendarEvent = {
      id: "e",
      title: "t",
      start: "2026-03-07T23:00:00",
      end: "2026-03-09T09:00:00",
    };
    const dropped = computeDroppedRange(event, "2026-03-08");
    expect(dropped.start).toBe("2026-03-08T23:00:00");
    expect(dropped.end).toBe("2026-03-10T09:00:00");
  });
});
