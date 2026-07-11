import type { CalendarEvent } from "@aethercal/calendar-core";
import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type CalendarMutation,
  type MutationResult,
  useOptimisticEvents,
} from "./useOptimisticEvents";

afterEach(() => {
  vi.useRealTimers();
});

const EVENT: CalendarEvent = {
  id: "e1",
  title: "Consult",
  start: "2026-07-15T10:00:00",
  end: "2026-07-15T11:00:00",
  revision: 1,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useOptimisticEvents — commit on accept", () => {
  it("shows the change immediately (pending), then commits the server times + new revision", async () => {
    const d = deferred<MutationResult>();
    const { result } = renderHook(() =>
      useOptimisticEvents({ events: [EVENT], mutate: () => d.promise, generateId: () => "cm-1" }),
    );
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    expect(result.current.pendingIds.has("e1")).toBe(true);
    expect(result.current.events[0]!.start).toBe("2026-07-16T10:00:00");

    await act(async () => {
      d.resolve({ id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 2 });
    });
    expect(result.current.pendingIds.has("e1")).toBe(false);
    expect(result.current.events[0]!.revision).toBe(2);
  });

  it("passes a client_mutation_id into mutate for idempotency", () => {
    const mutate = vi.fn(() => new Promise<MutationResult>(() => {}));
    const { result } = renderHook(() =>
      useOptimisticEvents({ events: [EVENT], mutate, generateId: () => "cm-xyz" }),
    );
    act(() => {
      result.current.submit("resize", { id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T12:00:00", revision: 1 });
    });
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "resize",
        clientMutationId: "cm-xyz",
        payload: expect.objectContaining({ id: "e1", client_mutation_id: "cm-xyz" }),
      }),
    );
  });
});

describe("useOptimisticEvents — rollback on rejection", () => {
  it("reverts a server-rejected drag and flashes, then clears after the flash window", async () => {
    vi.useFakeTimers();
    const d = deferred<MutationResult>();
    const { result } = renderHook(() =>
      useOptimisticEvents({
        events: [EVENT],
        mutate: () => d.promise,
        generateId: () => "cm-1",
        rollbackFlashMs: 500,
      }),
    );
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    expect(result.current.pendingIds.has("e1")).toBe(true);

    await act(async () => {
      d.reject(new Error("server said no"));
      await Promise.resolve();
    });
    expect(result.current.rolledBackIds.has("e1")).toBe(true);
    expect(result.current.events[0]!.start).toBe("2026-07-15T10:00:00"); // reverted to authoritative

    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.rolledBackIds.has("e1")).toBe(false);
  });
});

describe("useOptimisticEvents — rollback on timeout (no response)", () => {
  it("reverts a resize when the server never responds within the budget", () => {
    vi.useFakeTimers();
    const d = deferred<MutationResult>(); // never settles
    const { result } = renderHook(() =>
      useOptimisticEvents({
        events: [EVENT],
        mutate: () => d.promise,
        generateId: () => "cm-1",
        timeoutMs: 3000,
      }),
    );
    act(() => {
      result.current.submit("resize", { id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T12:30:00", revision: 1 });
    });
    expect(result.current.pendingIds.has("e1")).toBe(true);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.pendingIds.has("e1")).toBe(false);
    expect(result.current.rolledBackIds.has("e1")).toBe(true);
    expect(result.current.events[0]!.end).toBe("2026-07-15T11:00:00"); // reverted
  });
});

describe("useOptimisticEvents — React StrictMode double-invoke", () => {
  it("still commits after StrictMode's mount -> cleanup -> mount (mountedRef restored)", async () => {
    const d = deferred<MutationResult>();
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(React.StrictMode, null, children);
    const { result } = renderHook(
      () => useOptimisticEvents({ events: [EVENT], mutate: () => d.promise, generateId: () => "cm-1" }),
      { wrapper },
    );
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    await act(async () => {
      d.resolve({ id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 2 });
    });
    // Would stay pending if the double-invoke had left the hook marked unmounted.
    expect(result.current.pendingIds.has("e1")).toBe(false);
    expect(result.current.events[0]!.revision).toBe(2);
  });
});

describe("useOptimisticEvents — adapter contract failures roll back", () => {
  it("rolls back when mutate throws synchronously (never leaves it pending)", async () => {
    const { result } = renderHook(() =>
      useOptimisticEvents({
        events: [EVENT],
        mutate: () => {
          throw new Error("adapter boom");
        },
        generateId: () => "cm-1",
        rollbackFlashMs: 500,
      }),
    );
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.pendingIds.has("e1")).toBe(false);
    expect(result.current.rolledBackIds.has("e1")).toBe(true);
    expect(result.current.events[0]!.start).toBe("2026-07-15T10:00:00");
  });

  it("rolls back when the server responds for a DIFFERENT event id (contract violation)", async () => {
    const d = deferred<MutationResult>();
    const { result } = renderHook(() =>
      useOptimisticEvents({ events: [EVENT], mutate: () => d.promise, generateId: () => "cm-1", rollbackFlashMs: 500 }),
    );
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    await act(async () => {
      d.resolve({ id: "SOMEONE-ELSE", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 2 });
    });
    expect(result.current.rolledBackIds.has("e1")).toBe(true);
    expect(result.current.events[0]!.start).toBe("2026-07-15T10:00:00");
  });
});

describe("useOptimisticEvents — causal ordering (discard stale response)", () => {
  it("discards an out-of-order response with a lower revision and keeps the newer commit", async () => {
    const dA = deferred<MutationResult>();
    const dB = deferred<MutationResult>();
    let n = 0;
    const generateId = () => `cm-${++n}`;
    const mutate = vi
      .fn<(m: CalendarMutation) => Promise<MutationResult>>()
      .mockReturnValueOnce(dA.promise)
      .mockReturnValueOnce(dB.promise);
    const { result } = renderHook(() => useOptimisticEvents({ events: [EVENT], mutate, generateId }));

    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 1 });
    });
    act(() => {
      result.current.submit("drop", { id: "e1", start: "2026-07-18T10:00:00", end: "2026-07-18T11:00:00", revision: 1 });
    });

    // Second (newer) mutation confirms first, at revision 3.
    await act(async () => {
      dB.resolve({ id: "e1", start: "2026-07-18T10:00:00", end: "2026-07-18T11:00:00", revision: 3 });
    });
    expect(result.current.events[0]!.start).toBe("2026-07-18T10:00:00");

    // First mutation's response arrives LATE with an older revision (2) — must be discarded.
    await act(async () => {
      dA.resolve({ id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 2 });
    });
    expect(result.current.events[0]!.start).toBe("2026-07-18T10:00:00");
    expect(result.current.events[0]!.revision).toBe(3);
  });
});
