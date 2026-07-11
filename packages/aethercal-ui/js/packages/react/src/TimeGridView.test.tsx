import type { CalendarEvent } from "@aethercal/calendar-core";
import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";
import { TimeGridView } from "./TimeGridView";

afterEach(cleanup);

// July 2026: the 15th is a Wednesday. Monday-first week => 2026-07-13 .. 2026-07-19.
const ANCHOR = "2026-07-15";

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start" | "end">): CalendarEvent {
  return { title: partial.title ?? partial.id, ...partial };
}

function fakeDataTransfer() {
  const store: Record<string, string> = {};
  return {
    dropEffect: "",
    effectAllowed: "",
    setData: (k: string, v: string) => {
      store[k] = v;
    },
    getData: (k: string) => store[k] ?? "",
  };
}

/**
 * jsdom never lays anything out, so a column's rect is all-zeros by default. Stubbing a known
 * height lets the pointer geometry (clientY -> fraction -> minute) be exercised deterministically:
 * with a 480px column over the default 24h window, 1px = 3min, so clientY 240 -> 12:00.
 */
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

describe("week view — structure & a11y", () => {
  it("renders an ARIA grid (not the F2-A placeholder)", () => {
    const { getByRole, queryByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} events={[]} />,
    );
    expect(getByRole("grid")).toBeTruthy();
    expect(queryByRole("status")).toBeNull();
  });

  it("renders exactly 7 day column headers", () => {
    const { getAllByRole } = render(<AetherCalendar view="week" anchor={ANCHOR} events={[]} />);
    expect(getAllByRole("columnheader")).toHaveLength(7);
  });

  it("renders one hour label per hour of the default 0..24 window", () => {
    const { container } = render(<AetherCalendar view="week" anchor={ANCHOR} events={[]} />);
    expect(container.querySelectorAll(".aethercal-tg-hour")).toHaveLength(24);
  });

  it("honors a narrowed business-hours window", () => {
    const { container } = render(
      <AetherCalendar view="week" anchor={ANCHOR} events={[]} dayStartHour={8} dayEndHour={18} />,
    );
    expect(container.querySelectorAll(".aethercal-tg-hour")).toHaveLength(10);
    // Column height is driven by the visible-hours count so a narrowed window is not 24h tall.
    const grid = container.querySelector(".aethercal-timegrid") as HTMLElement;
    expect(grid.style.getPropertyValue("--ac-tg-hours")).toBe("10");
  });

  it("does not expose event blocks as buttons (no keyboard action until F2-D/E)", () => {
    const { queryByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    expect(queryByRole("button")).toBeNull();
  });
});

describe("week view — timed & all-day placement", () => {
  it("positions a timed event in its day column by vertical fraction", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" })]}
      />,
    );
    const block = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    expect(block).toBeTruthy();
    // 09:00 of a 24h window => top 37.5%, one hour => height ~4.1667%.
    expect(block.style.top).toBe("37.5%");
    expect(block.style.height).toContain("4.16");
    // It lives inside the 2026-07-15 timed column.
    const col = block.closest(".aethercal-tg-col") as HTMLElement;
    expect(col.getAttribute("data-date")).toBe("2026-07-15");
  });

  it("places an all-day event in the all-day row, never in the time grid", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "hol", title: "Holiday", start: "2026-07-15", end: "2026-07-15", allDay: true })]}
      />,
    );
    const allDayCell = container.querySelector(
      '.aethercal-tg-allday-cell[data-date="2026-07-15"]',
    ) as HTMLElement;
    expect(within(allDayCell).getByText("Holiday")).toBeTruthy();
    // The timed body must not contain the all-day event.
    const body = container.querySelector(".aethercal-tg-body") as HTMLElement;
    expect(body.querySelector('[data-event-id="hol"]')).toBeNull();
  });

  it("splits two overlapping events into half-width lanes", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[
          evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T10:30:00" }),
          evt({ id: "b", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" }),
        ]}
      />,
    );
    const a = container.querySelector('[data-event-id="a"]') as HTMLElement;
    const b = container.querySelector('[data-event-id="b"]') as HTMLElement;
    expect(a.style.width).toBe("50%");
    expect(b.style.width).toBe("50%");
    expect(new Set([a.style.left, b.style.left])).toEqual(new Set(["0%", "50%"]));
  });
});

describe("day view — one column reusing the week engine", () => {
  it("renders exactly one day column", () => {
    const { getAllByRole } = render(<AetherCalendar view="day" anchor={ANCHOR} events={[]} />);
    expect(getAllByRole("columnheader")).toHaveLength(1);
  });

  it("shows the anchor's own day and positions that day's events", () => {
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={[
          evt({ id: "today", start: "2026-07-15T13:00:00", end: "2026-07-15T14:00:00" }),
          evt({ id: "other", start: "2026-07-16T13:00:00", end: "2026-07-16T14:00:00" }),
        ]}
      />,
    );
    const col = container.querySelector(".aethercal-tg-col") as HTMLElement;
    expect(col.getAttribute("data-date")).toBe("2026-07-15");
    expect(container.querySelector('[data-event-id="today"]')).toBeTruthy();
    expect(container.querySelector('[data-event-id="other"]')).toBeNull();
  });
});

describe("week view — now indicator", () => {
  it("draws the now line only in the column matching now's date and inside the window", () => {
    const { container } = render(
      <AetherCalendar view="week" anchor={ANCHOR} events={[]} now="2026-07-15T12:00:00" />,
    );
    const indicators = container.querySelectorAll(".aethercal-now-indicator");
    expect(indicators).toHaveLength(1);
    const col = (indicators[0] as HTMLElement).closest(".aethercal-tg-col") as HTMLElement;
    expect(col.getAttribute("data-date")).toBe("2026-07-15");
    expect((indicators[0] as HTMLElement).style.top).toBe("50%");
  });

  it("draws no now line when now is outside the visible window", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[]}
        dayStartHour={13}
        dayEndHour={18}
        now="2026-07-15T12:00:00"
      />,
    );
    expect(container.querySelectorAll(".aethercal-now-indicator")).toHaveLength(0);
  });

  it("draws no now line when now falls on a day outside the visible week", () => {
    const { container } = render(
      <AetherCalendar view="week" anchor={ANCHOR} events={[]} now="2026-08-01T12:00:00" />,
    );
    expect(container.querySelectorAll(".aethercal-now-indicator")).toHaveLength(0);
  });
});

describe("week view — live now line when uncontrolled", () => {
  it("initializes to the system clock and advances the line as time passes", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date(2026, 6, 15, 12, 0, 0)); // Wed 2026-07-15, noon local
      const { container } = render(<AetherCalendar view="week" anchor={ANCHOR} events={[]} />);
      let indicator = container.querySelector(".aethercal-now-indicator") as HTMLElement;
      expect(indicator).toBeTruthy();
      expect(indicator.style.top).toBe("50%");
      // Later the (uncontrolled) line must have moved, not frozen at mount. Advancing the fake
      // clock by the 60s tick interval fires it once; land it exactly on 15:00 for a round check.
      act(() => {
        vi.setSystemTime(new Date(2026, 6, 15, 14, 59, 0));
        vi.advanceTimersByTime(60_000);
      });
      indicator = container.querySelector(".aethercal-now-indicator") as HTMLElement;
      expect(indicator.style.top).toBe("62.5%"); // 15 / 24
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("week view — drag to reschedule (reuses the drag machine)", () => {
  it("emits onEventDrop with a day-shifted range when dropped on another day column", () => {
    const onEventDrop = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onEventDrop={onEventDrop}
      />,
    );
    const block = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    const target = container.querySelector('.aethercal-tg-col[data-date="2026-07-17"]') as HTMLElement;
    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(block, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(onEventDrop).toHaveBeenCalledTimes(1);
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-17T10:00:00", end: "2026-07-17T11:00:00" }),
    );
  });

  it("ignores a drop that was never preceded by an in-calendar dragStart", () => {
    const onEventDrop = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onEventDrop={onEventDrop}
      />,
    );
    const target = container.querySelector('.aethercal-tg-col[data-date="2026-07-17"]') as HTMLElement;
    const dataTransfer = fakeDataTransfer();
    dataTransfer.setData("text/plain", "e1");
    fireEvent.drop(target, { dataTransfer });
    expect(onEventDrop).not.toHaveBeenCalled();
  });
});

describe("TimeGridView used directly (public export) is self-contained", () => {
  it("installs both the base and the time-grid stylesheets on its own", () => {
    // Used without AetherCalendar, it must still inject the base `--ac-*` tokens its CSS relies on.
    for (const id of ["aethercal-calendar-styles", "aethercal-timegrid-styles"]) {
      document.getElementById(id)?.remove();
    }
    render(
      <TimeGridView view="day" days={["2026-07-15"]} events={[]} locale="en" config={{}} now={new Date(2026, 6, 15, 12)} />,
    );
    expect(document.getElementById("aethercal-calendar-styles")).not.toBeNull();
    expect(document.getElementById("aethercal-timegrid-styles")).not.toBeNull();
  });
});

describe("F2-D — resize (duration) via pointer handles", () => {
  it("renders start & end resize handles on an editable event when onEventResize is wired", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onEventResize={vi.fn()}
      />,
    );
    const block = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    expect(block.querySelector('.aethercal-tg-resize-handle[data-edge="start"]')).toBeTruthy();
    expect(block.querySelector('.aethercal-tg-resize-handle[data-edge="end"]')).toBeTruthy();
  });

  it("renders NO resize handle when no onEventResize handler is given (no dishonest affordance)", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    expect(container.querySelector(".aethercal-tg-resize-handle")).toBeNull();
  });

  it("renders NO resize handle on a locked (editable:false) event even with a handler", () => {
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", editable: false })]}
        onEventResize={vi.fn()}
      />,
    );
    expect(container.querySelector(".aethercal-tg-resize-handle")).toBeNull();
  });

  it("dragging the end handle emits onEventResize with the snapped new end (and echoes revision)", () => {
    const onEventResize = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", revision: 2 })]}
        onEventResize={onEventResize}
      />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    const handle = container.querySelector('.aethercal-tg-resize-handle[data-edge="end"]') as HTMLElement;
    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientY: 220 });
    fireEvent.pointerMove(window, { clientY: 240 }); // fraction 0.5 -> 720min -> 12:00
    fireEvent.pointerUp(window, {});
    expect(onEventResize).toHaveBeenCalledTimes(1);
    expect(onEventResize).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T12:00:00", revision: 2 }),
    );
  });
});

describe("F2-D — range select (create) via pointer drag on empty space", () => {
  it("drag-selecting empty time-grid space emits on_range_select for the covered slot", () => {
    const onRangeSelect = vi.fn();
    const { container } = render(
      <AetherCalendar view="day" anchor={ANCHOR} events={[]} onRangeSelect={onRangeSelect} />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    fireEvent.pointerDown(col, { pointerId: 1, button: 0, clientY: 120 }); // 0.25 -> 360 -> 06:00
    fireEvent.pointerMove(window, { clientY: 240 }); // 0.5 -> 720 -> 12:00
    fireEvent.pointerUp(window, {});
    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T06:00:00",
      end: "2026-07-15T12:00:00",
      allDay: false,
    });
  });

  it("a plain click on empty space (no drag) does not create a range", () => {
    const onRangeSelect = vi.fn();
    const { container } = render(
      <AetherCalendar view="day" anchor={ANCHOR} events={[]} onRangeSelect={onRangeSelect} />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    fireEvent.pointerDown(col, { pointerId: 1, button: 0, clientY: 120 });
    fireEvent.pointerUp(window, {});
    expect(onRangeSelect).not.toHaveBeenCalled();
  });
});

describe("F2-D — context menu & click", () => {
  it("right-click on an event emits on_context_menu with its id", () => {
    const onContextMenu = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onContextMenu={onContextMenu}
      />,
    );
    fireEvent.contextMenu(container.querySelector('[data-event-id="e1"]') as HTMLElement);
    expect(onContextMenu).toHaveBeenCalledWith({ id: "e1" });
  });

  it("right-click on empty grid emits on_context_menu with a start slot", () => {
    const onContextMenu = vi.fn();
    const { container } = render(
      <AetherCalendar view="day" anchor={ANCHOR} events={[]} onContextMenu={onContextMenu} />,
    );
    const col = container.querySelector('.aethercal-tg-col[data-date="2026-07-15"]') as HTMLElement;
    stubRect(col, 480);
    fireEvent.contextMenu(col, { clientY: 240 }); // 12:00
    expect(onContextMenu).toHaveBeenCalledWith({ start: "2026-07-15T12:00:00" });
  });

  it("clicking an event emits on_event_click with its id", () => {
    const onEventClick = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onEventClick={onEventClick}
      />,
    );
    fireEvent.click(container.querySelector('[data-event-id="e1"]') as HTMLElement);
    expect(onEventClick).toHaveBeenCalledWith({ id: "e1" });
  });
});

describe("F2-D — drag-to-move changes the time-of-day, not only the day", () => {
  it("dropping on a timed column at a vertical position sets both the day and the hour", () => {
    const onEventDrop = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        onEventDrop={onEventDrop}
      />,
    );
    const block = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    const target = container.querySelector('.aethercal-tg-col[data-date="2026-07-17"]') as HTMLElement;
    stubRect(target, 480);
    const dt = fakeDataTransfer();
    fireEvent.dragStart(block, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt, clientY: 240 }); // 0.5 -> 12:00
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-17T12:00:00", end: "2026-07-17T13:00:00" }),
    );
  });
});

describe("F2-D — optimistic status classes", () => {
  it("marks pending and rolled-back events with status classes", () => {
    const { container } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        events={[
          evt({ id: "p", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
          evt({ id: "r", start: "2026-07-15T11:00:00", end: "2026-07-15T12:00:00" }),
        ]}
        pendingIds={new Set(["p"])}
        rolledBackIds={new Set(["r"])}
      />,
    );
    expect((container.querySelector('[data-event-id="p"]') as HTMLElement).className).toContain("is-pending");
    expect((container.querySelector('[data-event-id="r"]') as HTMLElement).className).toContain(
      "is-rolledback",
    );
  });
});
