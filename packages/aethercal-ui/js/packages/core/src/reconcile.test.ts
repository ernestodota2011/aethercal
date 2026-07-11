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

describe("applyOverrides — no overrides", () => {
  it("returns the events untouched when there is no in-flight mutation", () => {
    const events = [base, evt({ id: "e2", revision: 1 })];
    const { events: out, pendingIds, rolledBackIds } = applyOverrides(events, initialReconcileState);
    expect(out).toEqual(events);
    expect(pendingIds.size).toBe(0);
    expect(rolledBackIds.size).toBe(0);
  });
});
