import {
  type CalendarEvent,
  type ContextMenuPayload,
  type EventClickPayload,
  type EventDropPayload,
  type FirstDayOfWeek,
  type RangeSelectPayload,
  addCalendarDays,
  computeDroppedRange,
  dragReducer,
  formatLocalDateTime,
  getMonthGridDays,
  initialDragState,
  isDragging,
  nextGridIndex,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { KeyboardHint, LiveRegion } from "./a11y";
import { EventChip } from "./EventChip";
import type { CalendarMessages } from "./i18n";
import { formatDayCellLabel, formatEventTime, formatMonthTitle, localeWeekdayLabels } from "./labels";
import type { ThemeTokens } from "./theme";

const EMPTY_IDS: ReadonlySet<string> = new Set();
const COLS = 7;
const ROWS = 6;

export interface MonthViewProps {
  events: readonly CalendarEvent[];
  /** The month to display (any day within it). */
  anchor: Date;
  locale: string;
  firstDayOfWeek: FirstDayOfWeek;
  /** All user-facing strings + a11y announcements (locale-resolved by AetherCalendar). */
  messages: CalendarMessages;
  /** Explicit weekday labels (7, ordered from firstDayOfWeek). Overrides the locale-derived ones. */
  weekdayLabels?: readonly string[];
  maxEventsPerDay: number;
  /** Inline `--ac-*` theme overrides applied to the grid root (a preset or a custom object). */
  themeVars?: ThemeTokens;
  onEventDrop?: (payload: EventDropPayload) => void;
  /** Create an all-day event by activating an empty cell with the keyboard (F2-E). */
  onRangeSelect?: (payload: RangeSelectPayload) => void;
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
  for (let i = 0; i < days.length; i += COLS) {
    weeks.push(days.slice(i, i + COLS));
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

/** The exclusive-next-midnight all-day range for a single day cell (keyboard create). */
function allDayRangeFor(dateOnly: string): RangeSelectPayload {
  return {
    start: `${dateOnly}T00:00:00`,
    end: `${addCalendarDays(dateOnly, 1)}T00:00:00`,
    allDay: true,
  };
}

/**
 * Production month view: a 6×7 ARIA grid, Monday-first by default, with day numbers, a today
 * marker, stacked events collapsing to "+N more", pointer drag-to-reschedule, AND full keyboard
 * access (F2-E): the grid is one tabstop driving an `aria-activedescendant`, arrow keys move the
 * active cell, Enter creates on an empty cell or enters an event, and a grabbed event moves across
 * days by keyboard with live-region announcements, committing on Enter / reverting on Escape. All
 * geometry comes from `@aethercal/calendar-core` (RF-23); this file only renders and wires events.
 */
export function MonthView(props: MonthViewProps): React.JSX.Element {
  const {
    events,
    anchor,
    locale,
    firstDayOfWeek,
    messages,
    weekdayLabels,
    maxEventsPerDay,
    themeVars,
    onEventDrop,
    onRangeSelect,
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
  const anchorKey = React.useMemo(() => toDateOnly(formatLocalDateTime(anchor)), [anchor]);

  const [dragState, dispatch] = React.useReducer(dragReducer, initialDragState);
  const [expanded, setExpanded] = React.useState<ReadonlySet<string>>(() => new Set());

  // Keyboard state (aria-activedescendant model): the active day cell, an optional active event
  // inside it, and an optional grabbed event whose target day the arrows move. One tabstop, no DOM
  // focus juggling — the container keeps focus and points aria-activedescendant at the active id.
  const baseId = React.useId();
  const [activeDate, setActiveDate] = React.useState<string>(anchorKey);
  const [activeEventId, setActiveEventId] = React.useState<string | null>(null);
  // `moved` tracks whether an arrow was pressed, so confirming a grab without moving is a no-op
  // (never emits a same-place mutation).
  const [grab, setGrab] = React.useState<{
    eventId: string;
    targetDate: string;
    moved: boolean;
  } | null>(null);
  const [announcement, setAnnouncement] = React.useState("");

  // Keep the active cell valid if the month (anchor) changes under us.
  React.useEffect(() => {
    if (!days.includes(activeDate)) {
      setActiveDate(anchorKey);
      setActiveEventId(null);
      setGrab(null);
    }
  }, [days, activeDate, anchorKey]);

  const eventInteractive = React.useCallback(
    (event: CalendarEvent): boolean =>
      Boolean(onEventClick) || (event.editable !== false && Boolean(onEventDrop)),
    [onEventClick, onEventDrop],
  );

  // Keep the keyboard focus valid when `events` (or the handlers) change under us: if the
  // active/grabbed event is no longer a navigable (actionable) event on the active day, cancel any
  // grab and fall back to the cell, so aria-activedescendant never points at a removed/moved/locked
  // event (Crisol round-5/7).
  React.useEffect(() => {
    const navigable = new Set(
      (eventsByDay.get(activeDate) ?? []).filter((ev) => eventInteractive(ev)).map((ev) => ev.id),
    );
    if (grab && !navigable.has(grab.eventId)) {
      setGrab(null);
      setActiveEventId(null);
    } else if (!grab && activeEventId !== null && !navigable.has(activeEventId)) {
      setActiveEventId(null);
    }
  }, [eventsByDay, activeDate, activeEventId, grab, eventInteractive]);

  const cellDomId = (dateOnly: string): string => `${baseId}-c-${dateOnly}`;
  // The cell date is part of the event's DOM id so ids stay unique even if the same event id were
  // ever rendered in more than one cell (defensive/consistent with the time grid — Crisol round-1).
  const evtDomId = (dateOnly: string, eventId: string): string => `${baseId}-e-${dateOnly}-${eventId}`;
  const hintId = `${baseId}-hint`;

  const activeDescendantId = grab
    ? evtDomId(activeDate, grab.eventId)
    : activeEventId
      ? evtDomId(activeDate, activeEventId)
      : cellDomId(activeDate);

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

  const moveGrabTarget = React.useCallback(
    (delta: number) => {
      // Compute from the closure and set state plainly — no side effect (announce) inside a state
      // updater (Crisol round-6).
      if (!grab) return;
      const candidate = addCalendarDays(grab.targetDate, delta);
      // Clamp to the visible 6×7 grid (lexicographic compare is valid for YYYY-MM-DD).
      const first = days[0]!;
      const last = days[days.length - 1]!;
      if (candidate < first || candidate > last) return;
      setAnnouncement(messages.movedTo(formatDayCellLabel(candidate, locale)));
      setGrab({ ...grab, targetDate: candidate, moved: true });
    },
    [grab, days, locale, messages],
  );

  const commitGrab = React.useCallback(() => {
    if (!grab) return;
    // A grab confirmed without any arrow press is a no-op — never emit a same-place move.
    if (!grab.moved) {
      setActiveEventId(grab.eventId);
      setGrab(null);
      return;
    }
    const event = events.find((candidate) => candidate.id === grab.eventId);
    if (event && event.editable !== false && onEventDrop) {
      onEventDrop(computeDroppedRange(event, grab.targetDate));
      setAnnouncement(messages.dropped(formatDayCellLabel(grab.targetDate, locale)));
    }
    // Land on the TARGET CELL, not the moved event: the parent may not have re-rendered the event at
    // its new day yet, so pointing aria-activedescendant at the event's id could dangle. The cell
    // always exists (Crisol round-2). The user re-enters the event with Enter once it lands.
    setActiveDate(grab.targetDate);
    setActiveEventId(null);
    setGrab(null);
  }, [grab, events, onEventDrop, messages, locale]);

  const ARROW_DELTA: Record<string, number> = {
    ArrowLeft: -1,
    ArrowRight: 1,
    ArrowUp: -COLS,
    ArrowDown: COLS,
  };

  const handleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const { key } = e;
      const activate = key === "Enter" || key === " " || key === "Spacebar";

      // --- grabbed: arrows move the target day, Enter drops, Escape reverts -----------------------
      if (grab) {
        if (key in ARROW_DELTA) {
          e.preventDefault();
          moveGrabTarget(ARROW_DELTA[key]!);
          return;
        }
        if (activate) {
          e.preventDefault();
          commitGrab();
          return;
        }
        if (key === "Escape") {
          e.preventDefault();
          setGrab(null);
          setAnnouncement(messages.cancelled);
          return;
        }
        return;
      }

      const cellEvents = eventsByDay.get(activeDate) ?? [];
      // Only ACTIONABLE events are keyboard-navigable — never land the active descendant on a
      // locked, unactionable event (Crisol round-7).
      const navigable = cellEvents.filter((ev) => eventInteractive(ev));

      // --- event mode: cycle events (Up/Down), grab/click (Enter), leave (Escape / Left/Right) ----
      if (activeEventId) {
        const idx = navigable.findIndex((ev) => ev.id === activeEventId);
        if (key === "ArrowDown") {
          e.preventDefault();
          if (idx >= 0 && idx < navigable.length - 1) setActiveEventId(navigable[idx + 1]!.id);
          return;
        }
        if (key === "ArrowUp") {
          e.preventDefault();
          if (idx > 0) setActiveEventId(navigable[idx - 1]!.id);
          else setActiveEventId(null); // above the first event -> back to the cell
          return;
        }
        if (activate) {
          e.preventDefault();
          const event = navigable.find((ev) => ev.id === activeEventId);
          if (!event) return;
          if (event.editable !== false && onEventDrop) {
            setGrab({ eventId: event.id, targetDate: activeDate, moved: false });
            setAnnouncement(messages.grabbedMoveHint(event.title));
          } else if (onEventClick) {
            onEventClick({ id: event.id });
          }
          return;
        }
        if (key === "Escape") {
          e.preventDefault();
          setActiveEventId(null);
          return;
        }
        if (key === "ArrowLeft" || key === "ArrowRight" || key === "Home" || key === "End") {
          e.preventDefault();
          setActiveEventId(null);
          const nextIdx = nextGridIndex(days.indexOf(activeDate), key, ROWS, COLS);
          setActiveDate(days[nextIdx]!);
          return;
        }
        return;
      }

      // --- grid mode: navigate cells; Enter enters an event or creates on an empty cell -----------
      if (key in ARROW_DELTA || key === "Home" || key === "End") {
        e.preventDefault();
        const nextIdx = nextGridIndex(days.indexOf(activeDate), key, ROWS, COLS);
        setActiveDate(days[nextIdx]!);
        return;
      }
      if (activate) {
        if (navigable.length > 0) {
          e.preventDefault();
          // Expand the day so EVERY event has a rendered node — otherwise keyboard nav could target
          // an event hidden behind "+N more" and aria-activedescendant would point at a missing id
          // (Crisol round-1). Expanding keeps all events reachable and the active descendant valid.
          expandDay(activeDate);
          setActiveEventId(navigable[0]!.id);
        } else if (cellEvents.length === 0 && onRangeSelect) {
          // Create ONLY on an empty cell — never on top of a day that already has events (even
          // non-interactive ones), which would silently double-book it (Crisol round-2).
          e.preventDefault();
          onRangeSelect(allDayRangeFor(activeDate));
          setAnnouncement(messages.createHere(formatDayCellLabel(activeDate, locale)));
        }
      }
    },
    [
      grab,
      activeEventId,
      activeDate,
      days,
      eventsByDay,
      eventInteractive,
      onEventDrop,
      onEventClick,
      onRangeSelect,
      moveGrabTarget,
      commitGrab,
      expandDay,
      messages,
      locale,
      ARROW_DELTA,
    ],
  );

  return (
    <>
      <div
        className={cx("aethercal-calendar", isDragging(dragState) && "is-dragging")}
        role="grid"
        aria-label={formatMonthTitle(anchor, locale)}
        aria-describedby={hintId}
        aria-activedescendant={activeDescendantId}
        tabIndex={0}
        data-view="month"
        style={themeVars}
        onKeyDown={handleKeyDown}
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
              const isCellActive = !activeEventId && !grab && dateOnly === activeDate;
              const isDropTarget = grab?.targetDate === dateOnly;

              return (
                <div
                  key={dateOnly}
                  id={cellDomId(dateOnly)}
                  role="gridcell"
                  className={cx(
                    "aethercal-day",
                    isOutside && "is-outside",
                    isToday && "is-today",
                    isCellActive && "is-active",
                    isDropTarget && "is-drop-target",
                  )}
                  data-date={dateOnly}
                  aria-label={formatDayCellLabel(dateOnly, locale)}
                  onDragOver={dropEnabled ? (e) => e.preventDefault() : undefined}
                  onDrop={dropEnabled ? handleDrop(dateOnly) : undefined}
                  onContextMenu={
                    onContextMenu
                      ? (e) => {
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
                    {visible.map((event) => {
                      const isEvtActive =
                        grab?.eventId === event.id || (!grab && activeEventId === event.id);
                      return (
                        <EventChip
                          key={event.id}
                          id={evtDomId(dateOnly, event.id)}
                          event={event}
                          interactive={eventInteractive(event)}
                          isActive={isEvtActive}
                          isGrabbed={grab?.eventId === event.id}
                          timeLabel={event.allDay ? null : formatEventTime(event.start, locale)}
                          onDragStart={(id) => dispatch({ type: "DRAG_START", eventId: id })}
                          onDragEnd={() => dispatch({ type: "DRAG_CANCEL" })}
                          isPending={pendingIds.has(event.id)}
                          isRolledBack={rolledBackIds.has(event.id)}
                          {...(onEventClick ? { onClick: () => onEventClick({ id: event.id }) } : {})}
                          {...(onContextMenu
                            ? { onContextMenu: () => onContextMenu({ id: event.id }) }
                            : {})}
                        />
                      );
                    })}
                    {hidden > 0 && !isExpanded ? (
                      <button
                        type="button"
                        className="aethercal-more"
                        onClick={() => expandDay(dateOnly)}
                      >
                        {messages.more(hidden)}
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <KeyboardHint id={hintId} text={messages.keyboardHint} />
      <LiveRegion message={announcement} />
    </>
  );
}
