import { defineConfig } from "vitest/config";

// The React layer renders real DOM in tests (testing-library) -> jsdom environment.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
    css: false,
  },
});
