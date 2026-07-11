// Builds the AetherCal calendar bundle into the exact path the Python package ships from
// (`packages/aethercal-ui/src/aethercal/ui/assets/`), so `uv build --package aethercal-ui`
// (hatchling `artifacts`, see pyproject.toml) can package it into the wheel.
//
// The entry is `@aethercal/calendar-react` (the React layer); esbuild inlines its workspace
// dependency `@aethercal/calendar-core` (headless TS) from source. `react` and
// `react/jsx-runtime` are kept EXTERNAL (not bundled): the output is loaded by Reflex's own
// frontend build via a dynamic import of a local asset path (see `aethercal.ui.calendar` /
// docs/spikes/f2-dr-react-dedupe.md), which resolves bare specifiers against the *host app's*
// node_modules — vendoring our own React would create a second instance and break hooks
// ("Invalid hook call"). This external decision is the load-bearing dedupe contract (F2-DR).
import * as esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = path.join(here, "packages", "react", "src", "index.ts");
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
  entryPoints: [entry],
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
