import {
  type CalendarEvent,
  type ContextMenuPayload,
  type EventClickPayload,
  type EventDropPayload,
  type FirstDayOfWeek,
  computeDroppedRange,
  dragReducer,
  formatLocalDateTime,
  getMonthGridDays,
  initialDragState,
  isDragging,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { EventChip } from "./EventChip";
import { formatDayCellLabel, formatEventTime, formatMonthTitle, localeWeekdayLabels } from "./labels";

const EMPTY_IDS: ReadonlySet<string> = new Set();

export interface MonthViewProps {
  events: readonly CalendarEvent[];
  /** The month to display (any day within it). */
  anchor: Date;
  locale: string;
  firstDayOfWeek: FirstDayOfWeek;
  /** Explicit weekday labels (7, ordered from firstDayOfWeek). Overrides the locale-derived ones. */
  weekdayLabels?: readonly string[];
  maxEventsPerDay: number;
  formatMore: (hiddenCount: number) => string;
  onEventDrop?: (payload: EventDropPayload) => void;
  /** Click an event (F2-D). */
  onEventClick?: (payload: EventClickPayload) => void;
  /** Right-click on an event ({id}) or an empty day cell ({start}) (F2-D). */
  onContextMenu?: (payload: ContextMenuPayload) => void;
  /** Events with an in-flight optimistic mutation (rendered pending). */
  pendingIds?: ReadonlySet<string>;
  /** Events whose mutation was just reverted (rendered with the rollback flash). */
  rolledBackIds?: ReadonlySet<string>;
}

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

function chunkWeeks(days: string[]): string[][] {
  const weeks: string[][] = [];
  for (let i = 0; i < days.length; i += 7) {
    weeks.push(days.slice(i, i + 7));
  }
  return weeks;
}

function groupEventsByDay(events: readonly CalendarEvent[]): Map<string, CalendarEvent[]> {
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const key = toDateOnly(event.start);
    const bucket = byDay.get(key);
    if (bucket) bucket.push(event);
    else byDay.set(key, [event]);
  }
  return byDay;
}

/**
 * Production month view: a 6×7 ARIA grid, Monday-first by default, with day numbers, a today
 * marker, stacked events collapsing to "+N more", and drag-to-reschedule. All geometry comes from
 * `@aethercal/calendar-core`; this file only renders and wires events (RF-23 boundary).
 */
export function MonthView(props: MonthViewProps): React.JSX.Element {
  const {
    events,
    anchor,
    locale,
    firstDayOfWeek,
    weekdayLabels,
    maxEventsPerDay,
    formatMore,
    onEventDrop,
    onEventClick,
    onContextMenu,
    pendingIds = EMPTY_IDS,
    rolledBackIds = EMPTY_IDS,
  } = props;

  const days = React.useMemo(
    () => getMonthGridDays(anchor, firstDayOfWeek),
    [anchor, firstDayOfWeek],
  );
  const weeks = React.useMemo(() => chunkWeeks(days), [days]);
  const weekdays = React.useMemo(
    () => weekdayLabels ?? localeWeekdayLabels(locale, firstDayOfWeek),
    [weekdayLabels, locale, firstDayOfWeek],
  );
  const eventsByDay = React.useMemo(() => groupEventsByDay(events), [events]);
  const anchorMonth = anchor.getMonth();
  const todayKey = toDateOnly(formatLocalDateTime(new Date()));

  const [dragState, dispatch] = React.useReducer(dragReducer, initialDragState);
  const [expanded, setExpanded] = React.useState<ReadonlySet<string>>(() => new Set());

  const expandDay = React.useCallback((dateOnly: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.add(dateOnly);
      return next;
    });
  }, []);

  const handleDrop = React.useCallback(
    (targetDateOnly: string) => (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      // Only in-calendar drags reschedule: the drag state machine is the source of truth for
      // *which* event moved. A foreign/synthetic drop (no prior DRAG_START) is ignored even if
      // it carries a valid id in dataTransfer. dataTransfer is used only to corroborate.
      if (!isDragging(dragState)) {
        dispatch({ type: "DROP" });
        return;
      }
      const draggedId = dragState.eventId;
      const transferred = e.dataTransfer.getData("text/plain");
      dispatch({ type: "DROP" });
      if (transferred && transferred !== draggedId) return;
      if (!onEventDrop) return;
      const dragged = events.find((candidate) => candidate.id === draggedId);
      if (!dragged || dragged.editable === false) return;
      onEventDrop(computeDroppedRange(dragged, targetDateOnly));
    },
    [dragState, events, onEventDrop],
  );

  const dropEnabled = Boolean(onEventDrop);

  return (
    <div
      className={cx("aethercal-calendar", isDragging(dragState) && "is-dragging")}
      role="grid"
      aria-label={formatMonthTitle(anchor, locale)}
      data-view="month"
    >
      <div className="aethercal-weekdays" role="row">
        {weekdays.map((label, i) => (
          <div key={i} role="columnheader" className="aethercal-weekday">
            {label}
          </div>
        ))}
      </div>

      {weeks.map((week, wi) => (
        <div key={wi} className="aethercal-week" role="row">
          {week.map((dateOnly) => {
            const dayEvents = eventsByDay.get(dateOnly) ?? [];
            const isExpanded = expanded.has(dateOnly);
            const visible = isExpanded ? dayEvents : dayEvents.slice(0, maxEventsPerDay);
            const hidden = dayEvents.length - visible.length;
            const isOutside = new Date(`${dateOnly}T00:00:00`).getMonth() !== anchorMonth;
            const isToday = dateOnly === todayKey;

            return (
              <div
                key={dateOnly}
                role="gridcell"
                className={cx("aethercal-day", isOutside && "is-outside", isToday && "is-today")}
                data-date={dateOnly}
                aria-label={formatDayCellLabel(dateOnly, locale)}
                onDragOver={dropEnabled ? (e) => e.preventDefault() : undefined}
                onDrop={dropEnabled ? handleDrop(dateOnly) : undefined}
                onContextMenu={
                  onContextMenu
                    ? (e) => {
                        // Only an empty part of the cell (not a chip / "+N more" button) creates here;
                        // testing the target's ancestry keeps decorative children from blocking it.
                        if ((e.target as Element).closest("[data-event-id], button")) return;
                        e.preventDefault();
                        onContextMenu({ start: `${dateOnly}T00:00:00` });
                      }
                    : undefined
                }
              >
                <div className="aethercal-day-head">
                  <span className="aethercal-day-number">{Number(dateOnly.slice(-2))}</span>
                </div>
                <div className="aethercal-day-events">
                  {visible.map((event) => (
                    <EventChip
                      key={event.id}
                      event={event}
                      timeLabel={event.allDay ? null : formatEventTime(event.start, locale)}
                      onDragStart={(id) => dispatch({ type: "DRAG_START", eventId: id })}
                      onDragEnd={() => dispatch({ type: "DRAG_CANCEL" })}
                      isPending={pendingIds.has(event.id)}
                      isRolledBack={rolledBackIds.has(event.id)}
                      {...(onEventClick ? { onClick: () => onEventClick({ id: event.id }) } : {})}
                      {...(onContextMenu ? { onContextMenu: () => onContextMenu({ id: event.id }) } : {})}
                    />
                  ))}
                  {hidden > 0 && !isExpanded ? (
                    <button
                      type="button"
                      className="aethercal-more"
                      onClick={() => expandDay(dateOnly)}
                    >
                      {formatMore(hidden)}
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
