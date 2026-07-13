/**
 * The presentational parts of the resource timeline (RF-28): the day header, a collapsible group
 * header, and a resource row with its bars.
 *
 * These are DUMB by design. They receive fractions and flags and turn them into percentages and
 * class names; they decide nothing. Every geometric answer — where a bar sits, how tall a row is,
 * what a resize preview looks like — is computed by the core and handed down, so a bug can no longer
 * hide behind a component quietly doing its own axis maths.
 */
import {
  type CalendarEvent,
  type Edge,
  type EventResizePayload,
  type ResolvedTimeGridConfig,
  type TimelineDayHeader,
  type TimelineGroup,
  type TimelineRow,
  type TimelineTick,
  layoutTimelineEvent,
} from "@aethercal/calendar-core";
import type * as React from "react";
import type { CalendarMessages } from "./i18n";
import { formatEventTime } from "./labels";
import { formatDayColumnHeader } from "./timeGridLabels";
import { type KbGrab, type SelectBand, type StyleWithVars, cx, pct } from "./timelineShared";

/** The day axis above the rows: one evenly-sized column header per visible day. */
export function TimelineHeader(props: {
  dayHeaders: readonly TimelineDayHeader[];
  nowDateKey: string;
  locale: string;
  resourcesLabel: string;
}): React.JSX.Element {
  const { dayHeaders, nowDateKey, locale, resourcesLabel } = props;
  return (
    <div className="aethercal-tl-head" role="row">
      <div className="aethercal-tl-corner" role="columnheader">
        {resourcesLabel}
      </div>
      <div className="aethercal-tl-days">
        {dayHeaders.map((day) => (
          <div
            key={day.dateOnly}
            role="columnheader"
            className={cx("aethercal-tl-dayhead", day.dateOnly === nowDateKey && "is-today")}
            data-date={day.dateOnly}
            style={{ left: pct(day.leftFraction), width: pct(day.widthFraction) }}
          >
            <span>{formatDayColumnHeader(day.dateOnly, locale)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A collapsible group header. Its id doubles as its label (see `CalendarResource`). */
export function TimelineGroupRow(props: {
  group: TimelineGroup;
  domId: string;
  isActive: boolean;
  countLabel: string;
  onToggle: () => void;
}): React.JSX.Element {
  const { group, domId, isActive, countLabel, onToggle } = props;
  return (
    <div role="row" className={cx("aethercal-tl-group", group.collapsed && "is-collapsed")}>
      <div className="aethercal-tl-group-head" role="rowheader">
        <button
          type="button"
          id={domId}
          className={cx("aethercal-tl-group-toggle", isActive && "is-active")}
          aria-expanded={!group.collapsed}
          // Not a tabstop: the grid is a SINGLE tabstop and drives this through
          // aria-activedescendant + Enter, exactly like every other cell.
          tabIndex={-1}
          onClick={onToggle}
        >
          <span className="aethercal-tl-caret" aria-hidden="true">
            ▾
          </span>
          <span>{group.id}</span> <span className="aethercal-tl-group-count">{countLabel}</span>
        </button>
      </div>
    </div>
  );
}

export interface TimelineResourceRowProps {
  row: TimelineRow;
  /** The axis days + resolved hour window — needed only to place the live resize preview. */
  days: readonly string[];
  config: ResolvedTimeGridConfig;
  ticks: readonly TimelineTick[];
  /** Where the "now" line falls on the axis, or null when it is not in view. */
  nowFraction: number | null;
  locale: string;
  messages: CalendarMessages;
  rowDomId: string;
  evtDomId: (eventId: string) => string;
  /** The grid's cursor is on this row's header (and not inside an event). */
  isRowActive: boolean;
  /** The grid's cursor is on this row at all — scopes the active event to it. */
  isCurrentRow: boolean;
  activeEventId: string | null;
  kbGrab: KbGrab | null;
  /** This row is the target of an in-flight keyboard MOVE. */
  isKbTarget: boolean;
  selectBand: SelectBand | null;
  resizePreview: EventResizePayload | null;
  pendingIds: ReadonlySet<string>;
  rolledBackIds: ReadonlySet<string>;
  dropEnabled: boolean;
  resizeEnabled: boolean;
  selectEnabled: boolean;
  eventInteractive: (event: CalendarEvent) => boolean;
  onDrop: (e: React.DragEvent<HTMLDivElement>) => void;
  onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
  onTrackContextMenu?: (e: React.MouseEvent<HTMLDivElement>) => void;
  /** Returns false when the drag must not start; the bar then cancels the gesture. */
  beginDrag: (eventId: string) => boolean;
  endDrag: () => void;
  startResize: (event: CalendarEvent, edge: Edge) => (e: React.PointerEvent<HTMLDivElement>) => void;
  onEventClick?: (id: string) => void;
  onEventContextMenu?: (id: string) => void;
}

/** One resource row: its header cell plus the track holding its lane-stacked bars. */
export function TimelineResourceRow(props: TimelineResourceRowProps): React.JSX.Element {
  const {
    row,
    days,
    config,
    ticks,
    nowFraction,
    locale,
    messages,
    rowDomId,
    evtDomId,
    isRowActive,
    isCurrentRow,
    activeEventId,
    kbGrab,
    isKbTarget,
    selectBand,
    resizePreview,
    pendingIds,
    rolledBackIds,
    dropEnabled,
    resizeEnabled,
    selectEnabled,
    eventInteractive,
    onDrop,
    onPointerDown,
    onTrackContextMenu,
    beginDrag,
    endDrag,
    startResize,
    onEventClick,
    onEventContextMenu,
  } = props;

  const trackStyle: StyleWithVars = { "--ac-tl-lanes": row.laneCount };
  const headStyle: StyleWithVars = row.resource?.color
    ? { "--ac-tl-row-accent": row.resource.color }
    : {};

  return (
    <div role="row" className={cx("aethercal-tl-row", !row.resource && "is-unassigned")}>
      <div
        id={rowDomId}
        role="rowheader"
        className={cx("aethercal-tl-rowhead", isRowActive && "is-active")}
        style={headStyle}
      >
        {row.resource?.color ? <span className="aethercal-tl-swatch" aria-hidden="true" /> : null}
        <span className="aethercal-tl-rowhead-title">
          {row.resource ? row.resource.title : messages.timelineUnassigned}
        </span>
      </div>

      <div
        role="gridcell"
        className={cx("aethercal-tl-track", isKbTarget && "is-drop-target")}
        data-resource-id={row.resource?.id ?? ""}
        style={trackStyle}
        onDragOver={dropEnabled && row.resource ? (e) => e.preventDefault() : undefined}
        onDrop={dropEnabled && row.resource ? onDrop : undefined}
        onPointerDown={selectEnabled && row.resource ? onPointerDown : undefined}
        onContextMenu={onTrackContextMenu}
      >
        {ticks.map((tick) => (
          <div
            key={`${tick.dateOnly}-${tick.hour}`}
            className={cx("aethercal-tl-line", tick.isDayStart && "is-day-start")}
            style={{ left: pct(tick.leftFraction) }}
            aria-hidden="true"
          />
        ))}

        {selectBand && selectBand.resourceId === row.resource?.id ? (
          <div
            className="aethercal-tl-select-band"
            style={{
              left: pct(selectBand.leftFraction),
              width: pct(selectBand.widthFraction),
            }}
            aria-hidden="true"
          />
        ) : null}

        {row.blocks.map((block) => {
          const { event } = block;
          const editable = event.editable !== false;
          const previewed = resizePreview?.id === event.id ? resizePreview : null;
          const isEvtActive =
            kbGrab?.eventId === event.id ||
            (!kbGrab && activeEventId === event.id && isCurrentRow);
          const timeLabel = block.allDay
            ? messages.allDay
            : formatEventTime(previewed?.start ?? event.start, locale);

          // While a resize is in flight the bar must actually MOVE — otherwise the user drags an edge
          // and sees nothing change. The provisional fractions come from the CORE's axis transform,
          // never from a local wall-clock derivation: the axis is compressed, and re-deriving it here
          // is exactly the bug `packLanesBy` was introduced to kill.
          const previewBlock = previewed
            ? layoutTimelineEvent(
                { ...event, start: previewed.start, end: previewed.end },
                days,
                config,
              )[0]
            : undefined;
          const style: StyleWithVars = {
            left: pct(previewBlock?.leftFraction ?? block.leftFraction),
            width: pct(previewBlock?.widthFraction ?? block.widthFraction),
            top: pct(block.lane / block.laneCount),
            height: pct(1 / block.laneCount),
            ...(event.color ? { "--ac-tl-event-accent": event.color } : {}),
          };

          return (
            <div
              key={event.id}
              id={evtDomId(event.id)}
              className={cx(
                "aethercal-tl-event",
                block.allDay && "is-allday",
                !editable && "is-locked",
                block.continuesBefore && "continues-before",
                block.continuesAfter && "continues-after",
                pendingIds.has(event.id) && "is-pending",
                rolledBackIds.has(event.id) && "is-rolledback",
                Boolean(previewed) && "is-resizing",
                isEvtActive && "is-active",
                kbGrab?.eventId === event.id && "is-grabbed",
              )}
              {...(eventInteractive(event) ? { role: "button" } : {})}
              // Draggable ONLY when a drop can actually land somewhere: an editable event whose host
              // wired `onEventDrop`. Advertising a drag the component will silently swallow is a
              // promise the UI cannot keep.
              draggable={editable && dropEnabled}
              data-event-id={event.id}
              data-lane={block.lane}
              aria-label={`${timeLabel} ${event.title}`}
              title={event.title}
              style={style}
              onDragStart={(e) => {
                // Belt and braces: a plain-JS consumer (or a stray `draggable` in the DOM) must not
                // be able to start a gesture that has nowhere to go.
                if (!beginDrag(event.id)) {
                  e.preventDefault();
                  return;
                }
                e.dataTransfer.setData("text/plain", event.id);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragEnd={endDrag}
              onClick={onEventClick ? () => onEventClick(event.id) : undefined}
              onContextMenu={
                onEventContextMenu
                  ? (e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onEventContextMenu(event.id);
                    }
                  : undefined
              }
            >
              {/* A real whitespace text node between the time and the title, so the bar's visible
                  text matches its accessible name (WCAG 2.5.3) — the visual gap is a flex `gap`,
                  which is not text. */}
              <time className="aethercal-tl-event-time">{timeLabel}</time>{" "}
              <span className="aethercal-tl-event-title">{event.title}</span>
              {resizeEnabled && editable && !block.allDay ? (
                <>
                  <div
                    className="aethercal-tl-resize-handle aethercal-tl-resize-handle-start"
                    data-edge="start"
                    aria-hidden="true"
                    draggable={false}
                    onPointerDown={startResize(event, "start")}
                  />
                  <div
                    className="aethercal-tl-resize-handle aethercal-tl-resize-handle-end"
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

        {nowFraction !== null ? (
          <div className="aethercal-tl-now" style={{ left: pct(nowFraction) }} aria-hidden="true" />
        ) : null}
      </div>
    </div>
  );
}
