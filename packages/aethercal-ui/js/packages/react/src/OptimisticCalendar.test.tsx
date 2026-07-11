import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OptimisticCalendar } from "./OptimisticCalendar";
import type { MutationResult } from "./useOptimisticEvents";

afterEach(cleanup);

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

describe("OptimisticCalendar — full optimistic loop", () => {
  it("dropping an event calls mutate('drop', ...) and renders it pending while in flight", () => {
    const mutate = vi.fn(() => new Promise<MutationResult>(() => {})); // never settles -> stays pending
    const { container } = render(
      <OptimisticCalendar
        view="week"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", revision: 1 })]}
        mutate={mutate}
      />,
    );
    const block = container.querySelector('[data-event-id="e1"]') as HTMLElement;
    const target = container.querySelector('.aethercal-tg-col[data-date="2026-07-17"]') as HTMLElement;
    const dt = fakeDataTransfer();
    fireEvent.dragStart(block, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt });

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate).toHaveBeenCalledWith(expect.objectContaining({ kind: "drop" }));
    expect((container.querySelector('[data-event-id="e1"]') as HTMLElement).className).toContain(
      "is-pending",
    );
  });

  it("forwards non-mutation events (onEventClick) straight through", () => {
    const onEventClick = vi.fn();
    const { container } = render(
      <OptimisticCalendar
        view="day"
        anchor={ANCHOR}
        events={[evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" })]}
        mutate={vi.fn(() => new Promise<MutationResult>(() => {}))}
        onEventClick={onEventClick}
      />,
    );
    fireEvent.click(container.querySelector('[data-event-id="e1"]') as HTMLElement);
    expect(onEventClick).toHaveBeenCalledWith({ id: "e1" });
  });
});
