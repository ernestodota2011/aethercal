import {
  type CalendarEvent,
  type CalendarResource,
  type ContextMenuPayload,
  type Edge,
  type EventClickPayload,
  type EventDropPayload,
  type EventResizePayload,
  type RangeSelectPayload,
  type TimeGridConfig,
  type TimelineItem,
  type TimelineRow,
  DEFAULT_SNAP_MINUTES,
  addCalendarDays,
  buildResourceTimeline,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  formatLocalDateTime,
  initialInteractionState,
  interactionReducer,
  parseLocalDateTime,
  timelineNowFraction,
  timelinePointAt,
  toDateOnly,
} from "@aethercal/calendar-core";
import * as React from "react";
import { KeyboardHint, LiveRegion } from "./a11y";
import { type CalendarMessages, resolveMessages } from "./i18n";
import { formatEventTime } from "./labels";
import { ensureCalendarStyles } from "./styles";
import { formatDayColumnHeader } from "./timeGridLabels";
import { ensureTimelineStyles } from "./timelineStyles";
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

/** Allow setting `--ac-*` custom properties (and numeric lane counts) via inline style. */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string | number>;

/** The in-flight pointer gesture, tracked in a ref so window listeners see live data. */
type Gesture =
  | {
      kind: "resize";
      pointerId: number;
      eventId: string;
      edge: Edge;
      trackEl: HTMLElement;
      payload: EventResizePayload | null;
    }
  | {
      kind: "select";
      pointerId: number;
      resourceId: string;
      trackEl: HTMLElement;
      anchorDate: string;
      anchorMinute: number;
      currentDate: string;
      currentMinute: number;
    };

/**
 * In-flight KEYBOARD gesture. A move tracks BOTH a time target and a RESOURCE target — stepping the
 * resource is the keyboard equivalent of dragging a bar onto another row, and is exactly why
 * `GridPoint` grew a `resourceId` (RF-28). `moved` keeps a confirm-without-moving a strict no-op.
 */
type KbGrab =
  | {
      kind: "move";
      eventId: string;
      dateOnly: string;
      minute: number;
      resourceId: string;
      moved: boolean;
    }
  | { kind: "resize"; eventId: string; dateOnly: string; minute: number; moved: boolean };

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const pct = (fraction: number): string => `${fraction * 100}%`;

const EMPTY_IDS: ReadonlySet<string> = new Set();

/** The unassigned row's stable key. Namespaced so it can never collide with a real resource id. */
const UNASSIGNED_KEY = "unassigned";

/** A row's stable key: its resource id (namespaced) or the unassigned sentinel. */
const rowKeyOf = (row: TimelineRow): string =>
  row.resource ? `r:${row.resource.id}` : UNASSIGNED_KEY;

/** Fraction [0, 1] across a track the pointer's `clientX` falls at (0 for an unmeasured track). */
function fractionInTrack(clientX: number, trackEl: HTMLElement): number {
  const rect = trackEl.getBoundingClientRect();
  if (!(rect.width > 0)) return 0;
  return (clientX - rect.left) / rect.width;
}

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
 * The resource timeline (RF-28): resources in ROWS, time on the HORIZONTAL axis, with collapsible
 * groups and drag-between-rows.
 *
 * All geometry — which row an event belongs to, where its bar sits on the axis, how overlapping
 * bookings stack inside a row, which rows a collapsed group hides — comes from
 * `buildResourceTimeline` in `@aethercal/calendar-core` (the RF-23 boundary). This file only turns
 * fractions into percentages and translates input into core calls.
 *
 * The keyboard model IS the axes: up/down moves between resources, left/right between days. Enter
 * toggles a group, or grabs an event — and a grabbed event then moves through TIME with left/right
 * and across RESOURCES with up/down, so a keyboard user can perform the same cross-row drag a mouse
 * user gets. That parity is the whole point of the view.
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

  const [interaction, dispatch] = React.useReducer(interactionReducer, initialInteractionState);
  const gestureRef = React.useRef<Gesture | null>(null);
  const [resizePreview, setResizePreview] = React.useState<EventResizePayload | null>(null);
  const [selectBand, setSelectBand] = React.useState<{
    resourceId: string;
    leftFraction: number;
    widthFraction: number;
  } | null>(null);

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
      setAnnouncement(
        nowCollapsed ? messages.groupCollapsed(groupId) : messages.groupExpanded(groupId),
      );
    },
    [collapsedGroupIds, onToggleGroup, messages],
  );

  // ---- move (HTML5 drag): the drop names BOTH the new time and the target resource row ----------
  const handleDrop = React.useCallback(
    (row: TimelineRow) => (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      if (interaction.status !== "dragging") {
        dispatch({ type: "COMMIT" });
        return;
      }
      const draggedId = interaction.eventId;
      const transferred = e.dataTransfer.getData("text/plain");
      dispatch({ type: "COMMIT" });
      if (transferred && transferred !== draggedId) return;
      if (!onEventDrop || !row.resource) return;
      const dragged = events.find((candidate) => candidate.id === draggedId);
      if (!dragged || dragged.editable === false) return;

      const point = timelinePointAt(
        fractionInTrack(e.clientX, e.currentTarget),
        days,
        timeline.config,
      );
      if (!point) return;
      // An all-day event keeps its "no time-of-day" nature: it moves by DAY (and by resource) only.
      const minute = dragged.allDay === true ? null : point.minuteOfDay;
      onEventDrop(computeMovedRange(dragged, point.dateOnly, minute, row.resource.id));
    },
    [interaction, events, onEventDrop, days, timeline.config],
  );

  const onDragStart = React.useCallback((eventId: string) => {
    if (gestureRef.current?.kind === "resize") return;
    dispatch({ type: "DRAG_START", eventId });
  }, []);
  const onDragEnd = React.useCallback(() => dispatch({ type: "CANCEL" }), []);

  // ---- resize / select (Pointer Events) --------------------------------------------------------
  const startResize = React.useCallback(
    (event: CalendarEvent, edge: Edge) => (e: React.PointerEvent<HTMLDivElement>) => {
      if (!onEventResize || event.editable === false || e.button !== 0) return;
      if (gestureRef.current) return; // a gesture is already in progress — first pointer wins
      const trackEl = (e.currentTarget as HTMLElement).closest<HTMLElement>(".aethercal-tl-track");
      if (!trackEl) return;
      e.preventDefault();
      e.stopPropagation();
      gestureRef.current = {
        kind: "resize",
        pointerId: e.pointerId,
        eventId: event.id,
        edge,
        trackEl,
        payload: null,
      };
      e.currentTarget.setPointerCapture?.(e.pointerId);
      dispatch({ type: "RESIZE_START", eventId: event.id, edge });
    },
    [onEventResize],
  );

  const startSelect = React.useCallback(
    (row: TimelineRow) => (e: React.PointerEvent<HTMLDivElement>) => {
      if (!onRangeSelect || e.button !== 0 || !row.resource) return;
      if (gestureRef.current) return;
      if ((e.target as Element).closest("[data-event-id], button")) return;
      const trackEl = e.currentTarget;
      const point = timelinePointAt(fractionInTrack(e.clientX, trackEl), days, timeline.config);
      if (!point) return;
      const minute = point.minuteOfDay ?? 0;
      gestureRef.current = {
        kind: "select",
        pointerId: e.pointerId,
        resourceId: row.resource.id,
        trackEl,
        anchorDate: point.dateOnly,
        anchorMinute: minute,
        currentDate: point.dateOnly,
        currentMinute: minute,
      };
      trackEl.setPointerCapture?.(e.pointerId);
      dispatch({
        type: "SELECT_START",
        point: { dateOnly: point.dateOnly, minuteOfDay: minute, resourceId: row.resource.id },
      });
    },
    [onRangeSelect, days, timeline.config],
  );

  const gestureActive = interaction.status === "resizing" || interaction.status === "selecting";
  React.useLayoutEffect(() => {
    if (!gestureActive) return;

    const onMove = (e: PointerEvent): void => {
      const g = gestureRef.current;
      if (!g || e.pointerId !== g.pointerId) return;
      const point = timelinePointAt(fractionInTrack(e.clientX, g.trackEl), days, timeline.config);
      if (!point) return;

      if (g.kind === "resize") {
        const event = events.find((candidate) => candidate.id === g.eventId);
        if (!event) return;
        const payload = computeResize(event, g.edge, point.dateOnly, point.minuteOfDay ?? 0);
        g.payload = payload;
        setResizePreview(payload);
        return;
      }

      g.currentDate = point.dateOnly;
      g.currentMinute = point.minuteOfDay ?? 0;
      const from = axisFractionOf(g.anchorDate, g.anchorMinute);
      const to = axisFractionOf(g.currentDate, g.currentMinute);
      setSelectBand({
        resourceId: g.resourceId,
        leftFraction: Math.min(from, to),
        widthFraction: Math.abs(to - from),
      });
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
              { dateOnly: g.anchorDate, minuteOfDay: g.anchorMinute, resourceId: g.resourceId },
              { dateOnly: g.currentDate, minuteOfDay: g.currentMinute, resourceId: g.resourceId },
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
  }, [gestureActive, events, days, timeline.config, axisFractionOf, onEventResize, onRangeSelect]);

  const emptyContextMenu = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onContextMenu) return;
      if ((e.target as Element).closest("[data-event-id], button")) return;
      const point = timelinePointAt(
        fractionInTrack(e.clientX, e.currentTarget),
        days,
        timeline.config,
      );
      if (!point) return;
      e.preventDefault();
      const midnight = parseLocalDateTime(`${point.dateOnly}T00:00:00`);
      const slot = new Date(
        midnight.getFullYear(),
        midnight.getMonth(),
        midnight.getDate(),
        0,
        point.minuteOfDay ?? 0,
        0,
      );
      onContextMenu({ start: formatLocalDateTime(slot) });
    },
    [onContextMenu, days, timeline.config],
  );

  // ---- keyboard access: item cursor -> event mode -> grab (time + resource) ---------------------
  const baseId = React.useId();
  const hintId = `${baseId}-hint`;
  const snap = DEFAULT_SNAP_MINUTES;

  const [activeItem, setActiveItem] = React.useState(0);
  const [activeDayIndex, setActiveDayIndex] = React.useState(0);
  const [activeEventId, setActiveEventId] = React.useState<string | null>(null);
  const [kbGrab, setKbGrab] = React.useState<KbGrab | null>(null);

  const itemDomId = (index: number): string => `${baseId}-i-${index}`;
  const evtDomId = (eventId: string): string => `${baseId}-e-${eventId}`;

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

  const eventInteractive = React.useCallback(
    (event: CalendarEvent): boolean =>
      Boolean(onEventClick) || (event.editable !== false && Boolean(onEventDrop || onEventResize)),
    [onEventClick, onEventDrop, onEventResize],
  );

  // Only ACTIONABLE events are keyboard-navigable — the cursor never lands on a locked, unwired bar.
  const navigableEvents = React.useMemo(
    () => (currentRow?.blocks ?? []).map((b) => b.event).filter((e) => eventInteractive(e)),
    [currentRow, eventInteractive],
  );

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

  const activeDescendantId = kbGrab
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
        setAnnouncement(messages.movedTo(`${resourceTitleOf(resourceId)} · ${where}`));
        setKbGrab({ ...prev, dateOnly, minute, resourceId, moved: true });
      } else {
        const payload = computeResize(event, "end", dateOnly, minute);
        setAnnouncement(
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
      setAnnouncement(
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
      setAnnouncement(
        messages.resized(
          `${formatEventTime(payload.start, locale)} – ${formatEventTime(payload.end, locale)}`,
        ),
      );
      setActiveEventId(prev.eventId);
    } else {
      setActiveEventId(prev.eventId);
    }
    setKbGrab(null);
  }, [kbGrab, events, onEventDrop, onEventResize, resourceTitleOf, messages, locale]);

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
          setAnnouncement(messages.cancelled);
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
            setAnnouncement(messages.grabbedMoveHint(event.title));
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
            setAnnouncement(messages.grabbedResizeHint(event.title));
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
        // On a row with events, Enter descends into them; on an EMPTY row it creates.
        if (navigableEvents.length > 0) {
          e.preventDefault();
          setActiveEventId(navigableEvents[0]!.id);
          return;
        }
        if (
          currentRow?.resource &&
          currentRow.blocks.length === 0 &&
          onRangeSelect &&
          days.length > 0
        ) {
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
            setAnnouncement(
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
      messages,
      locale,
    ],
  );

  const rootStyle: StyleWithVars = { ...(themeVars ?? {}) };

  return (
    <>
      <div
        className={cx(
          "aethercal-calendar",
          "aethercal-timeline",
          interaction.status === "dragging" && "is-dragging",
          interaction.status === "resizing" && "is-resizing",
          interaction.status === "selecting" && "is-selecting",
        )}
        role="grid"
        aria-label={messages.viewNames.timeline}
        aria-describedby={hintId}
        aria-activedescendant={activeDescendantId}
        tabIndex={0}
        data-view="timeline"
        style={rootStyle}
        onKeyDown={handleKeyDown}
      >
        <div className="aethercal-tl-head" role="row">
          <div className="aethercal-tl-corner" role="columnheader">
            {messages.timelineResources}
          </div>
          <div className="aethercal-tl-days">
            {timeline.dayHeaders.map((day) => (
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

        {/* The rows scroll (a timeline can hold many resources), so the body is a focusable scroll
            container — axe `scrollable-region-focusable`. The grid's arrow-key navigation still runs:
            the handler sits on the focusable grid root and catches the bubbled keydown. */}
        <div className="aethercal-tl-body" role="rowgroup" tabIndex={0}>
          {timeline.items.map((item, index) => {
            const isItemActive = !activeEventId && !kbGrab && index === activeItem;

            if (item.kind === "group") {
              const { group } = item;
              return (
                <div
                  key={`g:${group.id}`}
                  role="row"
                  className={cx("aethercal-tl-group", group.collapsed && "is-collapsed")}
                >
                  <div className="aethercal-tl-group-head" role="rowheader">
                    <button
                      type="button"
                      id={itemDomId(index)}
                      className={cx("aethercal-tl-group-toggle", isItemActive && "is-active")}
                      aria-expanded={!group.collapsed}
                      // Not a tabstop: the grid is a SINGLE tabstop and drives this through
                      // aria-activedescendant + Enter, exactly like every other cell.
                      tabIndex={-1}
                      onClick={() => toggleGroup(group.id)}
                    >
                      <span className="aethercal-tl-caret" aria-hidden="true">
                        ▾
                      </span>
                      <span>{group.id}</span>{" "}
                      <span className="aethercal-tl-group-count">
                        {messages.timelineGroupCount(group.resourceCount)}
                      </span>
                    </button>
                  </div>
                </div>
              );
            }

            const { row } = item;
            const isKbTarget = kbGrab?.kind === "move" && row.resource?.id === kbGrab.resourceId;
            const trackStyle: StyleWithVars = { "--ac-tl-lanes": row.laneCount };
            const headStyle: StyleWithVars = row.resource?.color
              ? { "--ac-tl-row-accent": row.resource.color }
              : {};

            return (
              <div
                key={rowKeyOf(row)}
                role="row"
                className={cx("aethercal-tl-row", !row.resource && "is-unassigned")}
              >
                <div
                  id={itemDomId(index)}
                  role="rowheader"
                  className={cx("aethercal-tl-rowhead", isItemActive && "is-active")}
                  style={headStyle}
                >
                  {row.resource?.color ? (
                    <span className="aethercal-tl-swatch" aria-hidden="true" />
                  ) : null}
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
                  onDrop={dropEnabled && row.resource ? handleDrop(row) : undefined}
                  onPointerDown={selectEnabled && row.resource ? startSelect(row) : undefined}
                  onContextMenu={onContextMenu ? emptyContextMenu : undefined}
                >
                  {timeline.ticks.map((tick) => (
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
                      (!kbGrab && activeEventId === event.id && currentRow === row);
                    const timeLabel = block.allDay
                      ? messages.allDay
                      : formatEventTime(previewed?.start ?? event.start, locale);
                    const style: StyleWithVars = {
                      left: pct(block.leftFraction),
                      width: pct(block.widthFraction),
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
                        draggable={editable}
                        data-event-id={event.id}
                        data-lane={block.lane}
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
                        {/* A real whitespace text node between the time and the title, so the bar's
                            visible text matches its accessible name (WCAG 2.5.3) — the visual gap is
                            a flex `gap`, which is not text. */}
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
                    <div
                      className="aethercal-tl-now"
                      style={{ left: pct(nowFraction) }}
                      aria-hidden="true"
                    />
                  ) : null}
                </div>
              </div>
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
