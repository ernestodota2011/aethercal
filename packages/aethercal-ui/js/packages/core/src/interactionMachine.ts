/**
 * Headless interaction state machine for the calendar (AetherCal-06 §6).
 *
 * The complete gesture machine the time grid drives: `idle → dragging | resizing | selecting →
 * commit | cancel`. It is the F2-D superset of the F2-A `dragMachine` (which the month view still
 * uses for its move-only interaction) — it adds the `resizing` and `selecting` gestures the time
 * grid needs. Kept a pure reducer with no DOM so it is exhaustively testable as invariants with
 * fast-check, independent of React; the geometry that turns a gesture into new event times lives in
 * `interactions.ts`, and the optimistic commit/rollback lives in `reconcile.ts`.
 *
 * Design contract: exactly one gesture is active at a time. Starting a gesture (`*_START`)
 * supersedes any in-flight one from any state; both terminal actions (`COMMIT`, `CANCEL`) always
 * return to `idle`. `SELECT_MOVE` is only meaningful while selecting (it slides the far edge of the
 * range) and is a strict no-op — same state reference back — in any other state.
 */
import type { Edge, GridPoint } from "./types";

/** Exactly one gesture is active at a time (or none). */
export type InteractionState =
  | { readonly status: "idle" }
  | { readonly status: "dragging"; readonly eventId: string }
  | { readonly status: "resizing"; readonly eventId: string; readonly edge: Edge }
  | { readonly status: "selecting"; readonly anchor: GridPoint; readonly current: GridPoint };

/** Inputs a view feeds the machine (pointer/keyboard translated to intent by the React layer). */
export type InteractionAction =
  | { readonly type: "DRAG_START"; readonly eventId: string }
  | { readonly type: "RESIZE_START"; readonly eventId: string; readonly edge: Edge }
  | { readonly type: "SELECT_START"; readonly point: GridPoint }
  | { readonly type: "SELECT_MOVE"; readonly point: GridPoint }
  | { readonly type: "COMMIT" }
  | { readonly type: "CANCEL" };

export const initialInteractionState: InteractionState = { status: "idle" };

/** No gesture in progress. */
export function isIdle(state: InteractionState): state is { status: "idle" } {
  return state.status === "idle";
}

/** A move (drag) gesture is in progress. */
export function isDragging(
  state: InteractionState,
): state is { status: "dragging"; eventId: string } {
  return state.status === "dragging";
}

/** A resize (duration) gesture is in progress. */
export function isResizing(
  state: InteractionState,
): state is { status: "resizing"; eventId: string; edge: Edge } {
  return state.status === "resizing";
}

/** A range-select (create) gesture is in progress. */
export function isSelecting(
  state: InteractionState,
): state is { status: "selecting"; anchor: GridPoint; current: GridPoint } {
  return state.status === "selecting";
}

/** The event being dragged or resized, or `null` when idle / selecting (no single event). */
export function activeEventId(state: InteractionState): string | null {
  return state.status === "dragging" || state.status === "resizing" ? state.eventId : null;
}

/**
 * Pure transition function. `*_START` actions (re)start their gesture from any state; `COMMIT` and
 * `CANCEL` always return to `idle`; `SELECT_MOVE` slides the range's far edge only while selecting
 * and is otherwise a no-op that returns the identical state reference (so a view can dispatch it
 * on every pointer move without spurious re-renders).
 */
export function interactionReducer(
  state: InteractionState,
  action: InteractionAction,
): InteractionState {
  switch (action.type) {
    case "DRAG_START":
      return { status: "dragging", eventId: action.eventId };
    case "RESIZE_START":
      return { status: "resizing", eventId: action.eventId, edge: action.edge };
    case "SELECT_START":
      return { status: "selecting", anchor: action.point, current: action.point };
    case "SELECT_MOVE":
      if (state.status !== "selecting") return state;
      return { status: "selecting", anchor: state.anchor, current: action.point };
    case "COMMIT":
    case "CANCEL":
      return initialInteractionState;
  }
}
