/**
 * jsdom shims for the F2-D interaction tests.
 *
 * jsdom (as bundled with vitest) implements neither `PointerEvent` nor pointer capture nor
 * `matchMedia`. The calendar uses Pointer Events for resize/select (so it works with mouse, touch,
 * and pen in real browsers) and reads `prefers-reduced-motion` for the rollback animation. These
 * minimal, standards-shaped polyfills let the same production code run under jsdom; the component
 * itself calls pointer-capture defensively (optional chaining) and guards `matchMedia`, so these
 * shims only exist to let tests *fire* the events, never to paper over missing product behavior.
 */
const g = globalThis as unknown as {
  PointerEvent?: typeof MouseEvent;
  DragEvent?: typeof MouseEvent;
  matchMedia?: (query: string) => MediaQueryList;
};

// jsdom has no DragEvent, so a synthetic drop carries no clientY — which the time-grid drop reads to
// change an event's hour. This MouseEvent-based shim carries clientY (for the geometry) and
// dataTransfer (for the existing move-id corroboration); real browsers provide the genuine class.
if (typeof g.DragEvent !== "function") {
  class DragEventPolyfill extends MouseEvent {
    readonly dataTransfer: DataTransfer | null;
    constructor(type: string, params: (MouseEventInit & { dataTransfer?: DataTransfer }) = {}) {
      super(type, params);
      this.dataTransfer = params.dataTransfer ?? null;
    }
  }
  g.DragEvent = DragEventPolyfill as unknown as typeof MouseEvent;
}

if (typeof g.PointerEvent !== "function") {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 1;
      this.pointerType = params.pointerType ?? "mouse";
      this.isPrimary = params.isPrimary ?? true;
    }
  }
  g.PointerEvent = PointerEventPolyfill as unknown as typeof MouseEvent;
}

const proto = Element.prototype as unknown as {
  setPointerCapture?: (pointerId: number) => void;
  releasePointerCapture?: (pointerId: number) => void;
  hasPointerCapture?: (pointerId: number) => boolean;
};
proto.setPointerCapture ??= () => {};
proto.releasePointerCapture ??= () => {};
proto.hasPointerCapture ??= () => false;

if (typeof g.matchMedia !== "function") {
  g.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
