/**
 * The resource timeline (RF-28) as a user meets it: rows of resources, bars on a horizontal time
 * axis, collapsible groups, and — the point of the whole view — dragging an event from one resource
 * onto another.
 */
import type { CalendarEvent, CalendarResource, EventDropPayload } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import type * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-13"; // Monday
const NOW = "2026-07-13T09:00:00";

const HOSTS: CalendarResource[] = [
  { id: "h1", title: "Dr. Rivas" },
  { id: "h2", title: "Dr. Sosa" },
];

const GROUPED: CalendarResource[] = [
  { id: "a1", title: "Room A1", groupId: "Clinic A" },
  { id: "a2", title: "Room A2", groupId: "Clinic A" },
  { id: "b1", title: "Room B1", groupId: "Clinic B" },
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

/** Give a track a real measured width, which jsdom otherwise reports as 0. */
function measure(el: Element, left = 0, width = 300): void {
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
    left,
    width,
    top: 0,
    height: 40,
    right: left + width,
    bottom: 40,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

const tracks = (container: HTMLElement): HTMLElement[] =>
  Array.from(container.querySelectorAll<HTMLElement>(".aethercal-tl-track"));

/**
 * The RESOURCE row headers, in display order. A group header is a `rowheader` too (it heads its own
 * row), so the role alone would mix the two — this asks for the resource rows specifically.
 */
const rowNames = (container: HTMLElement): (string | null)[] =>
  Array.from(container.querySelectorAll(".aethercal-tl-rowhead")).map((el) => el.textContent);

describe("timeline — rows and bars", () => {
  it("renders one row per resource, with the resource names as row headers", () => {
    const { container } = renderTimeline();
    expect(rowNames(container)).toEqual(["Dr. Rivas", "Dr. Sosa"]);
  });

  it("places the event in its own resource's row and nowhere else", () => {
    const { container } = renderTimeline();
    const [rivas, sosa] = tracks(container);
    expect(within(rivas!).getByTitle("Consulta")).toBeTruthy();
    expect(within(sosa!).queryByTitle("Consulta")).toBeNull();
  });

  it("positions the bar by its fraction of the axis, not by a fixed slot", () => {
    const { container } = renderTimeline();
    const bar = container.querySelector<HTMLElement>(".aethercal-tl-event");
    // 09:00 on day 1 of a 3-day full-day axis => (9/24)/3 = 12.5%; one hour wide => (1/24)/3.
    // Compared numerically: these are floats, so the exact decimal string is not the contract.
    expect(Number.parseFloat(bar?.style.left ?? "")).toBeCloseTo(12.5, 9);
    expect(Number.parseFloat(bar?.style.width ?? "")).toBeCloseTo((100 / 24) * (1 / 3), 9);
  });

  it("surfaces an event with an unknown resource in an unassigned row rather than dropping it", () => {
    const orphan: CalendarEvent = {
      id: "orphan",
      title: "Huérfano",
      start: "2026-07-13T11:00:00",
      end: "2026-07-13T12:00:00",
      resourceId: "ghost",
    };
    const { container, getByTitle } = renderTimeline({ events: [...events, orphan] });
    expect(rowNames(container)).toEqual(["Dr. Rivas", "Dr. Sosa", "Unassigned"]);
    expect(getByTitle("Huérfano")).toBeTruthy();
  });

  it("stacks two overlapping bookings of the same resource into separate lanes", () => {
    const overlapping: CalendarEvent[] = [
      ...events,
      {
        id: "e2",
        title: "Segunda",
        start: "2026-07-13T09:30:00",
        end: "2026-07-13T10:30:00",
        resourceId: "h1",
      },
    ];
    const { container } = renderTimeline({ events: overlapping });
    const lanes = Array.from(
      container.querySelectorAll<HTMLElement>(".aethercal-tl-track [data-lane]"),
    ).map((el) => el.dataset.lane);
    expect(new Set(lanes)).toEqual(new Set(["0", "1"]));
  });
});

describe("timeline — grouping and collapse", () => {
  it("renders a collapsible header per group, expanded by default", () => {
    const { getAllByRole } = renderTimeline({ resources: GROUPED, events: [] });
    const toggles = getAllByRole("button", { name: /Clinic/ });
    expect(toggles.map((t) => t.getAttribute("aria-expanded"))).toEqual(["true", "true"]);
    expect(toggles[0]?.textContent).toContain("2 resources");
  });

  it("hides a group's rows when its header is clicked, and brings them back", () => {
    const { container, getByRole } = renderTimeline({ resources: GROUPED, events: [] });
    expect(rowNames(container)).toEqual(["Room A1", "Room A2", "Room B1"]);

    fireEvent.click(getByRole("button", { name: /Clinic A/ }));
    expect(getByRole("button", { name: /Clinic A/ }).getAttribute("aria-expanded")).toBe("false");
    expect(rowNames(container)).toEqual(["Room B1"]);

    fireEvent.click(getByRole("button", { name: /Clinic A/ }));
    expect(rowNames(container)).toEqual(["Room A1", "Room A2", "Room B1"]);
  });

  it("honours defaultCollapsedGroupIds and reports every toggle to the host", () => {
    const onToggleGroup = vi.fn();
    const { container, getByRole } = renderTimeline({
      resources: GROUPED,
      events: [],
      defaultCollapsedGroupIds: ["Clinic A"],
      onToggleGroup,
    });
    expect(rowNames(container)).toEqual(["Room B1"]);

    fireEvent.click(getByRole("button", { name: /Clinic A/ }));
    expect(onToggleGroup).toHaveBeenCalledWith("Clinic A", false);
  });
});

describe("timeline — dragging an event onto another resource", () => {
  it("emits a drop payload naming the TARGET resource row", () => {
    const onEventDrop = vi.fn<(p: EventDropPayload) => void>();
    const { container } = renderTimeline({ onEventDrop });

    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    const [, sosaTrack] = tracks(container);
    measure(sosaTrack!);

    fireEvent.dragStart(bar, { dataTransfer: { setData: vi.fn(), effectAllowed: "" } });
    fireEvent.dragOver(sosaTrack!);
    fireEvent.drop(sosaTrack!, {
      clientX: 37.5, // 12.5% of 300px => 09:00 on day 1: the same time, a different row
      dataTransfer: { getData: () => "e1" },
    });

    expect(onEventDrop).toHaveBeenCalledTimes(1);
    const payload = onEventDrop.mock.calls[0]![0];
    expect(payload.resourceId).toBe("h2"); // moved to Dr. Sosa
    expect(payload.id).toBe("e1");
    expect(payload.start).toBe("2026-07-13T09:00:00");
  });

  it("recomputes the TIME from where along the axis it was dropped", () => {
    const onEventDrop = vi.fn<(p: EventDropPayload) => void>();
    const { container } = renderTimeline({ onEventDrop });

    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    const [rivasTrack] = tracks(container);
    measure(rivasTrack!);

    fireEvent.dragStart(bar, { dataTransfer: { setData: vi.fn(), effectAllowed: "" } });
    fireEvent.drop(rivasTrack!, {
      clientX: 50, // 1/6 across a 3-day axis => halfway through day 1 => 12:00
      dataTransfer: { getData: () => "e1" },
    });

    const payload = onEventDrop.mock.calls[0]![0];
    expect(payload.start).toBe("2026-07-13T12:00:00");
    expect(payload.end).toBe("2026-07-13T13:00:00"); // the 1h duration is preserved
    expect(payload.resourceId).toBe("h1");
  });

  it("does not accept a drop on the unassigned row (it is a drag SOURCE, not a target)", () => {
    const onEventDrop = vi.fn();
    const orphan: CalendarEvent = {
      id: "orphan",
      title: "Huérfano",
      start: "2026-07-13T11:00:00",
      end: "2026-07-13T12:00:00",
    };
    const { container } = renderTimeline({ events: [...events, orphan], onEventDrop });
    const unassignedTrack = tracks(container)[2]!;
    measure(unassignedTrack);

    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    fireEvent.dragStart(bar, { dataTransfer: { setData: vi.fn(), effectAllowed: "" } });
    fireEvent.drop(unassignedTrack, { clientX: 50, dataTransfer: { getData: () => "e1" } });

    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("never moves a locked event", () => {
    const onEventDrop = vi.fn();
    const locked: CalendarEvent[] = [{ ...events[0]!, editable: false }];
    const { container } = renderTimeline({ events: locked, onEventDrop });
    const [, sosaTrack] = tracks(container);
    measure(sosaTrack!);

    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    expect(bar.getAttribute("draggable")).toBe("false");
    fireEvent.drop(sosaTrack!, { clientX: 50, dataTransfer: { getData: () => "e1" } });
    expect(onEventDrop).not.toHaveBeenCalled();
  });
});

describe("timeline — empty state", () => {
  it("renders an accessible empty state and NO dangling activedescendant when there is nothing to show", () => {
    // With no resources and no events there is no cell to be active. Pointing
    // `aria-activedescendant` at an id that does not exist strands a screen reader on a dead anchor,
    // so the attribute must be absent — and the grid must still say something.
    const { getByRole, container } = renderTimeline({ resources: [], events: [] });
    const grid = getByRole("grid");

    expect(grid.hasAttribute("aria-activedescendant")).toBe(false);
    expect(getByRole("gridcell").textContent).toBe("No resources to show");
    expect(container.querySelectorAll(".aethercal-tl-rowhead")).toHaveLength(0);
  });

  it("keeps the activedescendant pointing at a real node once rows exist", () => {
    const { getByRole, container } = renderTimeline();
    const id = getByRole("grid").getAttribute("aria-activedescendant")!;
    expect(container.querySelector(`[id="${id}"]`)).toBeTruthy();
  });

  it("does not crash navigating an empty timeline by keyboard", () => {
    const { getByRole } = renderTimeline({ resources: [], events: [] });
    const grid = getByRole("grid");
    for (const key of ["ArrowDown", "ArrowUp", "ArrowRight", "Enter", "Escape"]) {
      fireEvent.keyDown(grid, { key });
    }
    expect(grid.hasAttribute("aria-activedescendant")).toBe(false);
  });
});

describe("timeline — no dishonest drag affordance", () => {
  it("is not draggable when the host wired no onEventDrop", () => {
    // Otherwise the user drags the bar, drops it, and nothing happens — a silent no-op in the
    // interaction layer, which is worse than having no affordance at all.
    const { container } = renderTimeline();
    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    expect(bar.getAttribute("draggable")).toBe("false");
  });

  it("is draggable once onEventDrop is wired", () => {
    const { container } = renderTimeline({ onEventDrop: () => {} });
    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    expect(bar.getAttribute("draggable")).toBe("true");
  });

  it("refuses to start a drag gesture when there is no onEventDrop", () => {
    const { container } = renderTimeline();
    const bar = container.querySelector<HTMLElement>("[data-event-id='e1']")!;
    const setData = vi.fn();

    fireEvent.dragStart(bar, { dataTransfer: { setData, effectAllowed: "" } });

    // The gesture never starts: nothing is written to the dataTransfer, and the grid never enters
    // the dragging state.
    expect(setData).not.toHaveBeenCalled();
    expect(container.querySelector(".aethercal-timeline.is-dragging")).toBeNull();
  });
});

describe("timeline — creating on a row", () => {
  it("names the row a drag-to-create was drawn on", () => {
    const onRangeSelect = vi.fn();
    const { container } = renderTimeline({ events: [], onRangeSelect });
    const [, sosaTrack] = tracks(container);
    measure(sosaTrack!);

    fireEvent.pointerDown(sosaTrack!, { button: 0, pointerId: 1, clientX: 0 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 50 });
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect.mock.calls[0]![0]).toMatchObject({ resourceId: "h2", allDay: false });
  });
});
