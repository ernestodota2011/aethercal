/**
 * Touch swipe-to-navigate (U-02): a fast, mostly-horizontal touch drag pages the visible period the
 * same way the toolbar's own prev/next buttons do — ADDITIVE, so the tests here split into two
 * halves: (1) the swipe itself recognizes correctly (direction, mouse/vertical/slow are ignored),
 * and (2) — the half that actually matters for "aditivo, no reemplaza" — the EXISTING drag-to-
 * create-range gesture on the time-grid view, which claims the very same touch surface, is
 * completely unaffected: a genuine vertical drag still creates a range exactly as before, and a
 * recognized swipe cleanly aborts that nested gesture instead of ALSO creating one.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

// July 2026: the 15th is a Wednesday. Monday-first week => 2026-07-13 .. 2026-07-19.
const ANCHOR = "2026-07-15";
const EVENTS: CalendarEvent[] = [];

/** jsdom never lays anything out — stub a column's rect so the day-view's pointer geometry
 * (clientY -> fraction -> minute) resolves deterministically, same convention as TimeGridView.test.tsx. */
function stubRect(el: HTMLElement, height: number, top = 0): void {
  el.getBoundingClientRect = () =>
    ({
      top,
      left: 0,
      right: 100,
      bottom: top + height,
      width: 100,
      height,
      x: 0,
      y: top,
      toJSON() {},
    }) as DOMRect;
}

describe("swipe navigation — recognizes a touch drag as a period step", () => {
  it("swiping left (finger moves toward negative X) steps to the NEXT month", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 220, clientY: 104 });
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "month",
      from: "2026-08-01T00:00:00",
      to: "2026-09-01T00:00:00",
    });
  });

  it("swiping right (finger moves toward positive X) steps to the PREVIOUS month", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 100, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 180, clientY: 96 });
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "month",
      from: "2026-06-01T00:00:00",
      to: "2026-07-01T00:00:00",
    });
  });

  it("steps the DAY view by one day, honoring the same period math as the toolbar", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 220, clientY: 100 });
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "day",
      from: "2026-07-16T00:00:00",
      to: "2026-07-17T00:00:00",
    });
  });

  it("ignores a mouse drag entirely — swipe is touch-only", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "mouse", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "mouse", clientX: 200, clientY: 100 });
    expect(onRangeChange).not.toHaveBeenCalled();
  });

  it("ignores a mostly-vertical touch drag — not a page-turn", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 320, clientY: 300 });
    expect(onRangeChange).not.toHaveBeenCalled();
  });

  it("ignores a touch drag shorter than the swipe distance threshold", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 290, clientY: 100 });
    expect(onRangeChange).not.toHaveBeenCalled();
  });

  it("does nothing when the toolbar is off (navigation=false) — swipe is bundled with it", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={EVENTS} onRangeChange={onRangeChange} />,
    );
    const grid = getByRole("grid");
    fireEvent.pointerDown(grid, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 100 });
    fireEvent.pointerMove(grid, { pointerId: 1, pointerType: "touch", clientX: 220, clientY: 100 });
    expect(onRangeChange).not.toHaveBeenCalled();
  });
});

describe("swipe navigation — coexists with the time-grid's own drag-to-create gesture", () => {
  it("a recognized swipe aborts an in-flight range-select instead of ALSO creating one", () => {
    const onRangeChange = vi.fn();
    const onRangeSelect = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
        onRangeSelect={onRangeSelect}
      />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    // Fired on the column (not window) so the event bubbles through BOTH the time-grid's own
    // onPointerDown (startSelect) AND the swipe wrapper above it — the real path a touch takes.
    fireEvent.pointerDown(col, { pointerId: 1, pointerType: "touch", clientX: 300, clientY: 120 });
    fireEvent.pointerMove(col, { pointerId: 1, pointerType: "touch", clientX: 220, clientY: 124 });
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(onRangeChange).toHaveBeenCalledWith({
      view: "day",
      from: "2026-07-16T00:00:00",
      to: "2026-07-17T00:00:00",
    });
    expect(onRangeSelect).not.toHaveBeenCalled();
  });

  it("a genuine vertical drag still creates a range — the swipe wrapper never engages", () => {
    const onRangeChange = vi.fn();
    const onRangeSelect = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
        onRangeSelect={onRangeSelect}
      />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    fireEvent.pointerDown(col, { pointerId: 1, pointerType: "touch", clientX: 200, clientY: 120 }); // 0.25 -> 06:00
    fireEvent.pointerMove(col, { pointerId: 1, pointerType: "touch", clientX: 202, clientY: 240 }); // 0.5 -> 12:00
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T06:00:00",
      end: "2026-07-15T12:00:00",
      allDay: false,
    });
    expect(onRangeChange).not.toHaveBeenCalled();
  });

  it("mouse drag-to-create on the same view is completely unaffected by the swipe wrapper", () => {
    const onRangeChange = vi.fn();
    const onRangeSelect = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={EVENTS}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
        onRangeSelect={onRangeSelect}
      />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    fireEvent.pointerDown(col, { pointerId: 1, button: 0, clientY: 120 });
    fireEvent.pointerMove(window, { pointerId: 1, clientY: 240 });
    fireEvent.pointerUp(window, {});

    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T06:00:00",
      end: "2026-07-15T12:00:00",
      allDay: false,
    });
    expect(onRangeChange).not.toHaveBeenCalled();
  });
});
