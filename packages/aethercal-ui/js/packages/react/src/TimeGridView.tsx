import {
  type CalendarEvent,
  type ContextMenuPayload,
  type Edge,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type RangeSelectPayload,
  type TimeGridBlock,
  type TimeGridConfig,
  DEFAULT_SNAP_MINUTES,
  addCalendarDays,
  buildTimeGrid,
  clampMinuteToWindow,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  formatLocalDateTime,
  fractionToMinuteOfDay,
  initialInteractionState,
  interactionReducer,
  layoutDayColumn,
  nextGridIndex,
  nowMarkerFraction,
  parseLocalDateTime,
  stepInstantMinutes,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { KeyboardHint, LiveRegion } from "./a11y";
import { EventChip } from "./EventChip";
import { type CalendarMessages, resolveMessages } from "./i18n";
import { formatEventTime } from "./labels";
import { ensureCalendarStyles } from "./styles";
import { formatDayColumnHeader, formatHourLabel, formatTimeGridTitle } from "./timeGridLabels";
import { ensureTimeGridStyles } from "./timeGridStyles";
import type { ThemeTokens } from "./theme";

export interface TimeGridViewProps {
  /** "week" (7 columns) or "day" (1 column). Both use this one engine. */
  view: "week" | "day";
  /** The day-only strings to render as columns (7 for week, 1 for day), in display order. */
  days: readonly string[];
  events: readonly CalendarEvent[];
  locale: string;
  /** Visible hour window (default 0..24). */
  config: TimeGridConfig;
  /** Current wall-clock time, used for the "now" line and the today highlight (injectable). */
  now: Date;
  /**
   * Resolved message pack (all-day / continues / ends labels + a11y announcements). Optional so the
   * component can be used standalone; when omitted it is derived from `locale` + the legacy label
   * props below. AetherCalendar always passes it.
   */
  messages?: CalendarMessages;
  /** Inline `--ac-*` theme overrides applied to the grid root (a preset or a custom object). */
  themeVars?: ThemeTokens;
  /** Legacy override for the all-day rail label (prefer `messages`). */
  allDayLabel?: string;
  /** Legacy override for the cross-midnight "continues" label (prefer `messages`). */
  continuesLabel?: string;
  /** Legacy override for the multi-day final-day "ends {t}" label (prefer `messages`). */
  formatEndsLabel?: (endTimeLabel: string) => string;
  onEventDrop?: (payload: EventDropPayload) => void;
  /** Drag a resize handle on an event's top/bottom edge to change its duration (F2-D). */
  onEventResize?: (payload: EventResizePayload) => void;
  /** Drag across empty grid space to create a new event (F2-D). */
  onRangeSelect?: (payload: RangeSelectPayload) => void;
  /** Click an event (F2-D). */
  onEventClick?: (payload: EventClickPayload) => void;
  /** Right-click / context-menu on an event or an empty slot (F2-D). */
  onContextMenu?: (payload: ContextMenuPayload) => void;
  /** Events with an in-flight optimistic mutation (rendered with a pending affordance). */
  pendingIds?: ReadonlySet<string>;
  /** Events whose mutation was just reverted (rendered with the rollback flash). */
  rolledBackIds?: ReadonlySet<string>;
}

/** Allow setting `--ac-*` custom properties (and the numeric column count) via inline style. */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string | number>;

/** The in-flight pointer gesture (resize or select), tracked in a ref so window listeners see live data. */
type Gesture =
  | {
      kind: "resize";
      pointerId: number;
      eventId: string;
      edge: Edge;
      dateOnly: string;
      colEl: HTMLElement;
      payload: EventResizePayload | null;
    }
  | {
      kind: "select";
      pointerId: number;
      anchorDate: string;
      anchorCol: HTMLElement;
      anchorMinute: number;
      currentDate: string;
      currentCol: HTMLElement;
      currentMinute: number;
    };

/**
 * In-flight KEYBOARD gesture: moving an event (new start) or resizing its end edge (F2-E). `minute`
 * starts at the event's ORIGINAL minute-of-day (unclamped, so a day-only move preserves a time that
 * sits outside the visible window); the window clamp is applied only when an Up/Down step recomputes
 * it. `moved` tracks whether any arrow was pressed, so confirming without moving is a strict no-op.
 */
type KbGrab =
  | { kind: "move"; eventId: string; dateOnly: string; minute: number; moved: boolean }
  | { kind: "resize"; eventId: string; dateOnly: string; minute: number; moved: boolean };

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const pct = (fraction: number): string => `${fraction * 100}%`;

const EMPTY_IDS: ReadonlySet<string> = new Set();

/** Minute-of-day (0..1440) of an ISO local datetime string. */
function minuteOfDayOf(iso: string): number {
  const d = parseLocalDateTime(iso);
  return d.getHours() * 60 + d.getMinutes();
}

/** A locale time label for a minute-of-day on a given day (handles the 1440 window end gracefully). */
function minuteToTimeLabel(dateOnly: string, minute: number, locale: string): string {
  const base = parseLocalDateTime(`${dateOnly}T00:00:00`);
  const d = new Date(base.getFullYear(), base.getMonth(), base.getDate(), 0, minute, 0);
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(d);
}

/**
 * The honest time label for one timed block, given WHERE its column sits in the event's local-day
 * span: its start time on the start day, a "continues" label on a day it passes fully through, and
 * its real end time on the final day. Mirrors AgendaView's `rowTimeLabel` so both views label a
 * cross-midnight event the same way, instead of the start time bleeding onto every day it crosses.
 */
function blockTimeLabel(
  block: Pick<TimeGridBlock, "event" | "isContinuation" | "continuesAfter">,
  locale: string,
  continuesLabel: string,
  formatEndsLabel: (endTimeLabel: string) => string,
): string {
  const { event, isContinuation, continuesAfter } = block;
  if (!isContinuation) return formatEventTime(event.start, locale); // start day (or single day)
  if (continuesAfter) return continuesLabel; // a full pass-through day
  return formatEndsLabel(formatEventTime(event.end, locale)); // the final day: ends here
}

/** Fraction [0, 1] of a column the pointer's `clientY` falls at (0 when the column has no measured height). */
function fractionInColumn(clientY: number, colEl: HTMLElement): number {
  const rect = colEl.getBoundingClientRect();
  if (!(rect.height > 0)) return 0;
  return (clientY - rect.top) / rect.height;
}

/**
 * The shared week/day time-grid engine (AetherCal-06 §5): 7 (or 1) day columns × an hour axis, an
 * all-day rail above the grid, overlap-packed event blocks, plus the full F2-D pointer interaction
 * set AND F2-E keyboard access — arrow keys move the active day column, Enter descends into a
 * column's events, and a grabbed event moves (day + time) or resizes (end edge) by keyboard with
 * live-region announcements, committing on Enter / reverting on Escape. All date/geometry math comes
 * from `@aethercal/calendar-core` (the RF-23 boundary); this file only renders and translates input.
 */
export function TimeGridView(props: TimeGridViewProps): React.JSX.Element {
  const {
    view,
    days,
    events,
    locale,
    config,
    now,
    themeVars,
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onEventClick,
    onContextMenu,
    pendingIds = EMPTY_IDS,
    rolledBackIds = EMPTY_IDS,
  } = props;

  // Message pack: use the one AetherCalendar resolved, or derive it from `locale` + legacy label
  // props for a standalone consumer (backward compatible with the F2-B/D public API).
  const messages = React.useMemo<CalendarMessages>(() => {
    if (props.messages) return props.messages;
    const overrides: Partial<CalendarMessages> = {
      ...(props.allDayLabel !== undefined ? { allDay: props.allDayLabel } : {}),
      ...(props.continuesLabel !== undefined ? { continues: props.continuesLabel } : {}),
      ...(props.formatEndsLabel !== undefined ? { endsAt: props.formatEndsLabel } : {}),
    };
    return resolveMessages(locale, overrides);
  }, [props.messages, props.allDayLabel, props.continuesLabel, props.formatEndsLabel, locale]);

  React.useEffect(() => {
    ensureCalendarStyles();
    ensureTimeGridStyles();
  }, []);

  const grid = React.useMemo(() => buildTimeGrid(days, events, config), [days, events, config]);
  const nowFraction = React.useMemo(() => nowMarkerFraction(now, config), [now, config]);
  const nowDateKey = React.useMemo(() => toDateOnly(formatLocalDateTime(now)), [now]);

  const [interaction, dispatch] = React.useReducer(interactionReducer, initialInteractionState);
  const gestureRef = React.useRef<Gesture | null>(null);
  const [resizePreview, setResizePreview] = React.useState<EventResizePayload | null>(null);
  const [selectBand, setSelectBand] = React.useState<{
    dateOnly: string;
    topFraction: number;
    heightFraction: number;
  } | null>(null);

  const dropEnabled = Boolean(onEventDrop);
  const resizeEnabled = Boolean(onEventResize);
  const selectEnabled = Boolean(onRangeSelect);
  const dragging = interaction.status === "dragging";

  // ---- move (HTML5 drag): drop changes the DAY and, from the drop's vertical position, the HOUR --
  const handleDrop = React.useCallback(
    (targetDateOnly: string, timed: boolean) => (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      if (interaction.status !== "dragging") {
        dispatch({ type: "COMMIT" });
        return;
      }
      const draggedId = interaction.eventId;
      const transferred = e.dataTransfer.getData("text/plain");
      dispatch({ type: "COMMIT" });
      if (transferred && transferred !== draggedId) return;
      if (!onEventDrop) return;
      const dragged = events.find((candidate) => candidate.id === draggedId);
      if (!dragged || dragged.editable === false) return;
      let minute: number | null = null;
      if (timed && dragged.allDay !== true) {
        const col = e.currentTarget;
        const rect = col.getBoundingClientRect();
        if (rect.height > 0 && Number.isFinite(e.clientY)) {
          minute = fractionToMinuteOfDay((e.clientY - rect.top) / rect.height, grid.config);
        }
      }
      onEventDrop(computeMovedRange(dragged, targetDateOnly, minute));
    },
    [interaction, events, onEventDrop, grid.config],
  );

  const onDragStart = React.useCallback((eventId: string) => {
    if (gestureRef.current?.kind === "resize") return;
    dispatch({ type: "DRAG_START", eventId });
  }, []);
  const onDragEnd = React.useCallback(() => dispatch({ type: "CANCEL" }), []);

  // ---- resize / select (Pointer Events): a ref + window listeners track the drag to commit/cancel -
  const startResize = React.useCallback(
    (event: CalendarEvent, edge: Edge) => (e: React.PointerEvent<HTMLDivElement>) => {
      if (!onEventResize || event.editable === false || e.button !== 0) return;
      if (gestureRef.current) return; // a gesture is already in progress — first pointer wins
      const colEl = (e.currentTarget as HTMLElement).closest<HTMLElement>(".aethercal-tg-col");
      if (!colEl?.dataset.date) return;
      e.preventDefault();
      e.stopPropagation();
      gestureRef.current = {
        kind: "resize",
        pointerId: e.pointerId,
        eventId: event.id,
        edge,
        dateOnly: colEl.dataset.date,
        colEl,
        payload: null,
      };
      e.currentTarget.setPointerCapture?.(e.pointerId);
      dispatch({ type: "RESIZE_START", eventId: event.id, edge });
    },
    [onEventResize],
  );

  const startSelect = React.useCallback(
    (dateOnly: string) => (e: React.PointerEvent<HTMLDivElement>) => {
      if (!onRangeSelect || e.button !== 0) return;
      if (gestureRef.current) return; // a gesture is already in progress — first pointer wins
      if ((e.target as Element).closest("[data-event-id], button")) return;
      const colEl = e.currentTarget;
      const minute = fractionToMinuteOfDay(fractionInColumn(e.clientY, colEl), grid.config);
      gestureRef.current = {
        kind: "select",
        pointerId: e.pointerId,
        anchorDate: dateOnly,
        anchorCol: colEl,
        anchorMinute: minute,
        currentDate: dateOnly,
        currentCol: colEl,
        currentMinute: minute,
      };
      colEl.setPointerCapture?.(e.pointerId);
      dispatch({ type: "SELECT_START", point: { dateOnly, minuteOfDay: minute } });
    },
    [onRangeSelect, grid.config],
  );

  const gestureActive = interaction.status === "resizing" || interaction.status === "selecting";
  React.useLayoutEffect(() => {
    if (!gestureActive) return;
    const onMove = (e: PointerEvent): void => {
      const g = gestureRef.current;
      if (!g || e.pointerId !== g.pointerId) return;
      if (g.kind === "resize") {
        const under = document
          .elementFromPoint(e.clientX, e.clientY)
          ?.closest<HTMLElement>(".aethercal-tg-col");
        const col = under?.dataset.date ? under : g.colEl;
        const minute = fractionToMinuteOfDay(fractionInColumn(e.clientY, col), grid.config);
        const event = events.find((candidate) => candidate.id === g.eventId);
        if (!event) return;
        const payload = computeResize(event, g.edge, col.dataset.date ?? g.dateOnly, minute);
        g.payload = payload;
        setResizePreview(payload);
      } else {
        const under = document
          .elementFromPoint(e.clientX, e.clientY)
          ?.closest<HTMLElement>(".aethercal-tg-col");
        const currentCol = under?.dataset.date ? under : g.currentCol;
        g.currentCol = currentCol;
        g.currentDate = currentCol.dataset.date ?? g.anchorDate;
        g.currentMinute = fractionToMinuteOfDay(fractionInColumn(e.clientY, currentCol), grid.config);
        const range = computeRangeSelection(
          { dateOnly: g.anchorDate, minuteOfDay: g.anchorMinute },
          { dateOnly: g.currentDate, minuteOfDay: g.currentMinute },
        );
        const blocks =
          g.currentDate === g.anchorDate
            ? layoutDayColumn([{ id: "__sel", title: "", start: range.start, end: range.end }], g.anchorDate, grid.config)
            : [];
        const b = blocks[0];
        setSelectBand(
          b ? { dateOnly: g.anchorDate, topFraction: b.topFraction, heightFraction: b.heightFraction } : null,
        );
      }
    };
    const finish = (commit: boolean): void => {
      const g = gestureRef.current;
      gestureRef.current = null;
      setResizePreview(null);
      setSelectBand(null);
      if (commit && g) {
        if (g.kind === "resize" && g.payload && onEventResize) onEventResize(g.payload);
        if (
          g.kind === "select" &&
          onRangeSelect &&
          (g.currentDate !== g.anchorDate || g.currentMinute !== g.anchorMinute)
        ) {
          onRangeSelect(
            computeRangeSelection(
              { dateOnly: g.anchorDate, minuteOfDay: g.anchorMinute },
              { dateOnly: g.currentDate, minuteOfDay: g.currentMinute },
            ),
          );
        }
      }
      dispatch({ type: commit ? "COMMIT" : "CANCEL" });
    };
    const onUp = (e: PointerEvent): void => {
      if (gestureRef.current && e.pointerId !== gestureRef.current.pointerId) return;
      finish(true);
    };
    const onCancel = (e: PointerEvent): void => {
      if (gestureRef.current && e.pointerId !== gestureRef.current.pointerId) return;
      finish(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") finish(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("keydown", onKey);
    };
  }, [gestureActive, events, grid.config, onEventResize, onRangeSelect]);

  const emptyContextMenu = React.useCallback(
    (dateOnly: string, timed: boolean) => (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onContextMenu) return;
      if ((e.target as Element).closest("[data-event-id], button")) return;
      e.preventDefault();
      if (!timed) {
        onContextMenu({ start: `${dateOnly}T00:00:00` });
        return;
      }
      const minute = fractionToMinuteOfDay(fractionInColumn(e.clientY, e.currentTarget), grid.config);
      const midnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
      const slot = new Date(midnight.getFullYear(), midnight.getMonth(), midnight.getDate(), 0, minute, 0);
      onContextMenu({ start: formatLocalDateTime(slot) });
    },
    [onContextMenu, grid.config],
  );

  // ---- keyboard access (F2-E): active column -> event mode -> grab move / resize -----------------
  const baseId = React.useId();
  const columnDates = React.useMemo(() => grid.columns.map((c) => c.dateOnly), [grid.columns]);
  const [activeDate, setActiveDate] = React.useState<string>(
    () => (columnDates.includes(nowDateKey) ? nowDateKey : columnDates[0]) ?? "",
  );
  const [activeEventId, setActiveEventId] = React.useState<string | null>(null);
  const [kbGrab, setKbGrab] = React.useState<KbGrab | null>(null);
  const [announcement, setAnnouncement] = React.useState("");

  React.useEffect(() => {
    if (!columnDates.includes(activeDate)) {
      setActiveDate(columnDates[0] ?? "");
      setActiveEventId(null);
      setKbGrab(null);
    }
  }, [columnDates, activeDate]);

  const colDomId = (dateOnly: string): string => `${baseId}-col-${dateOnly}`;
  const evtDomId = (dateOnly: string, eventId: string): string =>
    `${baseId}-e-${dateOnly}-${eventId}`;
  const hintId = `${baseId}-hint`;
  const snap = DEFAULT_SNAP_MINUTES;

  // An event is keyboard-actionable when it can be clicked, moved, OR resized — so a grid wired with
  // ONLY onEventResize is still reachable (event mode -> "r"), and a locked (editable:false) event
  // with no click handler is NOT actionable (Crisol round-1/7).
  const eventInteractive = React.useCallback(
    (event: CalendarEvent): boolean =>
      Boolean(onEventClick) ||
      (event.editable !== false && Boolean(onEventDrop || onEventResize)),
    [onEventClick, onEventDrop, onEventResize],
  );

  /**
   * The events in the active column for event-mode cycling: the all-day rail events first, then the
   * timed blocks in display order — so keyboard users reach all-day events too, not just timed ones
   * (Crisol round-2). An all-day event grabs a DAY-only move (no time / no resize).
   */
  const activeColumnEvents = React.useMemo(() => {
    const col = grid.columns.find((c) => c.dateOnly === activeDate);
    return col ? [...col.allDay, ...col.timed.map((b) => b.event)] : [];
  }, [grid.columns, activeDate]);

  // Only ACTIONABLE events are keyboard-navigable — entering/cycling never lands the active
  // descendant on a locked, unactionable event (Crisol round-7). Non-interactive events still render
  // and are read via the list/agenda surface.
  const navigableColumnEvents = React.useMemo(
    () => activeColumnEvents.filter((ev) => eventInteractive(ev)),
    [activeColumnEvents, eventInteractive],
  );

  // Keep the keyboard focus valid when `events` (or the handlers) change under us: if the
  // active/grabbed event is no longer a navigable event in the active column, cancel any grab and
  // fall back to the column so aria-activedescendant never points at a vanished/unactionable node
  // (Crisol round-5/7).
  React.useEffect(() => {
    const navigable = new Set(navigableColumnEvents.map((ev) => ev.id));
    if (kbGrab && !navigable.has(kbGrab.eventId)) {
      setKbGrab(null);
      setActiveEventId(null);
    } else if (!kbGrab && activeEventId !== null && !navigable.has(activeEventId)) {
      setActiveEventId(null);
    }
  }, [navigableColumnEvents, activeEventId, kbGrab]);

  const activeDescendantId = kbGrab
    ? evtDomId(activeDate, kbGrab.eventId)
    : activeEventId
      ? evtDomId(activeDate, activeEventId)
      : colDomId(activeDate);

  const stepGrab = React.useCallback(
    (key: string) => {
      // Compute everything from the closure and set state plainly — no side effects (announce) inside
      // a state updater (Crisol round-6).
      const prev = kbGrab;
      if (!prev) return;
      let dateOnly = prev.dateOnly;
      let minute = prev.minute;
      const event = events.find((ev) => ev.id === prev.eventId);
      const isAllDay = event?.allDay === true;
      // An all-day event has no time-of-day: Up/Down are no-ops, Left/Right change the day. The day
      // is NOT clamped to the visible columns: a keyboard move can reschedule to any day (incl.
      // off-screen / next week), and an event whose start is more than one day before the visible
      // range must still be steppable INTO it (Crisol round-3). Up/Down step the (day, minute)
      // INSTANT so a time at exactly midnight rolls across the day boundary (Crisol round-8).
      if (!isAllDay && (key === "ArrowUp" || key === "ArrowDown")) {
        const stepped = stepInstantMinutes(dateOnly, minute, key === "ArrowUp" ? -snap : snap, grid.config);
        dateOnly = stepped.dateOnly;
        minute = stepped.minuteOfDay;
      } else if (key === "ArrowLeft") dateOnly = addCalendarDays(dateOnly, -1);
      else if (key === "ArrowRight") dateOnly = addCalendarDays(dateOnly, 1);
      // A step blocked by the window clamp (e.g. Up at the first slot) changes nothing — do NOT mark
      // it as a move, so a later confirm stays a no-op rather than a spurious mutation (Crisol
      // round-5).
      if (dateOnly === prev.dateOnly && minute === prev.minute) return;
      if (event) {
        if (prev.kind === "move") {
          setAnnouncement(
            messages.movedTo(
              isAllDay
                ? formatDayColumnHeader(dateOnly, locale)
                : `${formatDayColumnHeader(dateOnly, locale)} ${minuteToTimeLabel(dateOnly, minute, locale)}`,
            ),
          );
        } else {
          const payload = computeResize(event, "end", dateOnly, minute);
          setAnnouncement(
            messages.resizedTo(
              `${formatEventTime(payload.start, locale)} – ${formatEventTime(payload.end, locale)}`,
            ),
          );
        }
      }
      setKbGrab({ ...prev, dateOnly, minute, moved: true });
    },
    [kbGrab, snap, grid.config, events, messages, locale],
  );

  const commitGrab = React.useCallback(() => {
    // Read the grab from the closure and run all effects (mutation callbacks + setters) in the body.
    // Doing this inside a `setKbGrab` updater is unsafe: state updaters must be pure and React may
    // re-invoke them (StrictMode / concurrent), firing onEventDrop/onEventResize twice (Crisol
    // round-6). `setKbGrab(null)` stays a plain, side-effect-free update.
    const prev = kbGrab;
    if (!prev) return;
    // Confirming a grab without ever pressing an arrow must NOT emit a mutation (Crisol round-4).
    if (!prev.moved) {
      setActiveEventId(prev.eventId);
      setKbGrab(null);
      return;
    }
    const event = events.find((ev) => ev.id === prev.eventId);
    if (event && event.editable !== false && prev.kind === "move" && onEventDrop) {
      // All-day events move by day only (minute = null -> day-only recompute); timed events move to
      // the snapped day+minute.
      const payload = computeMovedRange(event, prev.dateOnly, event.allDay === true ? null : prev.minute);
      onEventDrop(payload);
      // Land on the target column when it is visible; otherwise (moved off-screen / next week) stay
      // on the current visible column so the active descendant never points at a non-rendered
      // column (Crisol round-2/3).
      const targetDay = toDateOnly(payload.start);
      setActiveDate(columnDates.includes(targetDay) ? targetDay : activeDate);
      setActiveEventId(null);
      setAnnouncement(
        messages.dropped(
          event.allDay === true
            ? formatDayColumnHeader(prev.dateOnly, locale)
            : minuteToTimeLabel(prev.dateOnly, prev.minute, locale),
        ),
      );
    } else if (event && event.editable !== false && prev.kind === "resize" && onEventResize) {
      const payload = computeResize(event, "end", prev.dateOnly, prev.minute);
      onEventResize(payload);
      setActiveEventId(prev.eventId); // a resize keeps the same start day -> the event id stays valid
      setAnnouncement(
        messages.resized(
          `${formatEventTime(payload.start, locale)} – ${formatEventTime(payload.end, locale)}`,
        ),
      );
    } else {
      setActiveEventId(prev.eventId);
    }
    setKbGrab(null);
  }, [kbGrab, events, onEventDrop, onEventResize, columnDates, activeDate, messages, locale]);

  const handleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const { key } = e;
      const activate = key === "Enter" || key === " " || key === "Spacebar";
      const arrow =
        key === "ArrowUp" || key === "ArrowDown" || key === "ArrowLeft" || key === "ArrowRight";

      // --- grabbed: arrows step the move/resize target, Enter commits, Escape reverts -------------
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
          setAnnouncement(messages.cancelled);
          return;
        }
        return;
      }

      // --- event mode: cycle events (Up/Down), grab move (Enter) / resize (r), leave (Escape) -----
      if (activeEventId) {
        const idx = navigableColumnEvents.findIndex((ev) => ev.id === activeEventId);
        if (key === "ArrowDown") {
          e.preventDefault();
          if (idx >= 0 && idx < navigableColumnEvents.length - 1) {
            setActiveEventId(navigableColumnEvents[idx + 1]!.id);
          }
          return;
        }
        if (key === "ArrowUp") {
          e.preventDefault();
          if (idx > 0) setActiveEventId(navigableColumnEvents[idx - 1]!.id);
          else setActiveEventId(null);
          return;
        }
        if (key === "ArrowLeft" || key === "ArrowRight") {
          e.preventDefault();
          setActiveEventId(null);
          const idxCol = columnDates.indexOf(activeDate);
          setActiveDate(columnDates[nextGridIndex(idxCol, key, 1, columnDates.length)]!);
          return;
        }
        if (activate) {
          e.preventDefault();
          const event = navigableColumnEvents.find((ev) => ev.id === activeEventId);
          if (!event) return;
          if (event.editable !== false && onEventDrop) {
            setKbGrab({
              kind: "move",
              eventId: event.id,
              dateOnly: toDateOnly(event.start),
              minute: minuteOfDayOf(event.start), // raw original — clamp only on step
              moved: false,
            });
            setAnnouncement(messages.grabbedMoveHint(event.title));
          } else if (onEventClick) {
            onEventClick({ id: event.id });
          }
          return;
        }
        if ((key === "r" || key === "R") && onEventResize) {
          e.preventDefault();
          const event = navigableColumnEvents.find((ev) => ev.id === activeEventId);
          // All-day events have no end-edge time to resize — resize is a timed-event affordance only.
          if (event && event.allDay !== true && event.editable !== false) {
            setKbGrab({
              kind: "resize",
              eventId: event.id,
              dateOnly: toDateOnly(event.end),
              minute: minuteOfDayOf(event.end), // raw original — clamp only on step
              moved: false,
            });
            setAnnouncement(messages.grabbedResizeHint(event.title));
          }
          return;
        }
        if (key === "Escape") {
          e.preventDefault();
          setActiveEventId(null);
          return;
        }
        return;
      }

      // --- grid mode: navigate columns; Down descends into events; Enter enters/creates -----------
      if (key === "ArrowLeft" || key === "ArrowRight" || key === "Home" || key === "End") {
        e.preventDefault();
        const idxCol = columnDates.indexOf(activeDate);
        setActiveDate(columnDates[nextGridIndex(idxCol, key, 1, columnDates.length)]!);
        return;
      }
      if (key === "ArrowDown") {
        if (navigableColumnEvents.length > 0) {
          e.preventDefault();
          setActiveEventId(navigableColumnEvents[0]!.id);
        }
        return;
      }
      if (activate) {
        if (navigableColumnEvents.length > 0) {
          e.preventDefault();
          setActiveEventId(navigableColumnEvents[0]!.id);
        } else if (activeColumnEvents.length === 0 && onRangeSelect) {
          // Create ONLY on a column with no events at all — never over an occupied column (Crisol
          // round-2). Build a POSITIVE, window-bounded duration and never emit a zero-length range
          // (Crisol round-7): if the visible window has no room for a slot, Enter is a no-op.
          const windowEnd = grid.config.dayEndHour * 60;
          const startMinute = clampMinuteToWindow(grid.config.dayStartHour * 60, grid.config);
          const endMinute = Math.min(startMinute + 60, windowEnd);
          if (endMinute > startMinute) {
            e.preventDefault();
            onRangeSelect(
              computeRangeSelection(
                { dateOnly: activeDate, minuteOfDay: startMinute },
                { dateOnly: activeDate, minuteOfDay: endMinute },
              ),
            );
            setAnnouncement(
              messages.createHere(
                `${formatDayColumnHeader(activeDate, locale)} ${minuteToTimeLabel(activeDate, startMinute, locale)}`,
              ),
            );
          }
        }
      }
    },
    [
      kbGrab,
      activeEventId,
      activeDate,
      activeColumnEvents,
      navigableColumnEvents,
      columnDates,
      onEventDrop,
      onEventResize,
      onEventClick,
      onRangeSelect,
      stepGrab,
      commitGrab,
      grid.config,
      messages,
      locale,
    ],
  );

  const containerStyle: StyleWithVars = {
    "--ac-tg-cols": grid.columns.length,
    "--ac-tg-hours": grid.config.dayEndHour - grid.config.dayStartHour,
    ...(themeVars ?? {}),
  };

  const allDayLabel = messages.allDay;

  return (
    <>
      <div
        className={cx(
          "aethercal-calendar",
          "aethercal-timegrid",
          dragging && "is-dragging",
          interaction.status === "resizing" && "is-resizing",
          interaction.status === "selecting" && "is-selecting",
        )}
        role="grid"
        aria-label={formatTimeGridTitle(days, locale)}
        aria-describedby={hintId}
        aria-activedescendant={activeDescendantId}
        tabIndex={0}
        data-view={view}
        style={containerStyle}
        onKeyDown={handleKeyDown}
      >
        <div className="aethercal-tg-head" role="row">
          <div className="aethercal-tg-corner" />
          {grid.columns.map((col) => (
            <div
              key={col.dateOnly}
              role="columnheader"
              className={cx("aethercal-tg-colhead", col.dateOnly === nowDateKey && "is-today")}
              data-date={col.dateOnly}
            >
              <span className="aethercal-tg-colhead-date">{formatDayColumnHeader(col.dateOnly, locale)}</span>
            </div>
          ))}
        </div>

        <div className="aethercal-tg-allday" role="row">
          <div className="aethercal-tg-rowhead" role="rowheader">
            {allDayLabel}
          </div>
          {grid.columns.map((col) => (
            <div
              key={col.dateOnly}
              role="gridcell"
              className="aethercal-tg-allday-cell"
              data-date={col.dateOnly}
              onDragOver={dropEnabled ? (e) => e.preventDefault() : undefined}
              onDrop={dropEnabled ? handleDrop(col.dateOnly, false) : undefined}
              onContextMenu={onContextMenu ? emptyContextMenu(col.dateOnly, false) : undefined}
            >
              {col.allDay.map((event) => {
                const isEvtActive =
                  (kbGrab?.eventId === event.id && col.dateOnly === activeDate) ||
                  (!kbGrab && activeEventId === event.id && col.dateOnly === activeDate);
                return (
                  <EventChip
                    key={event.id}
                    id={evtDomId(col.dateOnly, event.id)}
                    event={event}
                    interactive={eventInteractive(event)}
                    isActive={isEvtActive}
                    isGrabbed={kbGrab?.eventId === event.id && col.dateOnly === activeDate}
                    timeLabel={null}
                    onDragStart={onDragStart}
                    onDragEnd={onDragEnd}
                    isPending={pendingIds.has(event.id)}
                    isRolledBack={rolledBackIds.has(event.id)}
                    {...(onEventClick ? { onClick: () => onEventClick({ id: event.id }) } : {})}
                    {...(onContextMenu ? { onContextMenu: () => onContextMenu({ id: event.id }) } : {})}
                  />
                );
              })}
            </div>
          ))}
        </div>

        <div className="aethercal-tg-body" role="row">
          <div className="aethercal-tg-gutter" role="presentation" aria-hidden="true">
            {grid.hourMarks.map((mark) => (
              <div key={mark.hour} className="aethercal-tg-hour" style={{ top: pct(mark.topFraction) }}>
                {formatHourLabel(mark.hour, locale)}
              </div>
            ))}
          </div>
          {grid.columns.map((col) => {
            const isColActive = !activeEventId && !kbGrab && col.dateOnly === activeDate;
            const isKbTarget = kbGrab?.dateOnly === col.dateOnly;
            return (
              <div
                key={col.dateOnly}
                id={colDomId(col.dateOnly)}
                role="gridcell"
                className={cx(
                  "aethercal-tg-col",
                  col.dateOnly === nowDateKey && "is-today",
                  isColActive && "is-active",
                  isKbTarget && "is-drop-target",
                )}
                data-date={col.dateOnly}
                onDragOver={dropEnabled ? (e) => e.preventDefault() : undefined}
                onDrop={dropEnabled ? handleDrop(col.dateOnly, true) : undefined}
                onPointerDown={selectEnabled ? startSelect(col.dateOnly) : undefined}
                onContextMenu={onContextMenu ? emptyContextMenu(col.dateOnly, true) : undefined}
              >
                {grid.hourMarks.map((mark) => (
                  <div
                    key={mark.hour}
                    className="aethercal-tg-line"
                    style={{ top: pct(mark.topFraction) }}
                    aria-hidden="true"
                  />
                ))}
                {selectBand && selectBand.dateOnly === col.dateOnly ? (
                  <div
                    className="aethercal-tg-select-band"
                    style={{ top: pct(selectBand.topFraction), height: pct(selectBand.heightFraction) }}
                    aria-hidden="true"
                  />
                ) : null}
                {col.timed.map((block) => {
                  const { event } = block;
                  const editable = event.editable !== false;
                  const timeLabel = blockTimeLabel(block, locale, messages.continues, messages.endsAt);
                  const previewed = resizePreview?.id === event.id ? resizePreview : null;
                  const previewBlock = previewed
                    ? layoutDayColumn(
                        [{ ...event, start: previewed.start, end: previewed.end }],
                        col.dateOnly,
                        grid.config,
                      )[0]
                    : undefined;
                  const top = previewBlock ? previewBlock.topFraction : block.topFraction;
                  const height = previewBlock ? previewBlock.heightFraction : block.heightFraction;
                  const isEvtActive =
                    (kbGrab?.eventId === event.id && col.dateOnly === activeDate) ||
                    (!kbGrab && activeEventId === event.id && col.dateOnly === activeDate);
                  const isGrabbed = kbGrab?.eventId === event.id && col.dateOnly === activeDate;
                  const style: StyleWithVars = {
                    top: pct(top),
                    height: pct(height),
                    left: pct(block.lane / block.laneCount),
                    width: pct(1 / block.laneCount),
                    ...(event.color ? { "--ac-tg-event-accent": event.color } : {}),
                  };
                  return (
                    <div
                      key={event.id}
                      id={evtDomId(col.dateOnly, event.id)}
                      className={cx(
                        "aethercal-tg-event",
                        !editable && "is-locked",
                        pendingIds.has(event.id) && "is-pending",
                        rolledBackIds.has(event.id) && "is-rolledback",
                        Boolean(previewed) && "is-resizing",
                        isEvtActive && "is-active",
                        isGrabbed && "is-grabbed",
                      )}
                      {...(eventInteractive(event) ? { role: "button" } : {})}
                      draggable={editable}
                      data-event-id={event.id}
                      data-lane={block.lane}
                      data-lane-count={block.laneCount}
                      aria-label={`${timeLabel} ${event.title}`}
                      title={event.title}
                      style={style}
                      onDragStart={(e) => {
                        if (gestureRef.current?.kind === "resize") {
                          e.preventDefault();
                          return;
                        }
                        e.dataTransfer.setData("text/plain", event.id);
                        e.dataTransfer.effectAllowed = "move";
                        onDragStart(event.id);
                      }}
                      onDragEnd={onDragEnd}
                      onClick={onEventClick ? () => onEventClick({ id: event.id }) : undefined}
                      onContextMenu={
                        onContextMenu
                          ? (e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              onContextMenu({ id: event.id });
                            }
                          : undefined
                      }
                    >
                      <time className="aethercal-tg-event-time">{timeLabel}</time>
                      {/* Whitespace text node so the block's visible text ("9:00Consulta") matches
                          its accessible name ("9:00 Consulta") — the visual gap is a flex `gap`, not
                          text (finding M-2 / WCAG 2.5.3). No layout change; SR name unchanged. */}
                      {" "}
                      <span className="aethercal-tg-event-title">{event.title}</span>
                      {resizeEnabled && editable ? (
                        <>
                          <div
                            className="aethercal-tg-resize-handle aethercal-tg-resize-handle-start"
                            data-edge="start"
                            aria-hidden="true"
                            draggable={false}
                            onPointerDown={startResize(event, "start")}
                          />
                          <div
                            className="aethercal-tg-resize-handle aethercal-tg-resize-handle-end"
                            data-edge="end"
                            aria-hidden="true"
                            draggable={false}
                            onPointerDown={startResize(event, "end")}
                          />
                        </>
                      ) : null}
                    </div>
                  );
                })}
                {nowFraction !== null && col.dateOnly === nowDateKey ? (
                  <div className="aethercal-now-indicator" style={{ top: pct(nowFraction) }} aria-hidden="true" />
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      <KeyboardHint id={hintId} text={messages.keyboardHint} />
      <LiveRegion message={announcement} />
    </>
  );
}
