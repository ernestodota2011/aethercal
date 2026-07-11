import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type ReconcileAction,
  type ReconcileState,
  applyOverrides,
  initialReconcileState,
  reconcileReducer,
} from "./reconcile";
import type { CalendarEvent } from "./types";

const IDS = ["e1", "e2"];
const events: CalendarEvent[] = IDS.map((id) => ({
  id,
  title: id,
  start: "2026-07-15T10:00:00",
  end: "2026-07-15T11:00:00",
  revision: 1,
}));

const idArb = fc.constantFrom(...IDS);
const cmidArb = fc.constantFrom("cm-a", "cm-b", "cm-c");
const timeArb = fc.constantFrom("2026-07-16T10:00:00", "2026-07-17T12:00:00");
const revArb = fc.integer({ min: 0, max: 10 });

const actionArb: fc.Arbitrary<ReconcileAction> = fc.oneof(
  fc.record({
    type: fc.constant<"SUBMIT">("SUBMIT"),
    id: idArb,
    clientMutationId: cmidArb,
    start: timeArb,
    end: timeArb,
    baseRevision: revArb,
  }),
  fc.record({
    type: fc.constant<"RESOLVE">("RESOLVE"),
    id: idArb,
    clientMutationId: cmidArb,
    start: timeArb,
    end: timeArb,
    revision: revArb,
  }),
  fc.record({ type: fc.constant<"REJECT">("REJECT"), id: idArb, clientMutationId: cmidArb }),
  fc.record({ type: fc.constant<"TIMEOUT">("TIMEOUT"), id: idArb, clientMutationId: cmidArb }),
  fc.record({ type: fc.constant<"CLEAR">("CLEAR"), id: idArb }),
);

describe("reconcileReducer — invariants over arbitrary action sequences", () => {
  it("applied revision is monotonic non-decreasing per event", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        let state: ReconcileState = initialReconcileState;
        const prev: Record<string, number> = {};
        for (const action of actions) {
          state = reconcileReducer(state, action);
          for (const [id, rev] of Object.entries(state.appliedRevision)) {
            if (prev[id] !== undefined) expect(rev).toBeGreaterThanOrEqual(prev[id]!);
            prev[id] = rev;
          }
        }
      }),
    );
  });

  it("an event is never simultaneously pending and rolled-back, and applyOverrides never throws", () => {
    fc.assert(
      fc.property(fc.array(actionArb), (actions) => {
        const state = actions.reduce<ReconcileState>(reconcileReducer, initialReconcileState);
        const { pendingIds, rolledBackIds } = applyOverrides(events, state);
        for (const id of pendingIds) expect(rolledBackIds.has(id)).toBe(false);
      }),
    );
  });

  it("CLEAR of an id always removes its override", () => {
    fc.assert(
      fc.property(fc.array(actionArb), idArb, (actions, id) => {
        let state = actions.reduce<ReconcileState>(reconcileReducer, initialReconcileState);
        state = reconcileReducer(state, { type: "CLEAR", id });
        expect(state.overrides[id]).toBeUndefined();
      }),
    );
  });
});
