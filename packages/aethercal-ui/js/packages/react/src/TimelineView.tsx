import {
  type CalendarEvent,
  type CalendarResource,
  type ContextMenuPayload,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type RangeSelectPayload,
  type TimeGridConfig,
  buildResourceTimeline,
  formatLocalDateTime,
  timelineNowFraction,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { KeyboardHint, LiveRegion } from "./a11y";
import { type CalendarMessages, resolveMessages } from "./i18n";
import { ensureCalendarStyles } from "./styles";
import { TimelineGroupRow, TimelineHeader, TimelineResourceRow } from "./TimelineParts";
import { EMPTY_IDS, type StyleWithVars, cx, rowKeyOf } from "./timelineShared";
import { ensureTimelineStyles } from "./timelineStyles";
import { useTimelineKeyboard } from "./useTimelineKeyboard";
import { useTimelinePointerGestures } from "./useTimelinePointerGestures";
import type { ThemeTokens } from "./theme";

export interface TimelineViewProps {
  /** The day-only strings the horizontal axis spans, in display order. */
  days: readonly string[];
  /** The rows. Generic: AetherCal maps a resource to a host, but any array works. */
  resources: readonly CalendarResource[];
  events: readonly CalendarEvent[];
  locale: string;
  /** Visible hour window (default 0..24), shared with the week/day grid. */
  config: TimeGridConfig;
  /** Current wall-clock time, for the "now" line and the today highlight (injectable). */
  now: Date;
  messages?: CalendarMessages;
  /** Inline `--ac-*` theme overrides applied to the root (a preset or a custom object). */
  themeVars?: ThemeTokens;
  /** Groups collapsed on first render. Collapse is then the view's own state (uncontrolled). */
  defaultCollapsedGroupIds?: readonly string[];
  /** Notified whenever a group is expanded or collapsed, for a host that wants to persist it. */
  onToggleGroup?: (groupId: string, collapsed: boolean) => void;
  /** Drop an event onto a day/time AND a resource row — the payload names the target resource. */
  onEventDrop?: (payload: EventDropPayload) => void;
  /** Drag a bar's left/right edge to change its duration (the axis is horizontal here). */
  onEventResize?: (payload: EventResizePayload) => void;
  /** Drag across an empty track to create — the payload names the row it was drawn on. */
  onRangeSelect?: (payload: RangeSelectPayload) => void;
  onEventClick?: (payload: EventClickPayload) => void;
  onContextMenu?: (payload: ContextMenuPayload) => void;
  pendingIds?: ReadonlySet<string>;
  rolledBackIds?: ReadonlySet<string>;
}

/**
 * The resource timeline (RF-28): resources in ROWS, time on the HORIZONTAL axis, with collapsible
 * groups and drag-between-rows.
 *
 * This file is the COMPOSITION and nothing else: it resolves the geometry from the core, owns the
 * state the parts share (collapse, announcements), and wires the public callbacks. The gestures live
 * in `useTimelinePointerGestures` / `useTimelineKeyboard`, and the markup in `TimelineParts`. The
 * split is deliberate — every review round found its bug in the seam between those concerns while
 * they were tangled in one file.
 *
 * All geometry — which row an event belongs to, where its bar sits, how overlapping bookings stack,
 * which rows a collapsed group hides — comes from `buildResourceTimeline` (the RF-23 boundary).
 * Nothing here re-derives the axis.
 */
export function TimelineView(props: TimelineViewProps): React.JSX.Element {
  const {
    days,
    resources,
    events,
    locale,
    config,
    now,
    themeVars,
    defaultCollapsedGroupIds,
    onToggleGroup,
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onEventClick,
    onContextMenu,
    pendingIds = EMPTY_IDS,
    rolledBackIds = EMPTY_IDS,
  } = props;

  const messages = React.useMemo<CalendarMessages>(
    () => props.messages ?? resolveMessages(locale),
    [props.messages, locale],
  );

  React.useEffect(() => {
    ensureCalendarStyles();
    ensureTimelineStyles();
  }, []);

  const [announcement, setAnnouncement] = React.useState("");
  const announce = React.useCallback((message: string) => setAnnouncement(message), []);

  // Collapse is the view's OWN state: a consumer gets working groups with no wiring, and one that
  // cares is still told about every toggle through `onToggleGroup`.
  const [collapsedGroupIds, setCollapsedGroupIds] = React.useState<ReadonlySet<string>>(
    () => new Set(defaultCollapsedGroupIds ?? []),
  );
  const collapsedList = React.useMemo(() => [...collapsedGroupIds], [collapsedGroupIds]);

  const timeline = React.useMemo(
    () =>
      buildResourceTimeline(resources, events, days, {
        ...config,
        collapsedGroupIds: collapsedList,
      }),
    [resources, events, days, config, collapsedList],
  );

  const rows = React.useMemo(
    () => timeline.items.flatMap((item) => (item.kind === "row" ? [item.row] : [])),
    [timeline.items],
  );
  // Only a REAL resource row is a drop target. "Unassign an event by dropping it in the orphan row"
  // is not a gesture this view claims to support, so it must not pretend to accept one.
  const dropRows = React.useMemo(() => rows.filter((row) => row.resource !== null), [rows]);

  const nowFraction = React.useMemo(
    () => timelineNowFraction(now, days, timeline.config),
    [now, days, timeline.config],
  );
  const nowDateKey = React.useMemo(() => toDateOnly(formatLocalDateTime(now)), [now]);

  const dropEnabled = Boolean(onEventDrop);
  const resizeEnabled = Boolean(onEventResize);
  const selectEnabled = Boolean(onRangeSelect);

  /** The axis fraction of a (day, minute) instant — the inverse of `timelinePointAt`. */
  const axisFractionOf = React.useCallback(
    (dateOnly: string, minute: number): number => {
      const { windowMinutes, dayStartHour } = timeline.config;
      const total = days.length * windowMinutes;
      if (total <= 0) return 0;
      const index = days.indexOf(dateOnly);
      const base = index === -1 ? 0 : index;
      return (base * windowMinutes + (minute - dayStartHour * 60)) / total;
    },
    [days, timeline.config],
  );

  const toggleGroup = React.useCallback(
    (groupId: string) => {
      const nowCollapsed = !collapsedGroupIds.has(groupId);
      setCollapsedGroupIds((prev) => {
        const next = new Set(prev);
        if (next.has(groupId)) next.delete(groupId);
        else next.add(groupId);
        return next;
      });
      onToggleGroup?.(groupId, nowCollapsed);
      announce(nowCollapsed ? messages.groupCollapsed(groupId) : messages.groupExpanded(groupId));
    },
    [collapsedGroupIds, onToggleGroup, announce, messages],
  );

  const eventInteractive = React.useCallback(
    (event: CalendarEvent): boolean =>
      Boolean(onEventClick) || (event.editable !== false && Boolean(onEventDrop || onEventResize)),
    [onEventClick, onEventDrop, onEventResize],
  );

  const baseId = React.useId();
  const hintId = `${baseId}-hint`;
  const itemDomId = React.useCallback((index: number) => `${baseId}-i-${index}`, [baseId]);
  const evtDomId = React.useCallback((eventId: string) => `${baseId}-e-${eventId}`, [baseId]);

  const pointer = useTimelinePointerGestures({
    days,
    config: timeline.config,
    events,
    axisFractionOf,
    ...(onEventDrop ? { onEventDrop } : {}),
    ...(onEventResize ? { onEventResize } : {}),
    ...(onRangeSelect ? { onRangeSelect } : {}),
    ...(onContextMenu ? { onContextMenu } : {}),
  });

  const keyboard = useTimelineKeyboard({
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
    ...(onEventDrop ? { onEventDrop } : {}),
    ...(onEventResize ? { onEventResize } : {}),
    ...(onRangeSelect ? { onRangeSelect } : {}),
    ...(onEventClick ? { onEventClick } : {}),
  });

  const { interaction } = pointer;
  const { activeItem, activeEventId, kbGrab, currentRow, activeDescendantId } = keyboard;
  const rootStyle: StyleWithVars = { ...(themeVars ?? {}) };

  return (
    <>
      {/* The outer element is a plain styling wrapper — deliberately NOT the grid, and NOT focusable.
          `role="grid"` promises a SINGLE tab stop navigated by arrow keys + aria-activedescendant, so
          the component must expose exactly ONE focusable node. The rows scroll (a timeline can hold
          many resources), and a scrollable region has to be reachable by keyboard (axe
          `scrollable-region-focusable`) — so the scroll container IS the grid, rather than adding a
          second tab stop beside it. The header row lives inside it (sticky), which also keeps the
          columnheaders where ARIA requires them: inside the grid they head. */}
      <div
        className={cx(
          "aethercal-calendar",
          "aethercal-timeline",
          interaction.status === "dragging" && "is-dragging",
          interaction.status === "resizing" && "is-resizing",
          interaction.status === "selecting" && "is-selecting",
        )}
        data-view="timeline"
        style={rootStyle}
      >
        <div
          className="aethercal-tl-body"
          role="grid"
          aria-label={messages.viewNames.timeline}
          aria-describedby={hintId}
          {...(activeDescendantId !== undefined
            ? { "aria-activedescendant": activeDescendantId }
            : {})}
          tabIndex={0}
          onKeyDown={keyboard.handleKeyDown}
        >
          <TimelineHeader
            dayHeaders={timeline.dayHeaders}
            nowDateKey={nowDateKey}
            locale={locale}
            resourcesLabel={messages.timelineResources}
          />

          {/* Nothing to show: no resources, and no unassigned events either. Say so in a REAL row +
              gridcell (so the grid pattern stays coherent) instead of rendering an empty body that a
              screen reader would read as a grid with no content. `aria-activedescendant` is omitted
              while this is up — there is genuinely no cell to be active. */}
          {timeline.items.length === 0 ? (
            <div className="aethercal-tl-row aethercal-tl-row-empty" role="row">
              <div role="gridcell" className="aethercal-tl-empty">
                {messages.timelineEmpty}
              </div>
            </div>
          ) : null}

          {timeline.items.map((item, index) => {
            const isItemActive = !activeEventId && !kbGrab && index === activeItem;

            if (item.kind === "group") {
              return (
                <TimelineGroupRow
                  key={`g:${item.group.id}`}
                  group={item.group}
                  domId={itemDomId(index)}
                  isActive={isItemActive}
                  countLabel={messages.timelineGroupCount(item.group.resourceCount)}
                  onToggle={() => toggleGroup(item.group.id)}
                />
              );
            }

            const { row } = item;
            return (
              <TimelineResourceRow
                key={rowKeyOf(row)}
                row={row}
                days={days}
                config={timeline.config}
                ticks={timeline.ticks}
                nowFraction={nowFraction}
                locale={locale}
                messages={messages}
                rowDomId={itemDomId(index)}
                evtDomId={evtDomId}
                isRowActive={isItemActive}
                isCurrentRow={currentRow === row}
                activeEventId={activeEventId}
                kbGrab={kbGrab}
                isKbTarget={kbGrab?.kind === "move" && row.resource?.id === kbGrab.resourceId}
                selectBand={pointer.selectBand}
                resizePreview={pointer.resizePreview}
                pendingIds={pendingIds}
                rolledBackIds={rolledBackIds}
                dropEnabled={dropEnabled}
                resizeEnabled={resizeEnabled}
                selectEnabled={selectEnabled}
                eventInteractive={eventInteractive}
                onDrop={pointer.handleDrop(row)}
                onPointerDown={pointer.startSelect(row)}
                {...(onContextMenu ? { onTrackContextMenu: pointer.emptyContextMenu } : {})}
                beginDrag={pointer.beginDrag}
                endDrag={pointer.endDrag}
                startResize={pointer.startResize}
                {...(onEventClick ? { onEventClick: (id: string) => onEventClick({ id }) } : {})}
                {...(onContextMenu
                  ? { onEventContextMenu: (id: string) => onContextMenu({ id }) }
                  : {})}
              />
            );
          })}
        </div>
      </div>
      <KeyboardHint id={hintId} text={messages.timelineKeyboardHint} />
      <LiveRegion message={announcement} />
    </>
  );
}

export default TimelineView;
