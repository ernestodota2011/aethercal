import {
  type CalendarEvent,
  type CalendarView,
  type ContextMenuPayload,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type FirstDayOfWeek,
  type RangeSelectPayload,
  formatLocalDateTime,
  getWeekGridDays,
  parseLocalDateTime,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { AgendaView } from "./AgendaView";
import { MonthView } from "./MonthView";
import { ensureCalendarStyles } from "./styles";
import { TimeGridView } from "./TimeGridView";

export interface AetherCalendarProps {
  /** Which surface to render. All four (month/week/day/list) are implemented. */
  view?: CalendarView;
  events?: readonly CalendarEvent[];
  /** Any day within the range to show (Date or "YYYY-MM-DD[...]"). Defaults to today. */
  anchor?: Date | string;
  /** BCP-47 locale that drives labels/formatting. Defaults to "en" (labels are never hardcoded). */
  locale?: string;
  /** 0 = Sunday … 6 = Saturday. Defaults to Monday (1). */
  firstDayOfWeek?: FirstDayOfWeek;
  /** Events shown before collapsing the rest into "+N more" (month view). Defaults to 3. */
  maxEventsPerDay?: number;
  /** Explicit weekday labels (7, ordered from firstDayOfWeek); overrides locale-derived ones. */
  weekdayLabels?: readonly string[];
  /** Overflow label formatter (month view). Defaults to `(n) => "+" + n + " more"`. */
  formatMore?: (hiddenCount: number) => string;
  /** Fallback message for an unrecognized view value (all four views are implemented). */
  unavailableLabel?: string;
  /** First visible hour of the week/day time grid (0..23). Defaults to 0 (midnight). */
  dayStartHour?: number;
  /** Last visible hour of the week/day time grid, exclusive (1..24). Defaults to 24. */
  dayEndHour?: number;
  /** All-day label for the week/day rail AND the list/agenda all-day row. Defaults to "All day". */
  allDayLabel?: string;
  /** Current time for the week/day "now" line + today highlight (injectable; defaults to now). */
  now?: Date | string;
  /** List/agenda: label for a day a timed event passes fully through. Defaults to "Continues". */
  continuesLabel?: string;
  /** List/agenda: last-day label of a timed multi-day event, from its end time. Defaults to `ends {t}`. */
  formatEndsLabel?: (endTimeLabel: string) => string;
  /** List/agenda: message shown when there are no events. Defaults to "No events". */
  agendaEmptyLabel?: string;
  onEventDrop?: (payload: EventDropPayload) => void;
  /**
   * Drag an event's top/bottom edge handle on the week/day time grid to change its duration (F2-D).
   * Only rendered for an editable event; the month/list views have no resize affordance.
   */
  onEventResize?: (payload: EventResizePayload) => void;
  /** Drag across empty week/day grid space to create a new event (F2-D). */
  onRangeSelect?: (payload: RangeSelectPayload) => void;
  /** Click an event on any view (F2-D). */
  onEventClick?: (payload: EventClickPayload) => void;
  /** Right-click / context-menu on an event or an empty slot (F2-D). */
  onContextMenu?: (payload: ContextMenuPayload) => void;
  /** Events with an in-flight optimistic mutation (rendered pending). Driven by the reconciliation layer. */
  pendingIds?: ReadonlySet<string>;
  /** Events whose mutation was just reverted (rendered with the rollback flash). */
  rolledBackIds?: ReadonlySet<string>;
}

function resolveAnchor(anchor: Date | string | undefined): Date {
  if (anchor instanceof Date) return anchor;
  if (typeof anchor === "string") return parseLocalDateTime(anchor);
  return new Date();
}

function resolveNow(now: Date | string | undefined): Date {
  if (now instanceof Date) return now;
  if (typeof now === "string") return parseLocalDateTime(now);
  return new Date();
}

const defaultFormatMore = (hiddenCount: number): string => `+${hiddenCount} more`;

const defaultFormatEndsLabel = (endTimeLabel: string): string => `ends ${endTimeLabel}`;

/**
 * The AetherCal calendar entry component (the React layer's public surface, and the tag the Reflex
 * wrapper mounts as `AetherCalendar`). Routes to the month view (F2-A), the week/day time-grid
 * views (F2-B; day = a single-column week), or the list/agenda view (F2-C).
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
    dayStartHour,
    dayEndHour,
    allDayLabel = "All day",
    now,
    continuesLabel = "Continues",
    formatEndsLabel = defaultFormatEndsLabel,
    agendaEmptyLabel = "No events",
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onEventClick,
    onContextMenu,
    pendingIds,
    rolledBackIds,
  } = props;

  React.useEffect(() => {
    ensureCalendarStyles();
  }, []);

  const anchorDate = React.useMemo(() => resolveAnchor(anchor), [anchor]);

  // The "now" line must advance over time when uncontrolled, instead of freezing at mount. An
  // injected `now` (tests / controlled use) is used verbatim with NO timer, so it stays
  // deterministic; only an uncontrolled week/day view ticks (once a minute, cleared on unmount).
  const [autoNow, setAutoNow] = React.useState(() => new Date());
  React.useEffect(() => {
    if (now !== undefined) return;
    if (view !== "week" && view !== "day") return;
    const id = setInterval(() => setAutoNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, [now, view]);
  const nowDate = React.useMemo(
    () => (now !== undefined ? resolveNow(now) : autoNow),
    [now, autoNow],
  );

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

  const timeGridConfig = React.useMemo(
    () => ({
      ...(dayStartHour !== undefined ? { dayStartHour } : {}),
      ...(dayEndHour !== undefined ? { dayEndHour } : {}),
    }),
    [dayStartHour, dayEndHour],
  );

  if (view === "list") {
    return (
      <AgendaView
        events={events ?? []}
        locale={locale}
        allDayLabel={allDayLabel}
        continuesLabel={continuesLabel}
        formatEndsLabel={formatEndsLabel}
        emptyLabel={agendaEmptyLabel}
      />
    );
  }

  if (view === "month") {
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
        {...(onEventClick ? { onEventClick } : {})}
        {...(onContextMenu ? { onContextMenu } : {})}
        {...(pendingIds ? { pendingIds } : {})}
        {...(rolledBackIds ? { rolledBackIds } : {})}
      />
    );
  }

  if (view === "week" || view === "day") {
    const days =
      view === "week"
        ? getWeekGridDays(anchorDate, safeFirstDayOfWeek)
        : [toDateOnly(formatLocalDateTime(anchorDate))];
    return (
      <TimeGridView
        view={view}
        days={days}
        events={events ?? []}
        locale={locale}
        config={timeGridConfig}
        now={nowDate}
        allDayLabel={allDayLabel}
        {...(onEventDrop ? { onEventDrop } : {})}
        {...(onEventResize ? { onEventResize } : {})}
        {...(onRangeSelect ? { onRangeSelect } : {})}
        {...(onEventClick ? { onEventClick } : {})}
        {...(onContextMenu ? { onContextMenu } : {})}
        {...(pendingIds ? { pendingIds } : {})}
        {...(rolledBackIds ? { rolledBackIds } : {})}
      />
    );
  }

  // Defensive fallback for an unrecognized `view` from a plain-JS consumer (all four views above
  // are implemented; a valid CalendarView never reaches here).
  return (
    <div className="aethercal-calendar aethercal-unavailable" role="status" data-view={view}>
      {unavailableLabel}
    </div>
  );
}

export default AetherCalendar;
