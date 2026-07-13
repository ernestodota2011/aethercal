// Builds the DISTRIBUTABLE artifacts for the two published npm packages
// (`@aethercal/calendar-core` and `@aethercal/calendar-react`).
//
// This is NOT `build.mjs`. That one bundles the React layer into the single asset the Python wheel
// ships (`packages/aethercal-ui/src/aethercal/ui/assets/aethercal-calendar.js`), and CI guards it
// with a rebuild-and-diff drift check. This one produces a `dist/` inside each JS package, so npm
// consumers get compiled JS + type declarations instead of raw TypeScript sources.
//
// Why the packages still POINT at `src` in `exports`: inside the pnpm workspace the demo app and the
// Reflex bundler resolve these packages from source, which keeps one source of truth and no build
// step for contributors. `publishConfig` — which pnpm substitutes into the manifest at publish
// time — re-points `exports` / `main` / `types` at `dist`, so what ships to npm is the built code.
//
// Externals, per package:
//   core  — nothing. It has no runtime dependencies, by design (headless, no React).
//   react — `react` and `react/jsx-runtime` (peer: never vendor a second React, see
//           docs/spikes/f2-dr-react-dedupe.md), plus `@aethercal/calendar-core`, which stays a real
//           dependency instead of being inlined, so a consumer installing both gets ONE copy.

import * as esbuild from "esbuild";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

const PACKAGES = [
  { name: "@aethercal/calendar-core", dir: "core", external: [] },
  {
    name: "@aethercal/calendar-react",
    dir: "react",
    external: ["react", "react/jsx-runtime", "@aethercal/calendar-core"],
  },
];

for (const pkg of PACKAGES) {
  const root = path.join(here, "packages", pkg.dir);
  const outdir = path.join(root, "dist");

  fs.rmSync(outdir, { recursive: true, force: true });

  // 1. The JavaScript: one ESM file, bundled from the package's own sources.
  await esbuild.build({
    entryPoints: [path.join(root, "src", "index.ts")],
    outfile: path.join(outdir, "index.js"),
    bundle: true,
    format: "esm",
    target: "es2020",
    platform: "browser",
    sourcemap: true,
    external: pkg.external,
    logLevel: "warning",
  });

  // 2. The types. esbuild cannot emit declarations, so tsc does it, from a build-only tsconfig that
  //    EXCLUDES the test files — a published package must not ship its own `*.test.d.ts`.
  execFileSync(
    "node",
    [path.join(here, "node_modules", "typescript", "bin", "tsc"), "-p", "tsconfig.build.json"],
    { stdio: "inherit", cwd: root },
  );

  const js = fs.statSync(path.join(outdir, "index.js")).size;
  const dts = fs.statSync(path.join(outdir, "index.d.ts")).size;
  console.log(`built ${pkg.name} -> dist/index.js (${js} B) + dist/index.d.ts (${dts} B)`);
}
