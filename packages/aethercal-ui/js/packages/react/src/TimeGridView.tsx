import {
  type CalendarEvent,
  type EventDropPayload,
  type TimeGridBlock,
  type TimeGridConfig,
  buildTimeGrid,
  computeDroppedRange,
  dragReducer,
  formatLocalDateTime,
  initialDragState,
  isDragging,
  nowMarkerFraction,
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
}

/** Allow setting `--ac-*` custom properties (and the numeric column count) via inline style. */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string | number>;

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const pct = (fraction: number): string => `${fraction * 100}%`;

/**
 * A single timed event, absolutely positioned inside its day column from the fractions computed by
 * `@aethercal/calendar-core` (top/height vertically, lane/laneCount horizontally). Pointer-draggable
 * (reusing the same HTML5 drag + drag-machine wiring as the month view's `EventChip`) unless the
 * event is locked. Presentational-only a11y for F2-B: an accessible label but no `button` role and
 * no keyboard action yet (that, plus the resize handle, is F2-D/E — claiming an interactive role
 * without a handler would be an ARIA lie).
 */
function TimeGridBlockView(props: {
  block: TimeGridBlock;
  locale: string;
  onDragStart: (eventId: string) => void;
  onDragEnd: () => void;
}): React.JSX.Element {
  const { block, locale, onDragStart, onDragEnd } = props;
  const { event } = block;
  const editable = event.editable !== false;
  const timeLabel = formatEventTime(event.start, locale);
  const style: StyleWithVars = {
    top: pct(block.topFraction),
    height: pct(block.heightFraction),
    left: pct(block.lane / block.laneCount),
    width: pct(1 / block.laneCount),
    ...(event.color ? { "--ac-tg-event-accent": event.color } : {}),
  };
  return (
    <div
      className={cx("aethercal-tg-event", !editable && "is-locked")}
      draggable={editable}
      data-event-id={event.id}
      data-lane={block.lane}
      data-lane-count={block.laneCount}
      aria-label={`${timeLabel} ${event.title}`}
      title={event.title}
      style={style}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", event.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart(event.id);
      }}
      onDragEnd={onDragEnd}
    >
      <time className="aethercal-tg-event-time">{timeLabel}</time>
      <span className="aethercal-tg-event-title">{event.title}</span>
    </div>
  );
}

/**
 * The shared week/day time-grid engine (AetherCal-06 §5): 7 (or 1) day columns × an hour axis, an
 * all-day rail above the grid, overlap-packed event blocks, drag-to-reschedule, and a "now" line.
 * The day view is literally this component with a single-day `days` array. All date/geometry math
 * comes from `@aethercal/calendar-core` (the RF-23 boundary); this file only renders and wires drag.
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

  const [dragState, dispatch] = React.useReducer(dragReducer, initialDragState);

  const handleDrop = React.useCallback(
    (targetDateOnly: string) => (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      // The drag machine is the source of truth for WHICH event moved; a foreign/synthetic drop
      // (no prior DRAG_START on this calendar) is ignored even if it carries a valid id. Mirrors
      // the month view so both surfaces reschedule identically (day change, time-of-day preserved).
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
  const onDragStart = React.useCallback(
    (eventId: string) => dispatch({ type: "DRAG_START", eventId }),
    [],
  );
  const onDragEnd = React.useCallback(() => dispatch({ type: "DRAG_CANCEL" }), []);

  // Drive the day-column count AND the visible-hours count from geometry so a narrowed window
  // (e.g. business hours 08–18) is only as tall as its hours, not a fixed 24 (Crisol correctness).
  const containerStyle: StyleWithVars = {
    "--ac-tg-cols": grid.columns.length,
    "--ac-tg-hours": grid.config.dayEndHour - grid.config.dayStartHour,
  };

  return (
    <div
      className={cx("aethercal-calendar", "aethercal-timegrid", isDragging(dragState) && "is-dragging")}
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
            onDrop={dropEnabled ? handleDrop(col.dateOnly) : undefined}
          >
            {col.allDay.map((event) => (
              <EventChip
                key={event.id}
                event={event}
                timeLabel={null}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
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
            onDrop={dropEnabled ? handleDrop(col.dateOnly) : undefined}
          >
            {grid.hourMarks.map((mark) => (
              <div
                key={mark.hour}
                className="aethercal-tg-line"
                style={{ top: pct(mark.topFraction) }}
                aria-hidden="true"
              />
            ))}
            {col.timed.map((block) => (
              <TimeGridBlockView
                key={block.event.id}
                block={block}
                locale={locale}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
              />
            ))}
            {nowFraction !== null && col.dateOnly === nowDateKey ? (
              <div className="aethercal-now-indicator" style={{ top: pct(nowFraction) }} aria-hidden="true" />
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
