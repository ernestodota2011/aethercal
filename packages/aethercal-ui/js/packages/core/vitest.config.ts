import { defineConfig } from "vitest/config";

// The headless core runs in a plain Node environment — no DOM needed (that's the React layer).
// A fixed DST-observing timezone makes the drag geometry's DST behavior deterministic (the
// July-dated example tests have no transition; the dedicated dst spec exercises spring-forward).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    env: {
      TZ: "America/New_York",
    },
  },
});
