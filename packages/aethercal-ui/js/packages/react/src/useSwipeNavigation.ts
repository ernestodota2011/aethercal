/**
 * Touch-only horizontal swipe-to-navigate (U-02): recognizes a fast, mostly-horizontal drag as
 * "go to the previous/next period" — the same period step the built-in nav toolbar's prev/next
 * buttons already emit (F2-NAV). ADDITIVE only: it never replaces the toolbar or the keyboard, and
 * it never engages for anything other than a real touchscreen (`pointerType === "touch"`), so
 * mouse/pen users and every existing pointer-driven test keep their exact current behavior.
 *
 * ## Why this doesn't fight the grid's own gestures
 *
 * The time-grid and timeline views bind `onPointerDown` on the SAME surface to start their own
 * drag-to-create-range / resize gestures (`TimeGridView.startSelect`,
 * `useTimelinePointerGestures.startSelect`/`startResize`) — on a touchscreen, those already claim
 * every pointer that lands on an empty grid cell or a resize handle. Rather than reach into that
 * private, per-view state to cancel it, this hook fires a REAL `pointercancel` on `window` for the
 * SAME pointerId the instant it recognizes a swipe. That is the exact platform signal those views
 * already handle cleanly (see TimeGridView's "aborts a selection cleanly on pointercancel (e.g.
 * native touch scroll)" test) — so the nested gesture aborts with no stuck state and, critically,
 * never fires `onRangeSelect`/`onEventResize` for a touch the user meant as a page-turn. A vertical
 * or slow drag never crosses the swipe thresholds below, so it is left completely alone and the
 * existing gesture runs exactly as before.
 */
import * as React from "react";

export type SwipeDirection = "prev" | "next";

/** Minimum horizontal travel (px) before a touch drag is even considered a swipe candidate. */
const SWIPE_MIN_DISTANCE_PX = 48;
/** How much vertical drift is tolerated, as a fraction of the horizontal distance, and still reads
 * as "mostly horizontal". Above this the gesture is left to whatever grid interaction owns it
 * (a vertical drag is a resize/select, in this codebase's own UI language, not a page-turn). */
const SWIPE_MAX_OFF_AXIS_RATIO = 0.5;
/** A drag slower than this (ms since pointerdown) is not a flick — leave it to the nested gesture. */
const SWIPE_MAX_DURATION_MS = 600;
/** How long the transient `is-swiping-*` feedback class stays applied after a recognized swipe. */
const SWIPE_FEEDBACK_MS = 150;

interface TrackedTouch {
  pointerId: number;
  startX: number;
  startY: number;
  startTime: number;
  /** Once true, this pointer's move events are ignored for the rest of the gesture (either a swipe
   * already fired, or the gesture was ruled out as too slow/too vertical). */
  settled: boolean;
}

export interface UseSwipeNavigationOptions {
  /** Whether swipe recognition is armed at all — false is a true no-op (no tracking, no work). */
  enabled: boolean;
  /** Fired once per recognized swipe gesture. */
  onSwipe: (direction: SwipeDirection) => void;
}

export interface SwipeNavigationHandlers {
  onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerCancel: (e: React.PointerEvent<HTMLDivElement>) => void;
}

export interface UseSwipeNavigationResult {
  /** Spread onto the swipeable surface. */
  handlers: SwipeNavigationHandlers;
  /** The direction just recognized, for a brief visual cue; clears itself after ~150ms. */
  swipeDirection: SwipeDirection | null;
}

/** Abort any nested pointer gesture (time-grid select, timeline drag/resize/select) tracking this
 * SAME pointerId, by dispatching the platform's own cancellation signal. */
function cancelNestedGesture(pointerId: number): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new PointerEvent("pointercancel", { pointerId, bubbles: true, cancelable: true }));
}

export function useSwipeNavigation(options: UseSwipeNavigationOptions): UseSwipeNavigationResult {
  const { enabled, onSwipe } = options;
  const trackedRef = React.useRef<TrackedTouch | null>(null);
  const [swipeDirection, setSwipeDirection] = React.useState<SwipeDirection | null>(null);
  const feedbackTimer = React.useRef<number | undefined>(undefined);

  // Refs so the callbacks below stay referentially stable (no re-subscription churn) while always
  // reading the LATEST `enabled`/`onSwipe` — mirrors the ref-capture pattern the grid's own pointer
  // gesture hooks use for the same reason.
  const enabledRef = React.useRef(enabled);
  enabledRef.current = enabled;
  const onSwipeRef = React.useRef(onSwipe);
  onSwipeRef.current = onSwipe;

  React.useEffect(
    () => () => {
      if (feedbackTimer.current !== undefined) window.clearTimeout(feedbackTimer.current);
    },
    [],
  );

  const onPointerDown = React.useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!enabledRef.current || e.pointerType !== "touch") return;
    // First finger wins — a second touch mid-gesture (e.g. pinch) is not a swipe candidate.
    if (trackedRef.current) return;
    trackedRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startTime: e.timeStamp,
      settled: false,
    };
  }, []);

  const onPointerMove = React.useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const tracked = trackedRef.current;
    if (!tracked || tracked.settled || e.pointerId !== tracked.pointerId) return;

    const dx = e.clientX - tracked.startX;
    const dy = e.clientY - tracked.startY;
    const elapsed = e.timeStamp - tracked.startTime;

    if (elapsed > SWIPE_MAX_DURATION_MS) {
      tracked.settled = true; // too slow to be a flick — leave the nested gesture alone
      return;
    }
    if (Math.abs(dx) < SWIPE_MIN_DISTANCE_PX) return; // not far enough yet — keep watching
    if (Math.abs(dy) > Math.abs(dx) * SWIPE_MAX_OFF_AXIS_RATIO) {
      tracked.settled = true; // too vertical — this is a resize/select drag, not a page-turn
      return;
    }

    tracked.settled = true;
    const direction: SwipeDirection = dx < 0 ? "next" : "prev";
    cancelNestedGesture(tracked.pointerId);
    onSwipeRef.current(direction);

    setSwipeDirection(direction);
    if (feedbackTimer.current !== undefined) window.clearTimeout(feedbackTimer.current);
    feedbackTimer.current = window.setTimeout(() => setSwipeDirection(null), SWIPE_FEEDBACK_MS);
  }, []);

  /** Shared by the element-bound handler below AND the window backstop — both only need the
   * pointerId, so this takes the narrowest shape either kind of event actually offers. */
  const clearIfTracked = React.useCallback((e: { pointerId: number }) => {
    if (trackedRef.current?.pointerId === e.pointerId) trackedRef.current = null;
  }, []);

  const endTracking = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => clearIfTracked(e),
    [clearIfTracked],
  );

  // Window backstop (H1, hardening pass): `onPointerUp`/`onPointerCancel` above are bound only to
  // the swipeable element via React's synthetic props, so they fire only if THAT element is still
  // under the pointer when the gesture ends. A drag that wanders off the element's bounds before
  // lifting — finger exits the swipe viewport's edge, or a scrollable ancestor carries it past —
  // ends on whatever DOM node happens to be there instead, and `trackedRef` is never cleared. The
  // next real touch then finds a stale `trackedRef.current` and `onPointerDown`'s "first finger
  // wins" guard silently refuses to track it — swipe stays broken until the component remounts.
  // Native `pointerup`/`pointercancel` still bubble all the way to `window` regardless of which
  // element they end on (nothing here calls `stopPropagation`), so a window-level listener is a
  // reliable backstop: it clears `trackedRef` for ITS pointerId no matter where the gesture ended,
  // while the element-bound handlers above keep firing first (same pointerId, idempotent clear) for
  // the common case where the gesture never left the element.
  React.useEffect(() => {
    const onWindowPointerEnd = (e: PointerEvent): void => clearIfTracked(e);
    window.addEventListener("pointerup", onWindowPointerEnd);
    window.addEventListener("pointercancel", onWindowPointerEnd);
    return () => {
      window.removeEventListener("pointerup", onWindowPointerEnd);
      window.removeEventListener("pointercancel", onWindowPointerEnd);
    };
  }, [clearIfTracked]);

  return {
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endTracking,
      onPointerCancel: endTracking,
    },
    swipeDirection,
  };
}
