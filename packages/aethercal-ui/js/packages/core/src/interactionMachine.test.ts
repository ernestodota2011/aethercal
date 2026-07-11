import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type InteractionAction,
  type InteractionState,
  activeEventId,
  initialInteractionState,
  interactionReducer,
  isDragging,
  isIdle,
  isResizing,
  isSelecting,
} from "./interactionMachine";
import type { GridPoint } from "./types";

const pointA: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: 540 };
const pointB: GridPoint = { dateOnly: "2026-07-15", minuteOfDay: 720 };

describe("interactionReducer — explicit transitions", () => {
  it("starts idle", () => {
    expect(initialInteractionState).toEqual({ status: "idle" });
    expect(isIdle(initialInteractionState)).toBe(true);
  });

  it("idle + DRAG_START -> dragging(eventId)", () => {
    const next = interactionReducer(initialInteractionState, { type: "DRAG_START", eventId: "e1" });
    expect(next).toEqual({ status: "dragging", eventId: "e1" });
    expect(isDragging(next)).toBe(true);
    expect(activeEventId(next)).toBe("e1");
  });

  it("idle + RESIZE_START -> resizing(eventId, edge)", () => {
    const next = interactionReducer(initialInteractionState, {
      type: "RESIZE_START",
      eventId: "e1",
      edge: "end",
    });
    expect(next).toEqual({ status: "resizing", eventId: "e1", edge: "end" });
    expect(isResizing(next)).toBe(true);
    expect(activeEventId(next)).toBe("e1");
  });

  it("idle + SELECT_START -> selecting(anchor=current=point)", () => {
    const next = interactionReducer(initialInteractionState, { type: "SELECT_START", point: pointA });
    expect(next).toEqual({ status: "selecting", anchor: pointA, current: pointA });
    expect(isSelecting(next)).toBe(true);
    expect(activeEventId(next)).toBeNull();
  });

  it("selecting + SELECT_MOVE updates current but never the anchor", () => {
    const started = interactionReducer(initialInteractionState, { type: "SELECT_START", point: pointA });
    const moved = interactionReducer(started, { type: "SELECT_MOVE", point: pointB });
    expect(moved).toEqual({ status: "selecting", anchor: pointA, current: pointB });
  });

  it("SELECT_MOVE outside selecting is a no-op (same reference back)", () => {
    const dragging = interactionReducer(initialInteractionState, { type: "DRAG_START", eventId: "e1" });
    expect(interactionReducer(dragging, { type: "SELECT_MOVE", point: pointB })).toBe(dragging);
    expect(interactionReducer(initialInteractionState, { type: "SELECT_MOVE", point: pointB })).toBe(
      initialInteractionState,
    );
  });

  it("any active gesture + COMMIT -> idle", () => {
    for (const start of [
      { type: "DRAG_START", eventId: "e1" } as const,
      { type: "RESIZE_START", eventId: "e1", edge: "start" } as const,
      { type: "SELECT_START", point: pointA } as const,
    ]) {
      const active = interactionReducer(initialInteractionState, start);
      expect(interactionReducer(active, { type: "COMMIT" })).toEqual({ status: "idle" });
    }
  });

  it("any active gesture + CANCEL -> idle", () => {
    for (const start of [
      { type: "DRAG_START", eventId: "e1" } as const,
      { type: "RESIZE_START", eventId: "e1", edge: "end" } as const,
      { type: "SELECT_START", point: pointA } as const,
    ]) {
      const active = interactionReducer(initialInteractionState, start);
      expect(interactionReducer(active, { type: "CANCEL" })).toEqual({ status: "idle" });
    }
  });

  it("a new gesture supersedes the current one from any state", () => {
    const dragging = interactionReducer(initialInteractionState, { type: "DRAG_START", eventId: "a" });
    const resizing = interactionReducer(dragging, { type: "RESIZE_START", eventId: "b", edge: "end" });
    expect(resizing).toEqual({ status: "resizing", eventId: "b", edge: "end" });
    const selecting = interactionReducer(resizing, { type: "SELECT_START", point: pointA });
    expect(selecting).toEqual({ status: "selecting", anchor: pointA, current: pointA });
  });

  it("COMMIT / CANCEL on idle stay idle (no spurious transition)", () => {
    expect(interactionReducer(initialInteractionState, { type: "COMMIT" })).toEqual({ status: "idle" });
    expect(interactionReducer(initialInteractionState, { type: "CANCEL" })).toEqual({ status: "idle" });
  });
});

describe("interactionReducer — invariants over arbitrary action sequences", () => {
  const pointArb: fc.Arbitrary<GridPoint> = fc.record({
    dateOnly: fc.constantFrom("2026-07-15", "2026-07-16"),
    minuteOfDay: fc.oneof(fc.constant<number | null>(null), fc.integer({ min: 0, max: 1439 })),
  });
  const actionArb: fc.Arbitrary<InteractionAction> = fc.oneof(
    fc.record({ type: fc.constant<"DRAG_START">("DRAG_START"), eventId: fc.string() }),
    fc.record({
      type: fc.constant<"RESIZE_START">("RESIZE_START"),
      eventId: fc.string(),
      edge: fc.constantFrom<"start" | "end">("start", "end"),
    }),
    fc.record({ type: fc.constant<"SELECT_START">("SELECT_START"), point: pointArb }),
    fc.record({ type: fc.constant<"SELECT_MOVE">("SELECT_MOVE"), point: pointArb }),
    fc.constant<InteractionAction>({ type: "COMMIT" }),
    fc.constant<InteractionAction>({ type: "CANCEL" }),
  );

  it("never lands in an unknown status", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        const state = actions.reduce<InteractionState>(interactionReducer, initialInteractionState);
        expect(["idle", "dragging", "resizing", "selecting"]).toContain(state.status);
      }),
    );
  });

  it("a terminal action (COMMIT or CANCEL) always returns to idle", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        const state = actions.reduce<InteractionState>(interactionReducer, initialInteractionState);
        expect(interactionReducer(state, { type: "COMMIT" })).toEqual({ status: "idle" });
        expect(interactionReducer(state, { type: "CANCEL" })).toEqual({ status: "idle" });
      }),
    );
  });

  it("while selecting, the anchor equals the last SELECT_START point and never drifts", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        let expectedAnchor: GridPoint | null = null;
        let state: InteractionState = initialInteractionState;
        for (const action of actions) {
          state = interactionReducer(state, action);
          if (action.type === "SELECT_START") expectedAnchor = action.point;
          else if (action.type !== "SELECT_MOVE") expectedAnchor = null;
          if (state.status === "selecting") {
            expect(state.anchor).toEqual(expectedAnchor);
          }
        }
      }),
    );
  });

  it("the tracked eventId always matches the last DRAG_START/RESIZE_START", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        let expectedId: string | null = null;
        let state: InteractionState = initialInteractionState;
        for (const action of actions) {
          state = interactionReducer(state, action);
          if (action.type === "DRAG_START" || action.type === "RESIZE_START") expectedId = action.eventId;
          // SELECT_MOVE is a no-op outside selecting, so it never disturbs a tracked event id;
          // every other action either sets it (above) or leaves no event tracked.
          else if (action.type !== "SELECT_MOVE") expectedId = null;
          if (state.status === "dragging" || state.status === "resizing") {
            expect(state.eventId).toBe(expectedId);
          }
        }
      }),
    );
  });
});
