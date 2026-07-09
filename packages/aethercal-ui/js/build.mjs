// Builds the AetherCal calendar core into the exact path the Python package ships from
// (`packages/aethercal-ui/src/aethercal/ui/assets/`), so `uv build --package aethercal-ui`
// (hatchling `artifacts`, see pyproject.toml) can package it into the wheel.
//
// `react` and `react/jsx-runtime` are kept external (not bundled): the output is loaded by
// Reflex's own frontend build via a dynamic import of a local asset path (see
// `aethercal.ui.calendar` / docs/spikes/f0-10-reflex-tsx.md), which resolves bare specifiers
// against the *host app's* node_modules — bundling our own copy of React would create a second
// React instance and break hooks.
import * as esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const outfile = path.join(
  here,
  "..",
  "src",
  "aethercal",
  "ui",
  "assets",
  "aethercal-calendar.js",
);

await esbuild.build({
  entryPoints: [path.join(here, "src", "index.ts")],
  outfile,
  bundle: true,
  format: "esm",
  target: "es2020",
  minify: true,
  sourcemap: false,
  external: ["react", "react/jsx-runtime"],
  logLevel: "info",
});

console.log(`built -> ${path.relative(process.cwd(), outfile)}`);
