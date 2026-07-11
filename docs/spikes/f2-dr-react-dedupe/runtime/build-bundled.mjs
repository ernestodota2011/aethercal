// Builds the NEGATIVE-CONTROL bundle for the F2-DR spike: the same calendar bundle
// but WITHOUT `external: ['react', 'react/jsx-runtime']`, so React gets vendored
// INTO the bundle (the anti-pattern this spike warns against).
//
// Placement/run: drop this next to the calendar workspace's build.mjs
// (packages/aethercal-ui/js/) so it resolves esbuild + react from that workspace's
// node_modules, then `node build-bundled.mjs`. Post-F2-A the entry is the React layer
// (`packages/react/src/index.ts`), matching the real build.mjs — esbuild inlines the
// headless core and, with no `external`, vendors React in too (the bundle grows vs the
// external build; that size jump IS React getting vendored). Feed the output to
// hooktest-neg.mjs.
import * as esbuild from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
await esbuild.build({
  entryPoints: [path.join(here, "packages", "react", "src", "index.ts")],
  outfile: path.join(here, "bundled-calendar.js"),
  bundle: true,
  format: "esm",
  target: "es2020",
  minify: true,
  // NB: no `external` -> react is vendored in. That is the whole point.
});
console.log("built negative-control bundle (react vendored in)");
