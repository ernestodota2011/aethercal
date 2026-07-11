import { defineConfig } from "vitest/config";

// Two test kinds: the pure sample-data generator (src/*.test.ts) and the App shell component test
// (src/*.test.tsx, testing-library). jsdom is a superset environment that serves both — the pure
// data test doesn't touch the DOM, so running it under jsdom costs nothing. The setup file polyfills
// the browser APIs jsdom lacks that the calendar reaches for (matchMedia / PointerEvent / DragEvent).
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
  },
});
