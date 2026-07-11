import { defineConfig } from "vitest/config";

// The React layer renders real DOM in tests (testing-library) -> jsdom environment.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
    css: false,
    // Polyfills PointerEvent / pointer capture / matchMedia that jsdom lacks, so the pointer-based
    // resize/select gestures and the reduced-motion check run under the same production code.
    setupFiles: ["./vitest.setup.ts"],
  },
});
