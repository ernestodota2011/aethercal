import { describe, expect, it } from "vitest";
import {
  type ReconcileState,
  applyOverrides,
  initialReconcileState,
  reconcileReducer,
  selectSettledIds,
} from "./reconcile";
import type { CalendarEvent } from "./types";

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id">): CalendarEvent {
  return {
    title: partial.title ?? partial.id,
    start: partial.start ?? "2026-07-15T10:00:00",
    end: partial.end ?? "2026-07-15T11:00:00",
    ...partial,
  };
}

const base = evt({ id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", revision: 5 });

function submit(state: ReconcileState, over: Partial<Parameters<typeof reconcileReducer>[1]> = {}): ReconcileState {
  return reconcileReducer(state, {
    type: "SUBMIT",
    id: "e1",
    clientMutationId: "cm-1",
    start: "2026-07-16T10:00:00",
    end: "2026-07-16T11:00:00",
    baseRevision: 5,
    ...over,
  } as Parameters<typeof reconcileReducer>[1]);
}

describe("reconcileReducer — optimistic submit", () => {
  it("SUBMIT marks the event pending with the optimistic times", () => {
    const state = submit(initialReconcileState);
    const { events, pendingIds, rolledBackIds } = applyOverrides([base], state);
    expect(events[0]).toMatchObject({ id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00" });
    expect(pendingIds.has("e1")).toBe(true);
    expect(rolledBackIds.size).toBe(0);
  });
});

describe("reconcileReducer — commit on accept", () => {
  it("RESOLVE with a higher revision commits the server times + new revision, clears pending", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    const { events, pendingIds } = applyOverrides([base], state);
    expect(pendingIds.has("e1")).toBe(false);
    expect(events[0]).toMatchObject({ start: "2026-07-16T10:00:00", revision: 6 });
  });

  it("a committed override is dropped once the authoritative prop catches up to its revision", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    // The host updates its own events to the confirmed revision; the override must yield to it.
    const confirmed = evt({ id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 6 });
    expect(selectSettledIds([confirmed], state)).toContain("e1");
    const { events } = applyOverrides([confirmed], state);
    expect(events[0]).toMatchObject({ revision: 6 });
  });
});

describe("reconcileReducer — rollback on rejection / timeout", () => {
  it("REJECT rolls back to the authoritative times and flags a rollback", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, { type: "REJECT", id: "e1", clientMutationId: "cm-1" });
    const { events, pendingIds, rolledBackIds } = applyOverrides([base], state);
    expect(pendingIds.size).toBe(0);
    expect(rolledBackIds.has("e1")).toBe(true);
    // Rolled back to the untouched authoritative event (the optimistic 07-16 change is gone).
    expect(events[0]).toMatchObject({ start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" });
  });

  it("TIMEOUT rolls back exactly like a rejection", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, { type: "TIMEOUT", id: "e1", clientMutationId: "cm-1" });
    const { rolledBackIds, events } = applyOverrides([base], state);
    expect(rolledBackIds.has("e1")).toBe(true);
    expect(events[0]).toMatchObject({ start: "2026-07-15T10:00:00" });
  });

  it("CLEAR removes the override after the rollback flash", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, { type: "TIMEOUT", id: "e1", clientMutationId: "cm-1" });
    state = reconcileReducer(state, { type: "CLEAR", id: "e1" });
    const { rolledBackIds, pendingIds } = applyOverrides([base], state);
    expect(rolledBackIds.size).toBe(0);
    expect(pendingIds.size).toBe(0);
  });

  it("a late accept AFTER a timeout rollback does not resurrect the reverted change", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, { type: "TIMEOUT", id: "e1", clientMutationId: "cm-1" });
    // The server actually accepted it, but its response arrives after the client already rolled back.
    state = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    const { events, rolledBackIds, pendingIds } = applyOverrides([base], state);
    // Still rolled back to the authoritative times — NOT resurrected to the optimistic 07-16 change.
    expect(pendingIds.size).toBe(0);
    expect(rolledBackIds.has("e1")).toBe(true);
    expect(events[0]).toMatchObject({ start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" });
    // The revision watermark still advances, so a later stale response stays discarded.
    expect(state.appliedRevision.e1).toBe(6);
  });
});

describe("reconcileReducer — causal ordering (discard stale responses)", () => {
  it("discards a response whose revision is not greater than the already-applied revision", () => {
    let state = submit(initialReconcileState);
    state = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    const committed = applyOverrides([base], state).events[0];
    // A late / duplicate response carrying an OLDER revision must not overwrite the newer state.
    const after = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-20T08:00:00",
      end: "2026-07-20T09:00:00",
      revision: 5,
    });
    expect(after).toBe(state); // unchanged reference: the stale response was discarded
    expect(applyOverrides([base], after).events[0]).toEqual(committed);
  });

  it("a RESOLVE for a superseded mutation id records the revision but keeps the newer pending override", () => {
    let state = submit(initialReconcileState, { clientMutationId: "cm-1" });
    // A second, newer optimistic edit supersedes the first (different mutation id).
    state = reconcileReducer(state, {
      type: "SUBMIT",
      id: "e1",
      clientMutationId: "cm-2",
      start: "2026-07-18T10:00:00",
      end: "2026-07-18T11:00:00",
      baseRevision: 5,
    });
    // The FIRST mutation's response arrives late; it must not clobber the second, still-pending edit.
    state = reconcileReducer(state, {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    const { events, pendingIds } = applyOverrides([base], state);
    expect(pendingIds.has("e1")).toBe(true);
    expect(events[0]).toMatchObject({ start: "2026-07-18T10:00:00" });
  });
});

describe("reconcileReducer — optimistic resource moves (RF-28)", () => {
  const homed = evt({ id: "e1", revision: 5, resourceId: "h1" });

  it("renders a cross-row drag in the TARGET row while it is still pending", () => {
    // Without this the bar would snap straight back to the old resource row the moment you dropped
    // it, and only jump across once the server answered — the exact flicker optimistic UI exists to
    // prevent.
    const state = submit(initialReconcileState, { resourceId: "h2" });
    const { events, pendingIds } = applyOverrides([homed], state);
    expect(events[0]?.resourceId).toBe("h2");
    expect(pendingIds.has("e1")).toBe(true);
  });

  it("leaves the event's own resource alone when the mutation carries none", () => {
    // A month/week/day drop has no resource dimension; it must not blank the event's row.
    const state = submit(initialReconcileState);
    expect(applyOverrides([homed], state).events[0]?.resourceId).toBe("h1");
  });

  it("keeps the event in the target row once the server confirms the move", () => {
    const state = reconcileReducer(submit(initialReconcileState, { resourceId: "h2" }), {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
      resourceId: "h2",
    });
    expect(applyOverrides([homed], state).events[0]?.resourceId).toBe("h2");
  });

  it("holds the submitted row when the server accepts but does not restate it", () => {
    // The server said yes; a response that simply omits the resource must not be read as "moved back".
    const state = reconcileReducer(submit(initialReconcileState, { resourceId: "h2" }), {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
    });
    expect(applyOverrides([homed], state).events[0]?.resourceId).toBe("h2");
  });

  it("returns the event to its authoritative row when the move is rejected", () => {
    const state = reconcileReducer(submit(initialReconcileState, { resourceId: "h2" }), {
      type: "REJECT",
      id: "e1",
      clientMutationId: "cm-1",
    });
    const { events, rolledBackIds } = applyOverrides([homed], state);
    expect(events[0]?.resourceId).toBe("h1");
    expect(rolledBackIds.has("e1")).toBe(true);
  });

  it("yields to the authoritative event once its revision catches up", () => {
    const state = reconcileReducer(submit(initialReconcileState, { resourceId: "h2" }), {
      type: "RESOLVE",
      id: "e1",
      clientMutationId: "cm-1",
      start: "2026-07-16T10:00:00",
      end: "2026-07-16T11:00:00",
      revision: 6,
      resourceId: "h2",
    });
    // The server's own copy has landed (revision 6) and is the truth from here on.
    const authoritative = evt({ id: "e1", revision: 6, resourceId: "h2" });
    expect(applyOverrides([authoritative], state).events[0]).toEqual(authoritative);
  });
});

describe("applyOverrides — no overrides", () => {
  it("returns the events untouched when there is no in-flight mutation", () => {
    const events = [base, evt({ id: "e2", revision: 1 })];
    const { events: out, pendingIds, rolledBackIds } = applyOverrides(events, initialReconcileState);
    expect(out).toEqual(events);
    expect(pendingIds.size).toBe(0);
    expect(rolledBackIds.size).toBe(0);
  });
});
