/**
 * The calendar's built-in navigation toolbar (F2-NAV).
 *
 * A calendar that can only ever show the period containing "today" is incomplete. This toolbar adds
 * the missing chrome: accessible previous / today / next controls that move the visible PERIOD, a
 * localized period title, and a view switcher. It is a CONTROLLED affordance — it never mutates the
 * anchor itself; it emits `onRangeChange` (prev/next/today) and `onViewChange` (view switch) with the
 * `{ view, from, to }` payload, and the host keeps `anchor`/`view` in its own state and feeds them
 * back in. The "which period" geometry is the headless core's (`getVisibleRange`/`stepAnchor`,
 * RF-23); this layer only renders and wires the buttons.
 *
 * Rendered only when the host opts in via `AetherCalendar`'s `navigation` prop, so an existing
 * consumer that builds its own chrome is unaffected (and the default calendar has no toolbar).
 */
import {
  type CalendarView,
  type FirstDayOfWeek,
  type ViewChangePayload,
  getVisibleRange,
  stepAnchor,
} from "@aethercal/calendar-core";
import type * as React from "react";
import type { CalendarMessages } from "./i18n";
import { formatPeriodTitle } from "./labels";

/** The five surfaces, in the order the switcher lists them. */
const VIEW_ORDER: readonly CalendarView[] = ["month", "week", "day", "list", "timeline"];

export interface CalendarNavProps {
  view: CalendarView;
  /** The current anchor day (the host's controlled state). */
  anchor: Date;
  /** "Today" for the today button (injectable; the same value the now-line uses). */
  now: Date;
  locale: string;
  firstDayOfWeek: FirstDayOfWeek;
  /**
   * Days the timeline window spans (RF-28). The toolbar has to know: otherwise prev/next would step
   * by the wrong period and the title would name a range the grid is not showing.
   */
  timelineDays?: number;
  messages: CalendarMessages;
  /** Whether to render the view switcher (emits `onViewChange`). Defaults to true. */
  showViews?: boolean;
  /** Emitted when prev/next/today change the visible range. */
  onRangeChange?: (payload: ViewChangePayload) => void;
  /** Emitted when the view switcher changes the view. */
  onViewChange?: (payload: ViewChangePayload) => void;
}

export function CalendarNav({
  view,
  anchor,
  now,
  locale,
  firstDayOfWeek,
  timelineDays,
  messages,
  showViews = true,
  onRangeChange,
  onViewChange,
}: CalendarNavProps): React.JSX.Element {
  const goToRange = (nextAnchor: Date): void => {
    onRangeChange?.(getVisibleRange(view, nextAnchor, firstDayOfWeek, timelineDays));
  };
  const title = formatPeriodTitle(view, anchor, locale, firstDayOfWeek, timelineDays);

  return (
    <div className="aethercal-nav" role="toolbar" aria-label={messages.navToolbar}>
      <div className="aethercal-nav-group">
        <button
          type="button"
          className="aethercal-nav-btn aethercal-nav-arrow"
          aria-label={messages.navPrevious}
          onClick={() => goToRange(stepAnchor(anchor, view, -1))}
        >
          <span aria-hidden="true">‹</span>
        </button>
        <button
          type="button"
          className="aethercal-nav-btn aethercal-nav-today"
          onClick={() => goToRange(now)}
        >
          {messages.navToday}
        </button>
        <button
          type="button"
          className="aethercal-nav-btn aethercal-nav-arrow"
          aria-label={messages.navNext}
          onClick={() => goToRange(stepAnchor(anchor, view, 1))}
        >
          <span aria-hidden="true">›</span>
        </button>
      </div>
      <span className="aethercal-nav-title" aria-live="polite">
        {title}
      </span>
      {showViews ? (
        <div className="aethercal-nav-views">
          {VIEW_ORDER.map((candidate) => (
            <button
              key={candidate}
              type="button"
              className="aethercal-nav-btn aethercal-nav-view"
              aria-pressed={candidate === view}
              onClick={() =>
                onViewChange?.(getVisibleRange(candidate, anchor, firstDayOfWeek, timelineDays))
              }
            >
              {messages.viewNames[candidate]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default CalendarNav;
