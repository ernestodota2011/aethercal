/**
 * Pure optimistic-reconciliation reducer for calendar mutations (AetherCal-06 §4, RF-21).
 *
 * A mutation (drag/resize) is applied to the UI *immediately* (`pending`), then confirmed by the
 * server with a new monotonic `revision` (`committed`) or reverted (`rolledback`). This module is
 * the DOM-free, framework-agnostic state machine for that lifecycle so it is unit- and property-
 * testable in `calendar-core`; the React layer (`useOptimisticEvents`) only wires promises, timers,
 * and `client_mutation_id` generation to these actions.
 *
 * `revision` semantics (the F2-D acceptance criterion, fixed in `calendar-props.schema.json`):
 * a per-event monotonic-increasing integer the server assigns on each accepted mutation; the client
 * applies a response ONLY if its `revision` is greater than the highest already applied for that
 * event — a stale / out-of-order response (`revision <= applied`) is discarded. A `pending` mutation
 * that neither resolves nor rejects within the caller's budget is rolled back (TIMEOUT), so the UI
 * never sticks in `pending`.
 */
import type { CalendarEvent } from "./types";

export type OverrideStatus = "pending" | "committed" | "rolledback";

/** The optimistic state layered over one event while a mutation is in flight or just settled. */
export interface OptimisticOverride {
  readonly clientMutationId: string;
  readonly status: OverrideStatus;
  readonly start: string;
  readonly end: string;
  /** Optimistic (base) revision while pending; the server-assigned revision once committed. */
  readonly revision?: number;
}

export interface ReconcileState {
  readonly overrides: Readonly<Record<string, OptimisticOverride>>;
  /** Highest revision applied per event — the causal-ordering watermark for discarding stale responses. */
  readonly appliedRevision: Readonly<Record<string, number>>;
}

export const initialReconcileState: ReconcileState = { overrides: {}, appliedRevision: {} };

export type ReconcileAction =
  | {
      readonly type: "SUBMIT";
      readonly id: string;
      readonly clientMutationId: string;
      readonly start: string;
      readonly end: string;
      readonly baseRevision?: number;
    }
  | {
      readonly type: "RESOLVE";
      readonly id: string;
      readonly clientMutationId: string;
      readonly start: string;
      readonly end: string;
      readonly revision: number;
    }
  | { readonly type: "REJECT"; readonly id: string; readonly clientMutationId: string }
  | { readonly type: "TIMEOUT"; readonly id: string; readonly clientMutationId: string }
  | { readonly type: "CLEAR"; readonly id: string; readonly clientMutationId?: string };

function withoutKey<T>(record: Readonly<Record<string, T>>, key: string): Record<string, T> {
  const rest = { ...record };
  delete rest[key];
  return rest;
}

export function reconcileReducer(state: ReconcileState, action: ReconcileAction): ReconcileState {
  switch (action.type) {
    case "SUBMIT": {
      const seed = action.baseRevision ?? Number.NEGATIVE_INFINITY;
      const priorApplied = state.appliedRevision[action.id] ?? Number.NEGATIVE_INFINITY;
      return {
        overrides: {
          ...state.overrides,
          [action.id]: {
            clientMutationId: action.clientMutationId,
            status: "pending",
            start: action.start,
            end: action.end,
            ...(action.baseRevision !== undefined ? { revision: action.baseRevision } : {}),
          },
        },
        appliedRevision: { ...state.appliedRevision, [action.id]: Math.max(priorApplied, seed) },
      };
    }
    case "RESOLVE": {
      const applied = state.appliedRevision[action.id] ?? Number.NEGATIVE_INFINITY;
      // Causal ordering: a response no newer than what we already applied is stale — discard it.
      if (action.revision <= applied) return state;
      const existing = state.overrides[action.id];
      // Only a still-PENDING override for this exact mutation may commit visually. A response that
      // arrives after the mutation was already rolled back (TIMEOUT/REJECT) or superseded by a newer
      // edit only advances the revision watermark — it must NOT resurrect the reverted change (the
      // user already saw it fail); the next authoritative refresh reconciles the confirmed state.
      const canCommit =
        existing !== undefined &&
        existing.clientMutationId === action.clientMutationId &&
        existing.status === "pending";
      const overrides = canCommit
        ? {
            ...state.overrides,
            [action.id]: {
              clientMutationId: action.clientMutationId,
              status: "committed" as const,
              start: action.start,
              end: action.end,
              revision: action.revision,
            },
          }
        : state.overrides;
      return {
        overrides,
        appliedRevision: { ...state.appliedRevision, [action.id]: action.revision },
      };
    }
    case "REJECT":
    case "TIMEOUT": {
      const existing = state.overrides[action.id];
      if (!existing || existing.clientMutationId !== action.clientMutationId) return state;
      if (existing.status !== "pending") return state;
      return {
        ...state,
        overrides: { ...state.overrides, [action.id]: { ...existing, status: "rolledback" } },
      };
    }
    case "CLEAR": {
      const existing = state.overrides[action.id];
      if (!existing) return state;
      if (action.clientMutationId && existing.clientMutationId !== action.clientMutationId) return state;
      return { ...state, overrides: withoutKey(state.overrides, action.id) };
    }
  }
}

/** The events + pending/rolled-back id sets to render, with any optimistic overrides applied. */
export interface AppliedEvents {
  events: CalendarEvent[];
  pendingIds: ReadonlySet<string>;
  rolledBackIds: ReadonlySet<string>;
}

/**
 * Project `state` onto the authoritative `events`: pending/committed overrides replace an event's
 * times (a committed override yields once the prop's own revision catches up), while a rolledback
 * override shows the authoritative event again and flags it for the rollback animation.
 */
export function applyOverrides(
  events: readonly CalendarEvent[],
  state: ReconcileState,
): AppliedEvents {
  const pendingIds = new Set<string>();
  const rolledBackIds = new Set<string>();
  const projected = events.map((event) => {
    const ov = state.overrides[event.id];
    if (!ov) return event;
    if (ov.status === "pending") {
      pendingIds.add(event.id);
      return { ...event, start: ov.start, end: ov.end };
    }
    if (ov.status === "rolledback") {
      rolledBackIds.add(event.id);
      return event;
    }
    // committed: prefer the authoritative event once it has reached the confirmed revision.
    if (event.revision !== undefined && ov.revision !== undefined && event.revision >= ov.revision) {
      return event;
    }
    return {
      ...event,
      start: ov.start,
      end: ov.end,
      ...(ov.revision !== undefined ? { revision: ov.revision } : {}),
    };
  });
  return { events: projected, pendingIds, rolledBackIds };
}

/**
 * The ids of committed overrides that the authoritative `events` have caught up to (prop revision
 * >= override revision) and can therefore be pruned. The React layer dispatches `CLEAR` for these
 * when the `events` prop changes, so the override map converges instead of growing unbounded.
 */
export function selectSettledIds(
  events: readonly CalendarEvent[],
  state: ReconcileState,
): string[] {
  const byId = new Map(events.map((e) => [e.id, e]));
  const settled: string[] = [];
  for (const [id, ov] of Object.entries(state.overrides)) {
    if (ov.status !== "committed") continue;
    const event = byId.get(id);
    if (event && event.revision !== undefined && ov.revision !== undefined && event.revision >= ov.revision) {
      settled.push(id);
    }
  }
  return settled;
}
