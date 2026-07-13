/**
 * The POINTER half of the resource timeline's interaction (RF-28): HTML5 drag between rows, pointer
 * resize of a bar's left/right edge, and drag-to-create across empty track.
 *
 * Extracted so the view no longer mixes pointer gestures with keyboard gestures, geometry and
 * presentation in one file. Every gesture still resolves its position through the CORE's axis
 * transform (`timelinePointAt`): the axis is compressed, and a local wall-clock derivation is a bug
 * this codebase has already had to kill once.
 */
import {
  type CalendarEvent,
  type ContextMenuPayload,
  type Edge,
  type EventDropPayload,
  type EventResizePayload,
  type InteractionState,
  type RangeSelectPayload,
  type ResolvedTimeGridConfig,
  type TimelineRow,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  formatLocalDateTime,
  initialInteractionState,
  interactionReducer,
  parseLocalDateTime,
  timelinePointAt,
} from "@aethercal/calendar-core";
import * as React from "react";
import { type Gesture, type SelectBand, fractionInTrack } from "./timelineShared";

export interface UseTimelinePointerGesturesOptions {
  days: readonly string[];
  config: ResolvedTimeGridConfig;
  events: readonly CalendarEvent[];
  /** The axis fraction of a (day, minute) instant — the inverse of `timelinePointAt`. */
  axisFractionOf: (dateOnly: string, minute: number) => number;
  onEventDrop?: (payload: EventDropPayload) => void;
  onEventResize?: (payload: EventResizePayload) => void;
  onRangeSelect?: (payload: RangeSelectPayload) => void;
  onContextMenu?: (payload: ContextMenuPayload) => void;
}

export interface TimelinePointerGestures {
  interaction: InteractionState;
  /** The provisional range while a resize drags, so the bar can be drawn where it is going. */
  resizePreview: EventResizePayload | null;
  /** The live band while dragging across empty track to create. */
  selectBand: SelectBand | null;
  handleDrop: (row: TimelineRow) => (e: React.DragEvent<HTMLDivElement>) => void;
  /** Returns false when the drag must NOT start (no drop handler, or a resize is in progress). */
  beginDrag: (eventId: string) => boolean;
  endDrag: () => void;
  startResize: (event: CalendarEvent, edge: Edge) => (e: React.PointerEvent<HTMLDivElement>) => void;
  startSelect: (row: TimelineRow) => (e: React.PointerEvent<HTMLDivElement>) => void;
  emptyContextMenu: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export function useTimelinePointerGestures(
  options: UseTimelinePointerGesturesOptions,
): TimelinePointerGestures {
  const {
    days,
    config,
    events,
    axisFractionOf,
    onEventDrop,
    onEventResize,
    onRangeSelect,
    onContextMenu,
  } = options;

  const [interaction, dispatch] = React.useReducer(interactionReducer, initialInteractionState);
  const gestureRef = React.useRef<Gesture | null>(null);
  const [resizePreview, setResizePreview] = React.useState<EventResizePayload | null>(null);
  const [selectBand, setSelectBand] = React.useState<SelectBand | null>(null);

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

      const point = timelinePointAt(fractionInTrack(e.clientX, e.currentTarget), days, config);
      if (!point) return;
      // An all-day event keeps its "no time-of-day" nature: it moves by DAY (and by resource) only.
      const minute = dragged.allDay === true ? null : point.minuteOfDay;
      onEventDrop(computeMovedRange(dragged, point.dateOnly, minute, row.resource.id));
    },
    [interaction, events, onEventDrop, days, config],
  );

  const beginDrag = React.useCallback(
    (eventId: string) => {
      // No drop handler means a drag can lead nowhere. Refuse the gesture at the source rather than
      // let the user drag a bar around and silently drop it into a void.
      if (!onEventDrop) return false;
      if (gestureRef.current?.kind === "resize") return false;
      dispatch({ type: "DRAG_START", eventId });
      return true;
    },
    [onEventDrop],
  );

  const endDrag = React.useCallback(() => dispatch({ type: "CANCEL" }), []);

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
      const point = timelinePointAt(fractionInTrack(e.clientX, trackEl), days, config);
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
    [onRangeSelect, days, config],
  );

  const gestureActive = interaction.status === "resizing" || interaction.status === "selecting";
  React.useLayoutEffect(() => {
    if (!gestureActive) return;

    const onMove = (e: PointerEvent): void => {
      const g = gestureRef.current;
      if (!g || e.pointerId !== g.pointerId) return;
      const point = timelinePointAt(fractionInTrack(e.clientX, g.trackEl), days, config);
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
  }, [gestureActive, events, days, config, axisFractionOf, onEventResize, onRangeSelect]);

  const emptyContextMenu = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onContextMenu) return;
      if ((e.target as Element).closest("[data-event-id], button")) return;
      const point = timelinePointAt(fractionInTrack(e.clientX, e.currentTarget), days, config);
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
    [onContextMenu, days, config],
  );

  return {
    interaction,
    resizePreview,
    selectBand,
    handleDrop,
    beginDrag,
    endDrag,
    startResize,
    startSelect,
    emptyContextMenu,
  };
}
