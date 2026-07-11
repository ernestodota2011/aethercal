import {
  type CalendarEvent,
  type ContextMenuPayload,
  type Edge,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type RangeSelectPayload,
  type TimeGridConfig,
  buildTimeGrid,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  formatLocalDateTime,
  fractionToMinuteOfDay,
  initialInteractionState,
  interactionReducer,
  layoutDayColumn,
  nowMarkerFraction,
  parseLocalDateTime,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { EventChip } from "./EventChip";
import { formatEventTime } from "./labels";
import { ensureCalendarStyles } from "./styles";
import {
  DEFAULT_ALL_DAY_LABEL,
  formatDayColumnHeader,
  formatHourLabel,
  formatTimeGridTitle,
} from "./timeGridLabels";
import { ensureTimeGridStyles } from "./timeGridStyles";

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
  /** Label for the all-day rail (i18n presets are F2-E; overridable, English default). */
  allDayLabel?: string;
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

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const pct = (fraction: number): string => `${fraction * 100}%`;

const EMPTY_IDS: ReadonlySet<string> = new Set();

/** Fraction [0, 1] of a column the pointer's `clientY` falls at (0 when the column has no measured height). */
function fractionInColumn(clientY: number, colEl: HTMLElement): number {
  const rect = colEl.getBoundingClientRect();
  if (!(rect.height > 0)) return 0;
  return (clientY - rect.top) / rect.height;
}

/**
 * The shared week/day time-grid engine (AetherCal-06 §5): 7 (or 1) day columns × an hour axis, an
 * all-day rail above the grid, overlap-packed event blocks, plus the full F2-D interaction set —
 * drag-to-reschedule (day AND time-of-day), resize by dragging an edge handle, drag-select on empty
 * space to create, click, and context-menu — driven by the headless `interactionReducer`. All
 * date/geometry math comes from `@aethercal/calendar-core` (the RF-23 boundary); this file only
 * renders and translates pointer input into gestures.
 */
export function TimeGridView(props: TimeGridViewProps): React.JSX.Element {
  const {
    view,
    days,
    events,
    locale,
    config,
    now,
    allDayLabel = DEFAULT_ALL_DAY_LABEL,
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onEventClick,
    onContextMenu,
    pendingIds = EMPTY_IDS,
    rolledBackIds = EMPTY_IDS,
  } = props;

  React.useEffect(() => {
    // TimeGridView is a public export usable on its own, so it must install BOTH the base `--ac-*`
    // tokens (from styles.ts) and its own time-grid CSS — the grid's borders/colors reference the
    // base tokens. Both are idempotent; when mounted via AetherCalendar the base is already present.
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
    // A resize gesture must not also start an HTML5 move drag from the same block.
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
      // Empty space = anywhere in the column that is NOT an event or a button. Testing the target's
      // ancestry (not target===currentTarget) means decorative children (hour lines, the select band)
      // never block a selection, without relying on their CSS pointer-events being none.
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
      // Capture the pointer so the gesture stays bound to this column; if the platform starts a
      // native scroll instead (touch), it fires pointercancel, which aborts the selection cleanly.
      // (Full touch drag-select needs a long-press affordance — deferred to F2-E; see note in the PR.)
      colEl.setPointerCapture?.(e.pointerId);
      dispatch({ type: "SELECT_START", point: { dateOnly, minuteOfDay: minute } });
    },
    [onRangeSelect, grid.config],
  );

  const gestureActive = interaction.status === "resizing" || interaction.status === "selecting";
  // useLayoutEffect (not useEffect): the window listeners must be attached synchronously right after
  // the pointerdown that starts the gesture commits, before the browser dispatches the next pointer
  // event — a passive effect could miss a pointerup that fires before it flushes.
  React.useLayoutEffect(() => {
    if (!gestureActive) return;
    const onMove = (e: PointerEvent): void => {
      const g = gestureRef.current;
      // Only the pointer that started the gesture drives it — a second (multi-touch) pointer is ignored.
      if (!g || e.pointerId !== g.pointerId) return;
      if (g.kind === "resize") {
        // Resize can drag an edge into another day column (e.g. extend the end past midnight),
        // resolving the column under the pointer — the same way selection does.
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
        // A timed selection can extend into another day column: resolve the column under the pointer
        // so the emitted range spans days (the core geometry already supports it).
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
        // The live band is a single-column indicator; only draw it when the range stays in one day.
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
      // Same "empty space" test as select: a right-click on a decorative child (hour line) still
      // opens the create-here menu; a right-click on an event is handled by the event's own handler.
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

  // Drive the day-column count AND the visible-hours count from geometry so a narrowed window
  // (e.g. business hours 08–18) is only as tall as its hours, not a fixed 24 (Crisol correctness).
  const containerStyle: StyleWithVars = {
    "--ac-tg-cols": grid.columns.length,
    "--ac-tg-hours": grid.config.dayEndHour - grid.config.dayStartHour,
  };

  return (
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
      data-view={view}
      style={containerStyle}
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
            {col.allDay.map((event) => (
              <EventChip
                key={event.id}
                event={event}
                timeLabel={null}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
                isPending={pendingIds.has(event.id)}
                isRolledBack={rolledBackIds.has(event.id)}
                {...(onEventClick ? { onClick: () => onEventClick({ id: event.id }) } : {})}
                {...(onContextMenu ? { onContextMenu: () => onContextMenu({ id: event.id }) } : {})}
              />
            ))}
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
        {grid.columns.map((col) => (
          <div
            key={col.dateOnly}
            role="gridcell"
            className={cx("aethercal-tg-col", col.dateOnly === nowDateKey && "is-today")}
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
              const timeLabel = formatEventTime(event.start, locale);
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
                  className={cx(
                    "aethercal-tg-event",
                    !editable && "is-locked",
                    pendingIds.has(event.id) && "is-pending",
                    rolledBackIds.has(event.id) && "is-rolledback",
                    Boolean(previewed) && "is-resizing",
                  )}
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
        ))}
      </div>
    </div>
  );
}
