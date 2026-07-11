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
import { type CalendarMessages, resolveMessages } from "./i18n";
import { MonthView } from "./MonthView";
import { ensureCalendarStyles } from "./styles";
import { type ThemeInput, resolveThemeVars } from "./theme";
import { TimeGridView } from "./TimeGridView";

export interface AetherCalendarProps {
  /** Which surface to render. All four (month/week/day/list) are implemented. */
  view?: CalendarView;
  events?: readonly CalendarEvent[];
  /** Any day within the range to show (Date or "YYYY-MM-DD[...]"). Defaults to today. */
  anchor?: Date | string;
  /** BCP-47 locale that drives labels/formatting (weekday/date/time via Intl + the message pack). */
  locale?: string;
  /**
   * Theme: a preset name ("light" | "dark" | "midnight" | "high_contrast") or a custom `--ac-*`
   * token override object. Applied as inline CSS variables; the default look is the neutral light
   * preset. The Reflex wrapper passes this straight through.
   */
  theme?: ThemeInput;
  /** Per-string i18n overrides layered on top of the locale message pack (advanced). */
  messages?: Partial<CalendarMessages>;
  /** 0 = Sunday … 6 = Saturday. Defaults to Monday (1). */
  firstDayOfWeek?: FirstDayOfWeek;
  /** Events shown before collapsing the rest into "+N more" (month view). Defaults to 3. */
  maxEventsPerDay?: number;
  /** Explicit weekday labels (7, ordered from firstDayOfWeek); overrides locale-derived ones. */
  weekdayLabels?: readonly string[];
  /** Overflow label formatter (month view). Overrides the locale message ("+N more"). */
  formatMore?: (hiddenCount: number) => string;
  /** Fallback message for an unrecognized view value. Overrides the locale message. */
  unavailableLabel?: string;
  /** First visible hour of the week/day time grid (0..23). Defaults to 0 (midnight). */
  dayStartHour?: number;
  /** Last visible hour of the week/day time grid, exclusive (1..24). Defaults to 24. */
  dayEndHour?: number;
  /** All-day label for the week/day rail AND the list/agenda all-day row. Overrides the locale message. */
  allDayLabel?: string;
  /** Current time for the week/day "now" line + today highlight (injectable; defaults to now). */
  now?: Date | string;
  /** List/agenda: label for a day a timed event passes fully through. Overrides the locale message. */
  continuesLabel?: string;
  /** List/agenda: last-day label of a timed multi-day event, from its end time. Overrides the locale message. */
  formatEndsLabel?: (endTimeLabel: string) => string;
  /** List/agenda: message shown when there are no events. Overrides the locale message. */
  agendaEmptyLabel?: string;
  onEventDrop?: (payload: EventDropPayload) => void;
  /**
   * Drag an event's top/bottom edge handle on the week/day time grid to change its duration (F2-D).
   * Only rendered for an editable event; the month/list views have no resize affordance.
   */
  onEventResize?: (payload: EventResizePayload) => void;
  /** Drag across empty week/day grid space, or activate an empty month cell by keyboard, to create. */
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

/**
 * The AetherCal calendar entry component (the React layer's public surface, and the tag the Reflex
 * wrapper mounts as `AetherCalendar`). Routes to the month view (F2-A), the week/day time-grid
 * views (F2-B; day = a single-column week), or the list/agenda view (F2-C). It resolves the theme
 * (F2-E) into inline CSS variables and the locale (+ any per-string overrides) into a message pack
 * once, then hands both to the active view — so theming and i18n flow from one place.
 */
export function AetherCalendar(props: AetherCalendarProps): React.JSX.Element {
  const {
    view = "month",
    events,
    anchor,
    locale = "en",
    theme,
    messages: messageOverrides,
    firstDayOfWeek = 1,
    maxEventsPerDay = 3,
    weekdayLabels,
    formatMore,
    unavailableLabel,
    dayStartHour,
    dayEndHour,
    allDayLabel,
    now,
    continuesLabel,
    formatEndsLabel,
    agendaEmptyLabel,
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

  const themeVars = React.useMemo(() => resolveThemeVars(theme), [theme]);

  // Resolve the locale message pack once, layering the convenience label props (allDayLabel, etc.)
  // and the advanced `messages` overrides on top — so a caller can switch locale AND still override
  // a single string. Only explicitly-passed props override; unset ones follow the locale.
  const messages = React.useMemo<CalendarMessages>(() => {
    const overrides: Partial<CalendarMessages> = {
      ...(allDayLabel !== undefined ? { allDay: allDayLabel } : {}),
      ...(continuesLabel !== undefined ? { continues: continuesLabel } : {}),
      ...(formatEndsLabel !== undefined ? { endsAt: formatEndsLabel } : {}),
      ...(agendaEmptyLabel !== undefined ? { noEvents: agendaEmptyLabel } : {}),
      ...(unavailableLabel !== undefined ? { unavailable: unavailableLabel } : {}),
      ...(formatMore !== undefined ? { more: formatMore } : {}),
      ...messageOverrides,
    };
    return resolveMessages(locale, overrides);
  }, [
    locale,
    allDayLabel,
    continuesLabel,
    formatEndsLabel,
    agendaEmptyLabel,
    unavailableLabel,
    formatMore,
    messageOverrides,
  ]);

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
      <AgendaView events={events ?? []} locale={locale} messages={messages} themeVars={themeVars} />
    );
  }

  if (view === "month") {
    return (
      <MonthView
        events={events ?? []}
        anchor={anchorDate}
        locale={locale}
        messages={messages}
        themeVars={themeVars}
        firstDayOfWeek={safeFirstDayOfWeek}
        maxEventsPerDay={safeMaxEventsPerDay}
        {...(safeWeekdayLabels ? { weekdayLabels: safeWeekdayLabels } : {})}
        {...(onEventDrop ? { onEventDrop } : {})}
        {...(onRangeSelect ? { onRangeSelect } : {})}
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
        messages={messages}
        themeVars={themeVars}
        config={timeGridConfig}
        now={nowDate}
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
    <div
      className="aethercal-calendar aethercal-unavailable"
      role="status"
      data-view={view}
      style={themeVars}
    >
      {messages.unavailable}
    </div>
  );
}

export default AetherCalendar;
