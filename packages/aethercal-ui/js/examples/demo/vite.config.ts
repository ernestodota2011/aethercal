import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Static demo build (AetherCal-06 §9): the calendar runs entirely client-side, so this bundles to a
// portable static site with no backend.
//   - base "./"  -> relative asset URLs, so the output serves from any subdomain or sub-path.
//   - dedupe react -> one React instance even though calendar-react declares it as a peer (the same
//     single-instance contract the Reflex wrapper relies on; here Vite enforces it for the browser).
//   - the workspace packages are consumed AS SOURCE (their `exports` point at src/*.ts), so they are
//     excluded from the dep pre-bundle and transformed by Vite's normal pipeline (plugin-react JSX).
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    exclude: ["@aethercal/calendar-react", "@aethercal/calendar-core"],
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
