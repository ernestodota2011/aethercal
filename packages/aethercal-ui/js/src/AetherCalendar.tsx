import * as React from "react";
import { CalendarGrid } from "./CalendarGrid";
import { getMonthGridDays, getWeekGridDays } from "./dateMath";
import type { AetherCalendarProps } from "./types";

/**
 * The AetherCal calendar core: a minimal month/week grid with drag-to-reschedule.
 *
 * This is the F0-10 spike component — it proves the TSX-core -> bundle -> Reflex-wrapper ->
 * wheel pipeline end to end. It is intentionally not feature-complete (see
 * docs/spikes/f0-10-reflex-tsx.md): no day/list view, no multi-event stacking beyond a simple
 * vertical list, no keyboard/touch drag fallback.
 */
export function AetherCalendar({
  view = "month",
  events = [],
  onEventDrop,
}: AetherCalendarProps): React.JSX.Element {
  const today = React.useMemo(() => new Date(), []);
  const days = React.useMemo(
    () => (view === "week" ? getWeekGridDays(today) : getMonthGridDays(today)),
    [view, today],
  );

  return (
    <div className="aethercal-calendar" data-view={view}>
      <CalendarGrid
        days={days}
        events={events}
        onEventDrop={onEventDrop}
        minCellHeight={view === "week" ? 160 : 80}
      />
    </div>
  );
}

export default AetherCalendar;
