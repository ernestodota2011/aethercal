/**
 * Keyboard access + ARIA for the resource timeline (RF-28, RNF-7).
 *
 * The axes ARE the keyboard model: up/down moves between RESOURCES, left/right between days. The
 * load-bearing test here is the cross-resource grab — a keyboard user must be able to move an event
 * from one host to another, because that is the gesture the whole view exists for. A mouse-only
 * timeline would fail the a11y bar the other four views already clear.
 */
import type { CalendarEvent, CalendarResource, EventDropPayload } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render } from "@testing-library/react";
import type * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-13";
const NOW = "2026-07-13T09:00:00";

const HOSTS: CalendarResource[] = [
  { id: "h1", title: "Dr. Rivas" },
  { id: "h2", title: "Dr. Sosa" },
];

const events: CalendarEvent[] = [
  {
    id: "e1",
    title: "Consulta",
    start: "2026-07-13T09:00:00",
    end: "2026-07-13T10:00:00",
    resourceId: "h1",
  },
];

function renderTimeline(props: Partial<React.ComponentProps<typeof AetherCalendar>> = {}) {
  return render(
    <AetherCalendar
      view="timeline"
      anchor={ANCHOR}
      now={NOW}
      timelineDays={3}
      resources={HOSTS}
      events={events}
      {...props}
    />,
  );
}

const liveText = (container: HTMLElement): string =>
  container.querySelector("[aria-live]")?.textContent ?? "";

describe("timeline — ARIA structure", () => {
  it("is a single-tabstop grid with a keyboard description and an active descendant", () => {
    const { getByRole } = renderTimeline();
    const grid = getByRole("grid");
    expect(grid.getAttribute("tabindex")).toBe("0");
    expect(grid.getAttribute("aria-describedby")).toBeTruthy();
    expect(grid.getAttribute("aria-activedescendant")).toBeTruthy();
  });

  it("points aria-activedescendant at a node that actually exists", () => {
    // An activedescendant pointing at a node that is not rendered is a silent screen-reader dead end.
    // Matched by attribute, since React's useId contains colons that a bare #id selector cannot take.
    const { getByRole, container } = renderTimeline();
    const id = getByRole("grid").getAttribute("aria-activedescendant")!;
    expect(container.querySelector(`[id="${id}"]`)).toBeTruthy();
  });

  it("exposes a columnheader per visible day plus the resource column", () => {
    const { getAllByRole } = renderTimeline();
    expect(getAllByRole("columnheader")).toHaveLength(4); // 3 days + the resource column
  });

  it("makes the scrollable row body keyboard-focusable (scrollable-region-focusable)", () => {
    const { container } = renderTimeline();
    expect(container.querySelector(".aethercal-tl-body")?.getAttribute("tabindex")).toBe("0");
  });

  it("never exposes a status role (the live region is aria-live, not role=status)", () => {
    const { queryByRole } = renderTimeline();
    expect(queryByRole("status")).toBeNull();
  });

  it("does not expose an unwired event as a button (no ARIA lies)", () => {
    const { queryByRole } = renderTimeline();
    expect(queryByRole("button", { name: /Consulta/ })).toBeNull();
  });

  it("exposes a wired, editable event as a button", () => {
    const { getByRole } = renderTimeline({ onEventDrop: () => {} });
    expect(getByRole("button", { name: /Consulta/ })).toBeTruthy();
  });
});

describe("timeline — keyboard: moving an event to another resource", () => {
  it("grabs an event, steps it DOWN onto the next resource, and drops it there", () => {
    const onEventDrop = vi.fn<(p: EventDropPayload) => void>();
    const { getByRole } = renderTimeline({ onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" }); // row 1 (Dr. Rivas) -> into its events
    fireEvent.keyDown(grid, { key: "Enter" }); // grab the event
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // move it ACROSS to Dr. Sosa
    fireEvent.keyDown(grid, { key: "Enter" }); // drop

    expect(onEventDrop).toHaveBeenCalledTimes(1);
    const payload = onEventDrop.mock.calls[0]![0];
    expect(payload.resourceId).toBe("h2");
    expect(payload.id).toBe("e1");
    // Only the resource changed — the time is untouched.
    expect(payload.start).toBe("2026-07-13T09:00:00");
  });

  it("steps the TIME with left/right while grabbed (time runs horizontally here)", () => {
    const onEventDrop = vi.fn<(p: EventDropPayload) => void>();
    const { getByRole } = renderTimeline({ onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // +15 minutes
    fireEvent.keyDown(grid, { key: "Enter" });

    const payload = onEventDrop.mock.calls[0]![0];
    expect(payload.start).toBe("2026-07-13T09:15:00");
    expect(payload.end).toBe("2026-07-13T10:15:00"); // duration preserved
    expect(payload.resourceId).toBe("h1"); // stayed on its own row
  });

  it("announces the target resource as the event moves across rows", () => {
    const { getByRole, container } = renderTimeline({ onEventDrop: () => {} });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });

    expect(liveText(container)).toContain("Dr. Sosa");
  });

  it("reverts on Escape without emitting a mutation", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = renderTimeline({ onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "Escape" });

    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("confirming a grab without moving is a strict no-op", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = renderTimeline({ onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" }); // into the events
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "Enter" }); // confirm without ever stepping

    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("cannot step past the last resource row", () => {
    const onEventDrop = vi.fn<(p: EventDropPayload) => void>();
    const { getByRole } = renderTimeline({ onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // -> h2, the last row
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // blocked: there is nothing below it
    fireEvent.keyDown(grid, { key: "Enter" });

    expect(onEventDrop.mock.calls[0]![0].resourceId).toBe("h2");
  });

  it("never grabs a locked event", () => {
    const onEventDrop = vi.fn();
    const locked: CalendarEvent[] = [{ ...events[0]!, editable: false }];
    const { getByRole } = renderTimeline({ events: locked, onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "Enter" });

    expect(onEventDrop).not.toHaveBeenCalled();
  });
});

describe("timeline — keyboard: groups", () => {
  const GROUPED: CalendarResource[] = [
    { id: "a1", title: "Room A1", groupId: "Clinic A" },
    { id: "b1", title: "Room B1", groupId: "Clinic B" },
  ];

  it("toggles a group with Enter on its header and announces the new state", () => {
    const { getByRole, container } = renderTimeline({ resources: GROUPED, events: [] });
    const grid = getByRole("grid");

    // The cursor starts on the first item, which is the "Clinic A" group header.
    fireEvent.keyDown(grid, { key: "Enter" });

    expect(getByRole("button", { name: /Clinic A/ }).getAttribute("aria-expanded")).toBe("false");
    expect(liveText(container)).toContain("Clinic A");
    expect(container.querySelectorAll(".aethercal-tl-rowhead")).toHaveLength(1); // only Room B1
  });
});

describe("timeline — keyboard: creating on an empty cell", () => {
  it("creates on the active row and day, naming the resource", () => {
    const onRangeSelect = vi.fn();
    const { getByRole } = renderTimeline({ events: [], onRangeSelect });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "ArrowDown" }); // move to the second resource (Dr. Sosa)
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // move the day cursor to day 2
    fireEvent.keyDown(grid, { key: "Enter" }); // create there

    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect.mock.calls[0]![0]).toMatchObject({
      resourceId: "h2",
      start: "2026-07-14T00:00:00",
      allDay: false,
    });
  });

  it("creates on a FREE day of a row that is busy on ANOTHER day (keyboard/mouse parity)", () => {
    // `e1` sits on day 1 of Dr. Rivas. A mouse user can still click day 2 of that row and create
    // there. The keyboard must reach the same cell: asking "is the whole ROW empty?" froze every
    // other day of a resource the moment it held a single booking — a silent dead key, and a parity
    // regression in a component whose a11y is an acceptance criterion.
    const onRangeSelect = vi.fn();
    const { getByRole } = renderTimeline({ onRangeSelect }); // events = [e1], on day 1
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "ArrowRight" }); // day cursor -> day 2, which is FREE
    fireEvent.keyDown(grid, { key: "Enter" });

    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect.mock.calls[0]![0]).toMatchObject({
      resourceId: "h1", // the busy row
      start: "2026-07-14T00:00:00", // its free day
      allDay: false,
    });
  });

  it("still descends into the events when the cursor IS on the occupied day", () => {
    // The other half of the parity: Enter on a cell that holds a bar acts on the bar — exactly as a
    // click on a bar does — and must never create on top of it.
    const onRangeSelect = vi.fn();
    const onEventDrop = vi.fn();
    const { getByRole } = renderTimeline({ onRangeSelect, onEventDrop });
    const grid = getByRole("grid");

    fireEvent.keyDown(grid, { key: "Enter" }); // day 1 holds e1 -> descend into it
    fireEvent.keyDown(grid, { key: "Enter" }); // grab it
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // move it to the next resource
    fireEvent.keyDown(grid, { key: "Enter" }); // drop

    expect(onRangeSelect).not.toHaveBeenCalled(); // never created on top of the event
    expect(onEventDrop).toHaveBeenCalledTimes(1);
    expect(onEventDrop.mock.calls[0]![0]).toMatchObject({ id: "e1", resourceId: "h2" });
  });
});
