/**
 * jsdom shims for the demo's component test. jsdom (as bundled with vitest) implements neither
 * `matchMedia`, `PointerEvent`, `DragEvent`, nor pointer capture — all of which the calendar touches
 * (reduced-motion query, pointer resize/select, HTML5 drag on the month view). These minimal,
 * standards-shaped polyfills let the same production code render under jsdom. The component guards
 * each of these defensively in real code, so the shims only let the test drive it, never paper over
 * missing behavior. (Mirrors packages/react/vitest.setup.ts.)
 */
const g = globalThis as unknown as {
  PointerEvent?: typeof MouseEvent;
  DragEvent?: typeof MouseEvent;
  DataTransfer?: unknown;
  matchMedia?: (query: string) => MediaQueryList;
};

// jsdom ships no `DataTransfer`, which the month-view chip touches on dragstart (setData /
// effectAllowed). A tiny in-memory store lets the HTML5 drag path run under jsdom.
if (typeof g.DataTransfer !== "function") {
  class DataTransferPolyfill {
    private readonly store = new Map<string, string>();
    dropEffect = "none";
    effectAllowed = "all";
    readonly types: string[] = [];
    setData(format: string, data: string): void {
      this.store.set(format, data);
      if (!this.types.includes(format)) this.types.push(format);
    }
    getData(format: string): string {
      return this.store.get(format) ?? "";
    }
    clearData(): void {
      this.store.clear();
      this.types.length = 0;
    }
    setDragImage(): void {}
  }
  g.DataTransfer = DataTransferPolyfill;
}

if (typeof g.DragEvent !== "function") {
  class DragEventPolyfill extends MouseEvent {
    readonly dataTransfer: DataTransfer | null;
    constructor(type: string, params: MouseEventInit & { dataTransfer?: DataTransfer } = {}) {
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

const doc = (globalThis as unknown as { document?: Document }).document;
if (doc && typeof doc.elementFromPoint !== "function") {
  (doc as unknown as { elementFromPoint: (x: number, y: number) => Element | null }).elementFromPoint =
    () => null;
}

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
