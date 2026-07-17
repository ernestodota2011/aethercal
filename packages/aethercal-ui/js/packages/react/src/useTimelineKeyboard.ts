/**
 * The KEYBOARD half of the resource timeline's interaction (RF-28).
 *
 * The model IS the axes: up/down moves between RESOURCES, left/right between DAYS. Enter toggles a
 * group, or acts on whatever sits under the cursor — descending into a cell's bars, or creating on a
 * free cell. A grabbed event then moves through TIME with left/right and ACROSS RESOURCES with
 * up/down, so a keyboard user performs the very cross-row drag the view exists for.
 *
 * Extracted from the view so the keyboard's rules live in one readable place instead of being
 * interleaved with pointer handling and JSX — the shape that hid a dead Enter key for a whole review
 * round.
 */
import {
  type CalendarEvent,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type RangeSelectPayload,
  type ResourceTimeline,
  type TimelineItem,
  type TimelineRow,
  DEFAULT_SNAP_MINUTES,
  addCalendarDays,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  timelinePointAt,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import type { CalendarMessages } from "./i18n";
import { formatEventTime } from "./labels";
import { formatDayColumnHeader } from "./timeGridLabels";
import { type KbGrab, minuteOfDayOf, minuteToTimeLabel } from "./timelineShared";

export interface UseTimelineKeyboardOptions {
  timeline: ResourceTimeline;
  days: readonly string[];
  events: readonly CalendarEvent[];
  /** Rows that can actually receive an event (the unassigned row is a source, not a target). */
  dropRows: readonly TimelineRow[];
  locale: string;
  messages: CalendarMessages;
  /** Whether an event is actionable at all — the cursor never lands on a locked, unwired bar. */
  eventInteractive: (event: CalendarEvent) => boolean;
  axisFractionOf: (dateOnly: string, minute: number) => number;
  toggleGroup: (groupId: string) => void;
  announce: (message: string) => void;
  /** Stable DOM ids so `aria-activedescendant` can point at a real node. */
  itemDomId: (index: number) => string;
  evtDomId: (eventId: string) => string;
  onEventDrop?: (payload: EventDropPayload) => void;
  onEventResize?: (payload: EventResizePayload) => void;
  onRangeSelect?: (payload: RangeSelectPayload) => void;
  onEventClick?: (payload: EventClickPayload) => void;
}

export interface TimelineKeyboard {
  activeItem: number;
  activeEventId: string | null;
  kbGrab: KbGrab | null;
  currentRow: TimelineRow | undefined;
  /** `undefined` when there is genuinely no cell to be active (an empty timeline). */
  activeDescendantId: string | undefined;
  handleKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => void;
}

export function useTimelineKeyboard(options: UseTimelineKeyboardOptions): TimelineKeyboard {
  const {
    timeline,
    days,
    events,
    dropRows,
    locale,
    messages,
    eventInteractive,
    axisFractionOf,
    toggleGroup,
    announce,
    itemDomId,
    evtDomId,
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onEventClick,
  } = options;

  const snap = DEFAULT_SNAP_MINUTES;

  const [activeItem, setActiveItem] = React.useState(0);
  const [activeDayIndex, setActiveDayIndex] = React.useState(0);
  const [activeEventId, setActiveEventId] = React.useState<string | null>(null);
  const [kbGrab, setKbGrab] = React.useState<KbGrab | null>(null);

  // Keep the cursors valid as items appear/disappear (collapsing a group removes rows under it).
  React.useEffect(() => {
    if (activeItem > timeline.items.length - 1) {
      setActiveItem(Math.max(0, timeline.items.length - 1));
      setActiveEventId(null);
      setKbGrab(null);
    }
  }, [timeline.items.length, activeItem]);

  React.useEffect(() => {
    if (activeDayIndex > days.length - 1) setActiveDayIndex(Math.max(0, days.length - 1));
  }, [days.length, activeDayIndex]);

  const currentItem: TimelineItem | undefined = timeline.items[activeItem];
  const currentRow = currentItem?.kind === "row" ? currentItem.row : undefined;

  // Only ACTIONABLE events are keyboard-navigable — the cursor never lands on a locked, unwired bar.
  const navigableEvents = React.useMemo(
    () => (currentRow?.blocks ?? []).map((b) => b.event).filter((e) => eventInteractive(e)),
    [currentRow, eventInteractive],
  );

  /**
   * The actionable events sitting UNDER the day cursor — on the active day of the active row.
   *
   * This is what gives the keyboard the same reach as the mouse. The mouse decides by WHERE you
   * click: on a bar you grab it, on empty track you select. The keyboard's position is the (row, day)
   * cell, so it must decide by what is in THAT cell — not by whether the row happens to hold an event
   * somewhere else. Asking "is the whole row empty?" meant one Monday booking made every other day of
   * that resource uncreatable by keyboard, while a mouse user could still create there.
   *
   * Occupancy is judged on the AXIS (the same fractions the bars are drawn from), so what the user
   * sees in the cell is what the cursor acts on. A zero-width bar counts when it sits inside the day.
   */
  const eventsUnderCursor = React.useMemo(() => {
    const day = timeline.dayHeaders[activeDayIndex];
    if (!day || !currentRow) return [];
    const dayStart = day.leftFraction;
    const dayEnd = day.leftFraction + day.widthFraction;
    const EPS = 1e-9; // day edges are derived fractions; never let 1 ulp decide the cell
    return currentRow.blocks
      .filter((block) => {
        const start = block.leftFraction;
        const end = block.leftFraction + block.widthFraction;
        return end > start
          ? start < dayEnd - EPS && end > dayStart + EPS
          : start >= dayStart - EPS && start < dayEnd - EPS;
      })
      .map((block) => block.event)
      .filter((event) => eventInteractive(event));
  }, [timeline.dayHeaders, activeDayIndex, currentRow, eventInteractive]);

  // If the active/grabbed event vanished (events changed, or its group was collapsed), fall back to
  // the row so `aria-activedescendant` never points at a node that is not rendered.
  React.useEffect(() => {
    const ids = new Set(navigableEvents.map((e) => e.id));
    if (kbGrab && !ids.has(kbGrab.eventId)) {
      setKbGrab(null);
      setActiveEventId(null);
    } else if (!kbGrab && activeEventId !== null && !ids.has(activeEventId)) {
      setActiveEventId(null);
    }
  }, [navigableEvents, activeEventId, kbGrab]);

  /**
   * The node the grid's focus points at — or `undefined` when there is nothing to point at. An EMPTY
   * timeline renders no items, so naming an `activeItem` id here would reference a node that does not
   * exist and strand a screen reader on a dangling anchor.
   */
  const activeDescendantId: string | undefined =
    timeline.items.length === 0
      ? undefined
      : kbGrab
        ? evtDomId(kbGrab.eventId)
        : activeEventId
          ? evtDomId(activeEventId)
          : itemDomId(activeItem);

  const resourceTitleOf = React.useCallback(
    (resourceId: string): string =>
      dropRows.find((r) => r.resource?.id === resourceId)?.resource?.title ?? resourceId,
    [dropRows],
  );

  const stepGrab = React.useCallback(
    (key: string) => {
      const prev = kbGrab;
      if (!prev) return;
      const event = events.find((e) => e.id === prev.eventId);
      if (!event) return;
      const isAllDay = event.allDay === true;

      let dateOnly = prev.dateOnly;
      let minute = prev.minute;
      let resourceId = prev.kind === "move" ? prev.resourceId : "";

      if (key === "ArrowLeft" || key === "ArrowRight") {
        // Time runs horizontally here, so left/right step the TIME — or, for an all-day bar (which
        // has no time-of-day), the DAY.
        if (isAllDay) {
          dateOnly = addCalendarDays(dateOnly, key === "ArrowLeft" ? -1 : 1);
        } else {
          const delta = key === "ArrowLeft" ? -snap : snap;
          const stepped = timelinePointAt(
            axisFractionOf(dateOnly, minute + delta),
            days,
            timeline.config,
            snap,
          );
          if (!stepped) return;
          dateOnly = stepped.dateOnly;
          minute = stepped.minuteOfDay ?? minute;
        }
      } else if (prev.kind === "move" && (key === "ArrowUp" || key === "ArrowDown")) {
        // Up/down move the event ACROSS RESOURCES — the keyboard equivalent of dragging the bar onto
        // another row, and the reason this view exists at all.
        const idx = dropRows.findIndex((r) => r.resource?.id === resourceId);
        const nextIdx = key === "ArrowUp" ? idx - 1 : idx + 1;
        if (idx === -1 || nextIdx < 0 || nextIdx >= dropRows.length) return;
        resourceId = dropRows[nextIdx]!.resource!.id;
      } else {
        return;
      }

      // A step blocked by a clamp changes nothing — do NOT mark it as a move, so a later confirm
      // stays a no-op rather than emitting a spurious mutation.
      if (
        dateOnly === prev.dateOnly &&
        minute === prev.minute &&
        (prev.kind !== "move" || resourceId === prev.resourceId)
      ) {
        return;
      }

      if (prev.kind === "move") {
        const where = isAllDay
          ? formatDayColumnHeader(dateOnly, locale)
          : `${formatDayColumnHeader(dateOnly, locale)} ${minuteToTimeLabel(dateOnly, minute, locale)}`;
        announce(messages.movedTo(`${resourceTitleOf(resourceId)} · ${where}`));
        setKbGrab({ ...prev, dateOnly, minute, resourceId, moved: true });
      } else {
        const payload = computeResize(event, "end", dateOnly, minute);
        announce(
          messages.resizedTo(
            `${formatEventTime(payload.start, locale)} – ${formatEventTime(payload.end, locale)}`,
          ),
        );
        setKbGrab({ ...prev, dateOnly, minute, moved: true });
      }
    },
    [
      kbGrab,
      events,
      snap,
      days,
      timeline.config,
      dropRows,
      axisFractionOf,
      resourceTitleOf,
      announce,
      messages,
      locale,
    ],
  );

  const commitGrab = React.useCallback(() => {
    // Read the grab from the closure and run every effect in the body: a state updater must stay
    // pure (React may re-invoke it under StrictMode/concurrent), or the mutation would fire twice.
    const prev = kbGrab;
    if (!prev) return;
    // Confirming a grab without ever pressing an arrow must NOT emit a mutation.
    if (!prev.moved) {
      setActiveEventId(prev.eventId);
      setKbGrab(null);
      return;
    }
    const event = events.find((e) => e.id === prev.eventId);
    if (event && event.editable !== false && prev.kind === "move" && onEventDrop) {
      const minute = event.allDay === true ? null : prev.minute;
      onEventDrop(computeMovedRange(event, prev.dateOnly, minute, prev.resourceId));
      announce(
        messages.dropped(
          `${resourceTitleOf(prev.resourceId)} · ${
            event.allDay === true
              ? formatDayColumnHeader(prev.dateOnly, locale)
              : minuteToTimeLabel(prev.dateOnly, prev.minute, locale)
          }`,
        ),
      );
      setActiveEventId(null);
    } else if (event && event.editable !== false && prev.kind === "resize" && onEventResize) {
      const payload = computeResize(event, "end", prev.dateOnly, prev.minute);
      onEventResize(payload);
      announce(
        messages.resized(
          `${formatEventTime(payload.start, locale)} – ${formatEventTime(payload.end, locale)}`,
        ),
      );
      setActiveEventId(prev.eventId);
    } else {
      setActiveEventId(prev.eventId);
    }
    setKbGrab(null);
  }, [kbGrab, events, onEventDrop, onEventResize, resourceTitleOf, announce, messages, locale]);

  const handleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const { key } = e;
      const activate = key === "Enter" || key === " " || key === "Spacebar";
      const arrow =
        key === "ArrowUp" || key === "ArrowDown" || key === "ArrowLeft" || key === "ArrowRight";
      const lastItem = timeline.items.length - 1;

      // --- grabbed: left/right step the TIME, up/down step the RESOURCE, Enter commits -----------
      if (kbGrab) {
        if (arrow) {
          e.preventDefault();
          stepGrab(key);
          return;
        }
        if (activate) {
          e.preventDefault();
          commitGrab();
          return;
        }
        if (key === "Escape") {
          e.preventDefault();
          setKbGrab(null);
          announce(messages.cancelled);
        }
        return;
      }

      // --- event mode: cycle the row's events, grab (Enter) / resize (r), leave (Escape) ---------
      if (activeEventId) {
        const idx = navigableEvents.findIndex((ev) => ev.id === activeEventId);
        if (key === "ArrowRight") {
          e.preventDefault();
          if (idx >= 0 && idx < navigableEvents.length - 1) {
            setActiveEventId(navigableEvents[idx + 1]!.id);
          }
          return;
        }
        if (key === "ArrowLeft") {
          e.preventDefault();
          if (idx > 0) setActiveEventId(navigableEvents[idx - 1]!.id);
          else setActiveEventId(null);
          return;
        }
        if (key === "ArrowUp" || key === "ArrowDown") {
          e.preventDefault();
          setActiveEventId(null);
          setActiveItem((i) => Math.min(Math.max(i + (key === "ArrowUp" ? -1 : 1), 0), lastItem));
          return;
        }
        if (activate) {
          e.preventDefault();
          const event = navigableEvents.find((ev) => ev.id === activeEventId);
          if (!event) return;
          if (event.editable !== false && onEventDrop && currentRow?.resource) {
            setKbGrab({
              kind: "move",
              eventId: event.id,
              dateOnly: toDateOnly(event.start),
              minute: minuteOfDayOf(event.start),
              resourceId: currentRow.resource.id,
              moved: false,
            });
            announce(messages.grabbedMoveHint(event.title));
          } else if (onEventClick) {
            onEventClick({ id: event.id });
          }
          return;
        }
        if ((key === "r" || key === "R") && onEventResize) {
          e.preventDefault();
          const event = navigableEvents.find((ev) => ev.id === activeEventId);
          // An all-day bar has no end-edge time to resize — resize is a timed affordance only.
          if (event && event.allDay !== true && event.editable !== false) {
            setKbGrab({
              kind: "resize",
              eventId: event.id,
              dateOnly: toDateOnly(event.end),
              minute: minuteOfDayOf(event.end),
              moved: false,
            });
            announce(messages.grabbedResizeHint(event.title));
          }
          return;
        }
        if (key === "Escape") {
          e.preventDefault();
          setActiveEventId(null);
        }
        return;
      }

      // --- grid mode: up/down between resources, left/right between days ------------------------
      if (key === "ArrowUp" || key === "ArrowDown") {
        e.preventDefault();
        setActiveItem((i) => Math.min(Math.max(i + (key === "ArrowUp" ? -1 : 1), 0), lastItem));
        return;
      }
      if (key === "ArrowLeft" || key === "ArrowRight") {
        e.preventDefault();
        setActiveDayIndex((d) =>
          Math.min(Math.max(d + (key === "ArrowLeft" ? -1 : 1), 0), Math.max(0, days.length - 1)),
        );
        return;
      }
      if (key === "Home" || key === "End") {
        e.preventDefault();
        setActiveDayIndex(key === "Home" ? 0 : Math.max(0, days.length - 1));
        return;
      }
      if (activate) {
        // On a group header, Enter toggles it.
        if (currentItem?.kind === "group") {
          e.preventDefault();
          toggleGroup(currentItem.group.id);
          return;
        }
        // Enter acts on what is UNDER THE CURSOR, exactly as a click acts on what is under the
        // pointer: on a cell holding events it descends into them; on a FREE cell it creates. The old
        // rule asked whether the whole ROW was empty, which left a keyboard user unable to book an
        // empty Thursday just because the resource had something on Monday — while a mouse user
        // could. A busy row no longer freezes its own free days.
        if (eventsUnderCursor.length > 0) {
          e.preventDefault();
          setActiveEventId(eventsUnderCursor[0]!.id);
          return;
        }
        if (currentRow?.resource && onRangeSelect && days.length > 0) {
          // Build a POSITIVE, window-bounded slot; never emit a zero-length range.
          const day = days[Math.min(activeDayIndex, days.length - 1)]!;
          const startMinute = timeline.config.dayStartHour * 60;
          const endMinute = Math.min(startMinute + 60, timeline.config.dayEndHour * 60);
          if (endMinute > startMinute) {
            e.preventDefault();
            onRangeSelect(
              computeRangeSelection(
                { dateOnly: day, minuteOfDay: startMinute, resourceId: currentRow.resource.id },
                { dateOnly: day, minuteOfDay: endMinute, resourceId: currentRow.resource.id },
              ),
            );
            announce(
              messages.createHere(
                `${currentRow.resource.title} · ${formatDayColumnHeader(day, locale)} ${minuteToTimeLabel(day, startMinute, locale)}`,
              ),
            );
          }
        }
      }
    },
    [
      kbGrab,
      activeEventId,
      navigableEvents,
      eventsUnderCursor,
      currentItem,
      currentRow,
      timeline.items.length,
      timeline.config,
      days,
      activeDayIndex,
      onEventDrop,
      onEventResize,
      onEventClick,
      onRangeSelect,
      stepGrab,
      commitGrab,
      toggleGroup,
      announce,
      messages,
      locale,
    ],
  );

  return { activeItem, activeEventId, kbGrab, currentRow, activeDescendantId, handleKeyDown };
}
