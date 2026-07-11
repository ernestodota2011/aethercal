/**
 * Headless drag interaction state machine for the calendar (AetherCal-06 §6).
 *
 * F2-A scaffolding: only what the month view needs — track whether a drag is in progress and
 * which event is being dragged, so a view can highlight drop targets and know what to reschedule.
 * The richer machine (resize / range-select / context-menu, and the optimistic commit/rollback)
 * is F2-D and extends this same reducer. Kept as a pure reducer so it is testable with no DOM
 * (fast-check invariants), independent of React.
 */

/** The drag machine is either idle, or dragging a specific event. */
export type DragState =
  | { readonly status: "idle" }
  | { readonly status: "dragging"; readonly eventId: string };

/** Inputs the view feeds the machine. */
export type DragAction =
  | { readonly type: "DRAG_START"; readonly eventId: string }
  | { readonly type: "DRAG_CANCEL" }
  | { readonly type: "DROP" };

export const initialDragState: DragState = { status: "idle" };

/** Type guard: is a drag currently in progress? */
export function isDragging(
  state: DragState,
): state is { status: "dragging"; eventId: string } {
  return state.status === "dragging";
}

/**
 * Pure transition function. Both terminal actions (DROP, DRAG_CANCEL) always return to `idle`;
 * DRAG_START (re)starts a drag on the given event from any state.
 */
export function dragReducer(state: DragState, action: DragAction): DragState {
  switch (action.type) {
    case "DRAG_START":
      return { status: "dragging", eventId: action.eventId };
    case "DROP":
    case "DRAG_CANCEL":
      return initialDragState;
  }
}
