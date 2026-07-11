import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, render, within } from "@testing-library/react";
import type * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start">): CalendarEvent {
  return { title: partial.title ?? partial.id, end: partial.end ?? partial.start, ...partial };
}

const dayGroups = (container: HTMLElement): HTMLElement[] =>
  Array.from(container.querySelectorAll<HTMLElement>(".aethercal-agenda-day"));

describe("AetherCalendar list/agenda view — list semantics (not a grid)", () => {
  it("renders the agenda as a list, not an ARIA grid or an unavailable placeholder", () => {
    const { container, queryByRole, getAllByRole } = render(
      <AetherCalendar
        view="list"
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    expect(container.querySelector('[data-view="list"]')).toBeTruthy();
    expect(queryByRole("grid")).toBeNull();
    expect(queryByRole("status")).toBeNull(); // no longer the honest "unavailable" placeholder
    expect(getAllByRole("list").length).toBeGreaterThanOrEqual(1);
  });

  it("does not make agenda events draggable (list view has no drag — F2-D owns interactions)", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    expect(container.querySelector('[draggable="true"]')).toBeNull();
    expect(container.querySelector('[role="button"]')).toBeNull();
  });
});

describe("AetherCalendar list/agenda view — grouping & content", () => {
  it("groups events under one accessible day group per day, with the title and time shown", () => {
    const { container, getAllByRole } = render(
      <AetherCalendar
        view="list"
        locale="en"
        events={[evt({ id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    const groups = getAllByRole("group");
    expect(groups).toHaveLength(1);
    const day = dayGroups(container)[0]!;
    expect(day.getAttribute("data-date")).toBe("2026-07-15");
    const event = within(day).getByText("Consult");
    expect(event).toBeTruthy();
    expect(day.textContent).toContain("10:00"); // locale-formatted time is present
  });

  it("orders day groups chronologically and events within a day by start time", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        locale="en"
        events={[
          evt({ id: "d20", title: "Later day", start: "2026-07-20T09:00:00", end: "2026-07-20T10:00:00" }),
          evt({ id: "b", title: "Afternoon", start: "2026-07-15T14:00:00", end: "2026-07-15T15:00:00" }),
          evt({ id: "a", title: "Morning", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
        ]}
      />,
    );
    const groups = dayGroups(container);
    expect(groups.map((g) => g.getAttribute("data-date"))).toEqual(["2026-07-15", "2026-07-20"]);
    const firstDayTitles = Array.from(
      groups[0]!.querySelectorAll<HTMLElement>(".aethercal-agenda-event-title"),
    ).map((n) => n.textContent);
    expect(firstDayTitles).toEqual(["Morning", "Afternoon"]);
  });

  it("shows the all-day label for all-day events (default, and overridable by prop)", () => {
    const allDay = evt({ id: "ad", title: "Holiday", allDay: true, start: "2026-07-15T00:00:00", end: "2026-07-16T00:00:00" });
    const first = render(<AetherCalendar view="list" locale="en" events={[allDay]} />);
    expect(first.getByText("All day")).toBeTruthy();
    cleanup();
    const second = render(
      <AetherCalendar view="list" locale="en" events={[allDay]} allDayLabel="Todo el día" />,
    );
    expect(second.getByText("Todo el día")).toBeTruthy();
    expect(second.queryByText("All day")).toBeNull();
  });

  it("labels a timed multi-day event honestly per day (start time / continues / ends time)", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        locale="en"
        events={[evt({ id: "trip", title: "Trip", start: "2026-07-15T22:00:00", end: "2026-07-17T02:00:00" })]}
      />,
    );
    const groups = dayGroups(container);
    expect(groups.map((g) => g.getAttribute("data-date"))).toEqual([
      "2026-07-15",
      "2026-07-16",
      "2026-07-17",
    ]);
    // Start day keeps the real start time; the middle day reads "Continues" (not "All day", which
    // would misrepresent a timed event as all-day); the last day shows the real end time.
    expect(groups[0]!.textContent).toContain("10:00"); // 22:00 -> "10:00 PM"
    expect(within(groups[1]!).getByText("Continues")).toBeTruthy();
    expect(within(groups[1]!).queryByText("All day")).toBeNull();
    const lastDay = (groups[2]!.textContent ?? "").toLowerCase();
    expect(lastDay).toContain("2:00"); // 02:00 -> "2:00 AM"
    expect(lastDay).toContain("ends");
  });

  it("keeps 'All day' only for real all-day events, and overrides continuation labels by prop", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        locale="en"
        events={[
          evt({ id: "holiday", title: "Holiday", allDay: true, start: "2026-07-14T00:00:00", end: "2026-07-16T00:00:00" }),
          evt({ id: "trip", title: "Trip", start: "2026-07-15T22:00:00", end: "2026-07-17T02:00:00" }),
        ]}
        continuesLabel="Sigue"
        formatEndsLabel={(time) => `hasta ${time}`}
      />,
    );
    const groups = dayGroups(container);
    const dayOf = (date: string) => groups.find((g) => g.getAttribute("data-date") === date)!;
    // A real all-day event stays "All day" on every day it spans (14th and 15th).
    expect(within(dayOf("2026-07-14")).getByText("All day")).toBeTruthy();
    // The timed trip's continuation labels are overridden and never read "All day".
    expect(within(dayOf("2026-07-16")).getByText("Sigue")).toBeTruthy();
    expect((dayOf("2026-07-17").textContent ?? "").toLowerCase()).toContain("hasta");
  });
});

describe("AetherCalendar list/agenda view — locale & empty state", () => {
  it("derives the day heading from the locale (no hardcoded English)", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        locale="es"
        events={[evt({ id: "e1", title: "Cita", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
      />,
    );
    const title = container.querySelector(".aethercal-agenda-day-title")!.textContent ?? "";
    expect(title.toLowerCase()).toContain("julio"); // Spanish month name
    expect(title.toLowerCase()).not.toContain("july");
  });

  it("renders an empty-state message when there are no events (default, and overridable)", () => {
    const first = render(<AetherCalendar view="list" events={[]} />);
    expect(first.getByText("No events")).toBeTruthy();
    expect(first.queryByRole("grid")).toBeNull();
    cleanup();
    const second = render(<AetherCalendar view="list" events={[]} agendaEmptyLabel="Sin eventos" />);
    expect(second.getByText("Sin eventos")).toBeTruthy();
  });
});
