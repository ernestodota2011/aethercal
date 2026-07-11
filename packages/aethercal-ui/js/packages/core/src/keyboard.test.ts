/**
 * Headless keyboard-geometry tests (F2-E a11y, AetherCal-06 §3/§7).
 *
 * These pure helpers back keyboard navigation (moving the active cell around a 2-D grid) and
 * keyboard drag (stepping a move/resize target by whole days or snapped minutes) without any DOM,
 * so the React layer only has to translate a key press into an intent and call them (RF-23).
 */
import { describe, expect, it } from "vitest";
import {
  addCalendarDays,
  clampMinuteToWindow,
  nextGridIndex,
  stepInstantMinutes,
} from "./index";
import { resolveTimeGridConfig } from "./timeGrid";

describe("nextGridIndex (2-D grid navigation)", () => {
  const rows = 6;
  const cols = 7; // a 6x7 month grid (42 cells)

  it("moves left/right within the row and clamps at the row edges", () => {
    expect(nextGridIndex(10, "ArrowRight", rows, cols)).toBe(11);
    expect(nextGridIndex(10, "ArrowLeft", rows, cols)).toBe(9);
    expect(nextGridIndex(7, "ArrowLeft", rows, cols)).toBe(7); // start of row 1, clamped
    expect(nextGridIndex(13, "ArrowRight", rows, cols)).toBe(13); // end of row 1, clamped
    expect(nextGridIndex(0, "ArrowLeft", rows, cols)).toBe(0); // clamped
    expect(nextGridIndex(41, "ArrowRight", rows, cols)).toBe(41); // clamped
  });

  it("moves up/down by a full row and clamps at the edges", () => {
    expect(nextGridIndex(10, "ArrowDown", rows, cols)).toBe(17);
    expect(nextGridIndex(10, "ArrowUp", rows, cols)).toBe(3);
    expect(nextGridIndex(3, "ArrowUp", rows, cols)).toBe(3); // top row, clamped
    expect(nextGridIndex(38, "ArrowDown", rows, cols)).toBe(38); // bottom row, clamped
  });

  it("Home/End jump to the start/end of the current row", () => {
    expect(nextGridIndex(10, "Home", rows, cols)).toBe(7); // row 1 starts at 7
    expect(nextGridIndex(10, "End", rows, cols)).toBe(13); // row 1 ends at 13
    expect(nextGridIndex(0, "Home", rows, cols)).toBe(0);
    expect(nextGridIndex(41, "End", rows, cols)).toBe(41);
  });

  it("returns the current index unchanged for an unhandled key", () => {
    expect(nextGridIndex(10, "Tab", rows, cols)).toBe(10);
  });

  it("works for a single-column grid (time-grid day view has 1 column)", () => {
    // 24 rows x 1 col: up/down step one row, left/right clamp (nowhere to go).
    expect(nextGridIndex(5, "ArrowDown", 24, 1)).toBe(6);
    expect(nextGridIndex(5, "ArrowLeft", 24, 1)).toBe(5);
  });
});

describe("addCalendarDays", () => {
  it("adds and subtracts whole calendar days", () => {
    expect(addCalendarDays("2026-07-15", 1)).toBe("2026-07-16");
    expect(addCalendarDays("2026-07-15", -1)).toBe("2026-07-14");
    expect(addCalendarDays("2026-07-15", 7)).toBe("2026-07-22");
  });

  it("rolls across month and year boundaries", () => {
    expect(addCalendarDays("2026-07-31", 1)).toBe("2026-08-01");
    expect(addCalendarDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addCalendarDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("is a no-op for a zero delta", () => {
    expect(addCalendarDays("2026-07-15", 0)).toBe("2026-07-15");
  });
});

describe("clampMinuteToWindow", () => {
  const config = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 }); // 480..1080

  it("keeps a minute inside the visible window", () => {
    expect(clampMinuteToWindow(600, config)).toBe(600);
  });

  it("clamps to the window start and end", () => {
    expect(clampMinuteToWindow(0, config)).toBe(480);
    expect(clampMinuteToWindow(2000, config)).toBe(1080);
  });
});

describe("stepInstantMinutes (day+minute as one instant)", () => {
  const full = resolveTimeGridConfig({ dayStartHour: 0, dayEndHour: 24 }); // 0..1440

  it("steps within a day when no boundary is crossed", () => {
    expect(stepInstantMinutes("2026-07-15", 600, 15, full)).toEqual({
      dateOnly: "2026-07-15",
      minuteOfDay: 615,
    });
  });

  it("rolls to the previous day when a step goes below midnight (end-at-midnight resize)", () => {
    // An event ending exactly at 00:00 shortened by 15 min -> 23:45 of the previous day.
    expect(stepInstantMinutes("2026-07-16", 0, -15, full)).toEqual({
      dateOnly: "2026-07-15",
      minuteOfDay: 1425,
    });
  });

  it("keeps 1440 as the day's end (midnight) rather than rolling", () => {
    expect(stepInstantMinutes("2026-07-15", 1425, 15, full)).toEqual({
      dateOnly: "2026-07-15",
      minuteOfDay: 1440,
    });
  });

  it("rolls to the next day when a step goes past midnight", () => {
    expect(stepInstantMinutes("2026-07-15", 1440, 15, full)).toEqual({
      dateOnly: "2026-07-16",
      minuteOfDay: 15,
    });
  });

  it("clamps to the visible window after rolling (narrowed window)", () => {
    const business = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 }); // 480..1080
    // A within-day step blocked at the window edge stays put (no cross-midnight roll).
    expect(stepInstantMinutes("2026-07-15", 480, -15, business)).toEqual({
      dateOnly: "2026-07-15",
      minuteOfDay: 480,
    });
  });
});
