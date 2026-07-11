import {
  type CalendarEvent,
  type CalendarView,
  type EventDropPayload,
  type FirstDayOfWeek,
  parseLocalDateTime,
} from "@aethercal/calendar-core";
import * as React from "react";
import { MonthView } from "./MonthView";
import { ensureCalendarStyles } from "./styles";

export interface AetherCalendarProps {
  /** Which surface to render. Only "month" is implemented in F2-A; week/day/list arrive in F2-B/C. */
  view?: CalendarView;
  events?: readonly CalendarEvent[];
  /** Any day within the month to show (Date or "YYYY-MM-DD[...]"). Defaults to today. */
  anchor?: Date | string;
  /** BCP-47 locale that drives labels/formatting. Defaults to "en" (labels are never hardcoded). */
  locale?: string;
  /** 0 = Sunday … 6 = Saturday. Defaults to Monday (1). */
  firstDayOfWeek?: FirstDayOfWeek;
  /** Events shown before collapsing the rest into "+N more". Defaults to 3. */
  maxEventsPerDay?: number;
  /** Explicit weekday labels (7, ordered from firstDayOfWeek); overrides locale-derived ones. */
  weekdayLabels?: readonly string[];
  /** Overflow label formatter. Defaults to `(n) => "+" + n + " more"`. */
  formatMore?: (hiddenCount: number) => string;
  /** Message for views not yet implemented (week/day/list in F2-A). */
  unavailableLabel?: string;
  onEventDrop?: (payload: EventDropPayload) => void;
}

function resolveAnchor(anchor: Date | string | undefined): Date {
  if (anchor instanceof Date) return anchor;
  if (typeof anchor === "string") return parseLocalDateTime(anchor);
  return new Date();
}

const defaultFormatMore = (hiddenCount: number): string => `+${hiddenCount} more`;

/**
 * The AetherCal calendar entry component (the React layer's public surface, and the tag the
 * Reflex wrapper mounts as `AetherCalendar`). F2-A renders the production month view; other views
 * render an honest "not available yet" status until F2-B/C build them.
 */
export function AetherCalendar(props: AetherCalendarProps): React.JSX.Element {
  const {
    view = "month",
    events,
    anchor,
    locale = "en",
    firstDayOfWeek = 1,
    maxEventsPerDay = 3,
    weekdayLabels,
    formatMore = defaultFormatMore,
    unavailableLabel = "This view is not available yet.",
    onEventDrop,
  } = props;

  React.useEffect(() => {
    ensureCalendarStyles();
  }, []);

  const anchorDate = React.useMemo(() => resolveAnchor(anchor), [anchor]);

  // Normalize the public numeric/structural props so a bad value from a plain-JS consumer (the
  // TS types are advisory once published to npm) degrades gracefully instead of breaking the grid.
  const safeFirstDayOfWeek: FirstDayOfWeek = (
    Number.isInteger(firstDayOfWeek) && firstDayOfWeek >= 0 && firstDayOfWeek <= 6
      ? firstDayOfWeek
      : 1
  ) as FirstDayOfWeek;
  const safeMaxEventsPerDay =
    Number.isInteger(maxEventsPerDay) && maxEventsPerDay >= 0 ? maxEventsPerDay : 3;
  const safeWeekdayLabels =
    weekdayLabels && weekdayLabels.length === 7 ? weekdayLabels : undefined;

  if (view !== "month") {
    return (
      <div className="aethercal-calendar aethercal-unavailable" role="status" data-view={view}>
        {unavailableLabel}
      </div>
    );
  }

  return (
    <MonthView
      events={events ?? []}
      anchor={anchorDate}
      locale={locale}
      firstDayOfWeek={safeFirstDayOfWeek}
      maxEventsPerDay={safeMaxEventsPerDay}
      formatMore={formatMore}
      {...(safeWeekdayLabels ? { weekdayLabels: safeWeekdayLabels } : {})}
      {...(onEventDrop ? { onEventDrop } : {})}
    />
  );
}

export default AetherCalendar;
