import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type DragAction,
  type DragState,
  dragReducer,
  initialDragState,
  isDragging,
} from "./dragMachine";

describe("dragReducer — explicit transitions", () => {
  it("starts idle", () => {
    expect(initialDragState).toEqual({ status: "idle" });
  });

  it("idle + DRAG_START -> dragging(eventId)", () => {
    const next = dragReducer(initialDragState, { type: "DRAG_START", eventId: "evt-1" });
    expect(next).toEqual({ status: "dragging", eventId: "evt-1" });
    expect(isDragging(next)).toBe(true);
  });

  it("dragging + DROP -> idle", () => {
    const dragging = dragReducer(initialDragState, { type: "DRAG_START", eventId: "evt-1" });
    expect(dragReducer(dragging, { type: "DROP" })).toEqual({ status: "idle" });
  });

  it("dragging + DRAG_CANCEL -> idle", () => {
    const dragging = dragReducer(initialDragState, { type: "DRAG_START", eventId: "evt-1" });
    expect(dragReducer(dragging, { type: "DRAG_CANCEL" })).toEqual({ status: "idle" });
  });

  it("DRAG_START while dragging switches to the new event", () => {
    const first = dragReducer(initialDragState, { type: "DRAG_START", eventId: "a" });
    const second = dragReducer(first, { type: "DRAG_START", eventId: "b" });
    expect(second).toEqual({ status: "dragging", eventId: "b" });
  });

  it("DROP / DRAG_CANCEL on idle stay idle (no spurious transition)", () => {
    expect(dragReducer(initialDragState, { type: "DROP" })).toEqual({ status: "idle" });
    expect(dragReducer(initialDragState, { type: "DRAG_CANCEL" })).toEqual({ status: "idle" });
  });
});

describe("dragReducer — invariants over arbitrary action sequences", () => {
  const actionArb: fc.Arbitrary<DragAction> = fc.oneof(
    fc.record({ type: fc.constant<"DRAG_START">("DRAG_START"), eventId: fc.string() }),
    fc.constant<DragAction>({ type: "DROP" }),
    fc.constant<DragAction>({ type: "DRAG_CANCEL" }),
  );

  it("never lands in an unknown status", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        const state = actions.reduce<DragState>(dragReducer, initialDragState);
        expect(["idle", "dragging"]).toContain(state.status);
      }),
    );
  });

  it("a terminal action (DROP or DRAG_CANCEL) always returns to idle", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        const state = actions.reduce<DragState>(dragReducer, initialDragState);
        expect(dragReducer(state, { type: "DROP" })).toEqual({ status: "idle" });
        expect(dragReducer(state, { type: "DRAG_CANCEL" })).toEqual({ status: "idle" });
      }),
    );
  });

  it("while dragging, the tracked eventId equals the last DRAG_START", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        let expected: string | null = null;
        let state: DragState = initialDragState;
        for (const action of actions) {
          state = dragReducer(state, action);
          if (action.type === "DRAG_START") {
            expected = action.eventId;
          } else {
            expected = null;
          }
          if (state.status === "dragging") {
            expect(state.eventId).toBe(expected);
          }
        }
      }),
    );
  });
});
