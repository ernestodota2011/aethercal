/**
 * Optimistic reconciliation hook (AetherCal-06 §4, RF-21) — the React side of the headless
 * `reconcileReducer` in `@aethercal/calendar-core`.
 *
 * A calendar surface calls `submit(kind, payload)` from its drag/resize handler; the hook applies
 * the change immediately (the returned `events` reflect it, and its id joins `pendingIds`), assigns
 * a `client_mutation_id` for idempotency, and calls the caller's async `mutate`. On accept it
 * commits the server's times + new `revision`; on rejection OR timeout (the server never answers
 * within `timeoutMs`) it rolls back and briefly flags the id in `rolledBackIds` for the rollback
 * flash — the UI never sticks in `pending`. Stale / out-of-order responses (a `revision` not greater
 * than what is already applied) are discarded. All the ordering logic is the pure reducer; this hook
 * only owns the effects (promises, timers, id generation, and pruning committed overrides once the
 * authoritative `events` prop catches up).
 */
import {
  type CalendarEvent,
  type EventDropPayload,
  type EventResizePayload,
  type MutationKind,
  type ReconcileState,
  applyOverrides,
  initialReconcileState,
  reconcileReducer,
  selectSettledIds,
} from "@aethercal/calendar-core";
import * as React from "react";

/** The mutation handed to `mutate`: gesture kind + idempotency id + the payload (carrying it too). */
export interface CalendarMutation {
  kind: MutationKind;
  clientMutationId: string;
  payload: EventDropPayload & EventResizePayload;
}

/** What the server returns when it accepts a mutation: the confirmed times + the new revision. */
export interface MutationResult {
  id: string;
  start: string;
  end: string;
  revision: number;
}

export interface UseOptimisticEventsOptions {
  /** The authoritative events (server-confirmed). Optimistic overrides are layered on top. */
  events: readonly CalendarEvent[];
  /** Perform the mutation server-side; resolve with the new revision to commit, reject to roll back. */
  mutate: (mutation: CalendarMutation) => Promise<MutationResult>;
  /** Budget before an unanswered mutation is rolled back. Default 8000ms. */
  timeoutMs?: number;
  /** How long a rolled-back id stays flagged for the flash animation. Default 900ms. */
  rollbackFlashMs?: number;
  /** Idempotency-id generator (injectable for tests). Default `crypto.randomUUID()`. */
  generateId?: () => string;
}

export interface UseOptimisticEventsResult {
  events: CalendarEvent[];
  pendingIds: ReadonlySet<string>;
  rolledBackIds: ReadonlySet<string>;
  submit: (kind: MutationKind, payload: EventDropPayload | EventResizePayload) => void;
}

function defaultGenerateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `cm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

const DEFAULT_TIMEOUT_MS = 8000;
const DEFAULT_ROLLBACK_FLASH_MS = 900;

export function useOptimisticEvents(options: UseOptimisticEventsOptions): UseOptimisticEventsResult {
  const {
    events,
    mutate,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    rollbackFlashMs = DEFAULT_ROLLBACK_FLASH_MS,
    generateId = defaultGenerateId,
  } = options;

  const [state, dispatch] = React.useReducer(reconcileReducer, initialReconcileState);

  // Refs so the stable `submit` callback and cleanup see the latest values without re-subscribing.
  const stateRef = React.useRef<ReconcileState>(state);
  stateRef.current = state;
  const eventsRef = React.useRef(events);
  eventsRef.current = events;
  const mountedRef = React.useRef(true);
  const timersRef = React.useRef(new Map<string, ReturnType<typeof setTimeout>>());

  React.useEffect(() => {
    // Set true on (re)mount, not just once: React StrictMode runs mount -> cleanup -> mount in dev,
    // so a cleanup-only `false` would leave the ref permanently unmounted and silently drop every
    // resolve/reject/timeout dispatch after the double-invoke.
    mountedRef.current = true;
    const timers = timersRef.current;
    return () => {
      mountedRef.current = false;
      for (const handle of timers.values()) clearTimeout(handle);
      timers.clear();
    };
  }, []);

  // Prune committed overrides once the authoritative prop has caught up to their revision, so the
  // override map converges instead of growing (runs only when the events prop actually changes).
  React.useEffect(() => {
    for (const id of selectSettledIds(events, stateRef.current)) {
      const ov = stateRef.current.overrides[id];
      dispatch({ type: "CLEAR", id, ...(ov ? { clientMutationId: ov.clientMutationId } : {}) });
    }
  }, [events]);

  const submit = React.useCallback(
    (kind: MutationKind, payload: EventDropPayload | EventResizePayload) => {
      const clientMutationId = generateId();
      const base = eventsRef.current.find((event) => event.id === payload.id);
      const timers = timersRef.current;

      const clearTimer = (key: string): void => {
        const handle = timers.get(key);
        if (handle !== undefined) {
          clearTimeout(handle);
          timers.delete(key);
        }
      };
      const scheduleFlash = (): void => {
        timers.set(
          `fl:${clientMutationId}`,
          setTimeout(() => {
            timers.delete(`fl:${clientMutationId}`);
            if (mountedRef.current) dispatch({ type: "CLEAR", id: payload.id, clientMutationId });
          }, rollbackFlashMs),
        );
      };

      dispatch({
        type: "SUBMIT",
        id: payload.id,
        clientMutationId,
        start: payload.start,
        end: payload.end,
        ...(base?.revision !== undefined ? { baseRevision: base.revision } : {}),
      });

      timers.set(
        `to:${clientMutationId}`,
        setTimeout(() => {
          timers.delete(`to:${clientMutationId}`);
          if (!mountedRef.current) return;
          dispatch({ type: "TIMEOUT", id: payload.id, clientMutationId });
          scheduleFlash();
        }, timeoutMs),
      );

      const rollback = (): void => {
        clearTimer(`to:${clientMutationId}`);
        if (!mountedRef.current) return;
        dispatch({ type: "REJECT", id: payload.id, clientMutationId });
        scheduleFlash();
      };

      const mutation: CalendarMutation = {
        kind,
        clientMutationId,
        payload: { ...payload, client_mutation_id: clientMutationId },
      };
      // Call `mutate` synchronously, but normalize a synchronous throw into a rejection so a
      // sync-throwing adapter rolls back immediately instead of hanging until the timeout.
      let pending: Promise<MutationResult>;
      try {
        pending = mutate(mutation);
      } catch (error) {
        pending = Promise.reject(error instanceof Error ? error : new Error(String(error)));
      }
      pending
        .then((result) => {
          // A response for a different event id is a contract violation — revert rather than apply it.
          if (result.id !== payload.id) {
            rollback();
            return;
          }
          clearTimer(`to:${clientMutationId}`);
          if (!mountedRef.current) return;
          dispatch({
            type: "RESOLVE",
            id: result.id,
            clientMutationId,
            start: result.start,
            end: result.end,
            revision: result.revision,
          });
        })
        .catch(rollback);
    },
    [mutate, timeoutMs, rollbackFlashMs, generateId],
  );

  const applied = React.useMemo(() => applyOverrides(events, state), [events, state]);

  return {
    events: applied.events,
    pendingIds: applied.pendingIds,
    rolledBackIds: applied.rolledBackIds,
    submit,
  };
}
