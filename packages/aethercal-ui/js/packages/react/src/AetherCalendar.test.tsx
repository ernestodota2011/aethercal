import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import type * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15"; // a Wednesday; July 2026 starts on a Wednesday.

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start">): CalendarEvent {
  return {
    title: partial.title ?? partial.id,
    end: partial.end ?? partial.start,
    ...partial,
  };
}

/** A minimal HTML5 DataTransfer stand-in that survives across dragStart -> drop. */
function fakeDataTransfer() {
  const store: Record<string, string> = {};
  return {
    dropEffect: "",
    effectAllowed: "",
    setData: (key: string, value: string) => {
      store[key] = value;
    },
    getData: (key: string) => store[key] ?? "",
  };
}

describe("AetherCalendar month view — structure & a11y", () => {
  it("renders an ARIA grid", () => {
    const { getByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    expect(getByRole("grid")).toBeTruthy();
  });

  it("renders exactly 7 column headers", () => {
    const { getAllByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    expect(getAllByRole("columnheader")).toHaveLength(7);
  });

  it("renders 42 day gridcells (6 weeks)", () => {
    const { getAllByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    expect(getAllByRole("gridcell")).toHaveLength(42);
  });
});

describe("AetherCalendar month view — i18n-ready labels (not hardcoded English)", () => {
  it("uses caller-provided weekday labels verbatim when given", () => {
    const labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
    const { getAllByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} weekdayLabels={labels} />,
    );
    const headers = getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(labels);
  });

  it("derives weekday labels from the locale when none are given (no hardcoded English)", () => {
    const { getAllByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} locale="es" />,
    );
    // Spanish short weekday names never match the English set — proves it is locale-driven.
    const headers = getAllByRole("columnheader").map((h) => (h.textContent ?? "").toLowerCase());
    expect(headers.some((h) => h.startsWith("mon") || h.startsWith("tue"))).toBe(false);
  });
});

describe("AetherCalendar month view — events", () => {
  it("renders an event's title inside its day cell", () => {
    const { container } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T10:30:00" })]}
      />,
    );
    const cell = container.querySelector('[data-date="2026-07-15"]') as HTMLElement;
    expect(within(cell).getByText("Consult")).toBeTruthy();
  });

  it("collapses overflow into '+N more' and expands on click", () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      evt({ id: `e${i}`, title: `Event ${i}`, start: "2026-07-15T09:00:00", end: "2026-07-15T09:30:00" }),
    );
    const { container, getByText } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} maxEventsPerDay={3} />,
    );
    const cell = container.querySelector('[data-date="2026-07-15"]') as HTMLElement;
    // 3 visible chips + a "+2 more" control.
    expect(within(cell).getAllByText(/^Event \d$/)).toHaveLength(3);
    const more = getByText("+2 more");
    fireEvent.click(more);
    expect(within(cell).getAllByText(/^Event \d$/)).toHaveLength(5);
  });

  it("formats the overflow label through the formatMore prop (i18n-ready)", () => {
    const events = Array.from({ length: 4 }, (_, i) =>
      evt({ id: `e${i}`, start: "2026-07-15T09:00:00", end: "2026-07-15T09:30:00" }),
    );
    const { getByText } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={events}
        maxEventsPerDay={2}
        formatMore={(n) => `${n} más`}
      />,
    );
    expect(getByText("2 más")).toBeTruthy();
  });
});

describe("AetherCalendar month view — drag to reschedule", () => {
  it("emits onEventDrop with the recomputed range when dropped on a new day", () => {
    const onEventDrop = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T10:30:00" })]}
        onEventDrop={onEventDrop}
      />,
    );
    const chip = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    const target = container.querySelector('[data-date="2026-07-20"]') as HTMLElement;
    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(chip, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    expect(onEventDrop).toHaveBeenCalledTimes(1);
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "e1",
        start: "2026-07-20T10:00:00",
        end: "2026-07-20T10:30:00",
      }),
    );
  });

  it("ignores a drop that never started as an in-calendar drag (no dragStart)", () => {
    const onEventDrop = vi.fn();
    const { container } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T10:30:00" })]}
        onEventDrop={onEventDrop}
      />,
    );
    const target = container.querySelector('[data-date="2026-07-20"]') as HTMLElement;
    // A foreign/synthetic drop that carries a valid event id but was never preceded by a
    // dragStart on the calendar must not reschedule anything.
    const dataTransfer = fakeDataTransfer();
    dataTransfer.setData("text/plain", "e1");
    fireEvent.drop(target, { dataTransfer });
    expect(onEventDrop).not.toHaveBeenCalled();
  });
});

describe("AetherCalendar month view — defensive prop normalization (JS consumers)", () => {
  it("falls back to a valid 7-column grid for an out-of-range firstDayOfWeek and bad labels", () => {
    const bad = {
      view: "month",
      anchor: ANCHOR,
      events: [],
      firstDayOfWeek: 9,
      weekdayLabels: ["a", "b"],
    } as unknown as React.ComponentProps<typeof AetherCalendar>;
    const { getAllByRole } = render(<AetherCalendar {...bad} />);
    expect(getAllByRole("columnheader")).toHaveLength(7);
    expect(getAllByRole("gridcell")).toHaveLength(42);
  });

  it("falls back maxEventsPerDay to a sane default when given a negative value", () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      evt({ id: `e${i}`, start: "2026-07-15T09:00:00", end: "2026-07-15T09:30:00" }),
    );
    const props = {
      view: "month",
      anchor: ANCHOR,
      events,
      maxEventsPerDay: -1,
    } as unknown as React.ComponentProps<typeof AetherCalendar>;
    const { getByText } = render(<AetherCalendar {...props} />);
    expect(getByText("+2 more")).toBeTruthy();
  });
});

describe("AetherCalendar month view — a11y honesty", () => {
  it("does not expose event chips as buttons (no keyboard action until F2-E)", () => {
    const { queryByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T10:30:00" })]}
      />,
    );
    // With a single event there is no "+N more" control either, so there must be no button at
    // all: a draggable chip claiming role="button" without a keyboard action is an ARIA lie.
    expect(queryByRole("button")).toBeNull();
  });
});

describe("AetherCalendar — views not yet implemented in F2-A", () => {
  it("shows an honest 'view unavailable' status for week/day/list (built in F2-B/C)", () => {
    const { getByRole, queryByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} events={[]} />,
    );
    expect(getByRole("status")).toBeTruthy();
    expect(queryByRole("grid")).toBeNull();
  });
});
