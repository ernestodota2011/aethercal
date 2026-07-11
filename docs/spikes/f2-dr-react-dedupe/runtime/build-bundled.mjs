// Builds the NEGATIVE-CONTROL bundle for the F2-DR spike: the same calendar core
// but WITHOUT `external: ['react', 'react/jsx-runtime']`, so React gets vendored
// INTO the bundle (the anti-pattern this spike warns against).
//
// Placement/run: drop this next to the calendar core's build.mjs
// (packages/aethercal-ui/js/) so it resolves esbuild + react from that project's
// node_modules, then `node build-bundled.mjs`. It writes bundled-calendar.js
// (~11.6 KB vs the 3.2 KB external build -- the size jump IS React getting
// vendored in). Feed that file to hooktest-neg.mjs.
import * as esbuild from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
await esbuild.build({
  entryPoints: [path.join(here, "src", "index.ts")],
  outfile: path.join(here, "bundled-calendar.js"),
  bundle: true,
  format: "esm",
  target: "es2020",
  minify: true,
  // NB: no `external` -> react is vendored in. That is the whole point.
});
console.log("built negative-control bundle (react vendored in)");
