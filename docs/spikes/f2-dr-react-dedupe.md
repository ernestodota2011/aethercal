# F2-DR: React dedupe inside a real Reflex app

**Verdict: GO. React is deduplicated to a single instance when `aethercal.ui.Calendar`
is mounted in a real, fully-built Reflex app. No "Invalid hook call".** This closes the
one open condition risk #1 left by the F0-10 spike (`docs/spikes/f0-10-reflex-tsx.md`).

The dedupe is not luck: it rests on one load-bearing config choice — the esbuild bundle
keeps `react` / `react/jsx-runtime` **external** (`packages/aethercal-ui/js/build.mjs`).
Because the bundle vendors no React of its own, and Reflex installs exactly one React,
Vite resolves the bundle's bare `react` imports to that single copy. A negative control
that vendors React into the bundle reproduces the exact duplicate-React crash, proving
the `external` decision is what makes this work.

This spike also fixes one root-cause hygiene defect it surfaced: `react` was declared as
a hard `dependency` of the JS package when it is semantically a **peerDependency** (the
component relies on the host's React). See "Root-cause config fix" below.

---

## Environment

- Reflex `0.9.6.post1`. Its frontend is **Vite + React Router** (not Next.js), pinning
  **React 19.2.6** in `.web/package.json`
  (`reflex_base/constants/installer.py:_determine_react_version -> "19.2.6"`).
- The calendar core (`packages/aethercal-ui/js`) is built against React 18 as a
  dev/build tool only; it ships **no** React (external).
- Host machine: Windows 11, Node v23.11.0, pnpm 10.32.1. (Windows symlink caveat below.)

## How the wiring resolves (static trace)

1. `calendar.py`: `library = rx.asset("assets/aethercal-calendar.js", shared=True).importable_path`
   → `"$/public/external/aethercal/ui/calendar/assets/aethercal-calendar.js"`.
2. `NoSSRComponent._get_import_name` keeps a `$`-prefixed library as-is, so
   `_get_dynamic_imports` emits `import('$/public/external/.../aethercal-calendar.js')`.
3. Reflex's generated `vite.config.js` (`reflex_base/compiler/templates.py:vite_config_template`)
   aliases **`$` → `.web/`**:
   ```js
   resolve: { alias: [ { find: "$", replacement: fileURLToPath(new URL("./", import.meta.url)) }, ... ] }
   ```
   So the dynamic import is a **Vite module-graph import** (not a raw static `public/`
   fetch). Vite therefore resolves the bundle's bare `import "react"` /
   `import "react/jsx-runtime"` against `.web/node_modules/react` — the single host React.

The empirical build below confirms this trace end to end.

---

## Evidence

### Built bundle keeps React external (input assumption verified)

The committed bundle contains bare React imports, not a vendored React:

```
$ head -c 80 packages/aethercal-ui/src/aethercal/ui/assets/aethercal-calendar.js
import*as u from"react";import*as c from"react";import{jsx as N}from"react/jsx-runtime";...
```

The bundle is reproducible (no source↔artifact drift): a fresh `node build.mjs` yields a
byte-identical file (`sha256 36fc7012...982c0` before and after).

### A real Reflex app builds clean with the calendar mounted

Minimal Reflex host (`docs/spikes/f2-dr-react-dedupe/demo/`) mounts `Calendar` and wires
`on_event_drop` to a backend `rx.event` handler (so a real state↔component round trip
exists). Built with Reflex's own toolchain:

```
$ python -m reflex export --frontend-only --no-zip
...
Compiling: ---------------------------------------- 100% 14/13 0:00:01
Creating Production Build:  ---------------------------------- 100% 4/4 0:00:08
EXIT=0
```

Windows caveat (predicted by F0-10): `rx.asset(shared=True)` tries to **symlink** the
asset into `.web` at import time; on this box symlink creation fails
(`OSError WinError 1314`, no Developer Mode/admin). Worked around **without masking the
question** by pre-placing a real copy of the bundle at the exact destination
(`<demo>/assets/external/aethercal/ui/calendar/assets/aethercal-calendar.js`), so Reflex's
`if not dst_file.exists()` skips the symlink. The asset lands in `.web/public` exactly as a
symlink would — orthogonal to React dedupe. (On Linux/CI this is a non-issue.)

### Exactly one React on disk

```
$ find .web/node_modules -path '*react/package.json'    -> .web/node_modules/react     19.2.6   (single)
$ find .web/node_modules -path '*react-dom/package.json' -> .web/node_modules/react-dom 19.2.6   (single)
```
(`@emotion/react`, `lucide-react`, `@floating-ui/react-dom` are unrelated packages.)

### The Vite build processed our bundle through the module graph and shares one React chunk

Vite emitted a hashed chunk for our bundle — proof it went through the graph, not served
raw from `public/`:

```
.web/build/client/assets/aethercal-calendar-D8UrjTY-.js   (3230 B)
```

Its imports point React at the **shared** React chunk (it embeds no React of its own):

```
$ grep -oE 'from"[^"]*"' aethercal-calendar-*.js
from"./rolldown-runtime-QTnfLwEv.js"
from"./chunk-5KNZJZUH-03iYwFZK.js"   <-- React core
from"./esm-DLHhNCyz.js"              <-- jsx-runtime / deps
```

`chunk-5KNZJZUH` is the single React core (only chunk that is a React *leaf*; react-dom,
the jsx-runtime chunk and the react-router entry all **import** from it). The Reflex host
pages (`_index`, `root`, `_404`) import React from that **same** chunk. Host and component
therefore share one React instance.

### Runtime hook execution passes against the host's React 19 (positive)

`docs/spikes/f2-dr-react-dedupe/runtime/hooktest.mjs`, run inside `.web/` so bare `react`
resolves to the host's single copy, renders the component (executing its
`useMemo`/`useCallback`):

```
REACT_VERSION 19.2.6
HOOKS_RAN_OK true
HTML_LEN 9428
HTML_HEAD <div class="aethercal-calendar" data-view="month">...
```

The component built against React 18 runs cleanly against the host's React **19.2.6** — the
hooks it uses (`useMemo`/`useCallback`/jsx-runtime) are stable across 18↔19.

### Negative control: vendoring React reproduces the crash (proves root cause)

`runtime/build-bundled.mjs` rebuilds the same core **without** `external` (React vendored
in: 3.2 KB → 11.6 KB). Rendering it with the host's React-DOM 19 (`hooktest-neg.mjs`):

```
HOST_REACT_VERSION 19.2.6
EXPECTED_FAILURE Cannot read properties of null (reading 'useMemo')
```

That is the duplicate-React "Invalid hook call" failure. It fires only when React is
duplicated — confirming `external:['react','react/jsx-runtime']` is the load-bearing cause
of the GO, not incidental.

---

## Root-cause config fix (applied on this branch)

`packages/aethercal-ui/js/package.json`: `react` was a hard `dependency`. Since the bundle
keeps React external and relies on the host's React, it is a **peerDependency**. Left as a
plain dependency it (a) misstates the contract and (b) would risk pulling a *second* React
if this package were ever npm-published or consumed transitively — the exact duplication
this spike rules out. Fixed at the root, not patched:

```diff
-  "dependencies": {
-    "react": "^18.3.1"
-  },
+  "peerDependencies": {
+    "react": ">=18.0.0 <20.0.0"
+  },
   "devDependencies": {
     "@types/react": "^18.3.12",
     "esbuild": "^0.28.1",
+    "react": "^18.3.1",
     "typescript": "^5.6.3",
     "vitest": "^3.2.7"
   },
```

`react` stays in `devDependencies` for building/tests/types. The peer range `>=18 <20`
admits both the dev-time React 18 and Reflex's runtime React 19. Verified after the change:
`pnpm install` clean, `pnpm build` byte-identical bundle (`sha256 36fc7012...982c0`,
no drift), `pnpm test` 7/7, `pnpm typecheck` clean.

---

## Implications for the F2 architecture

- **The planned `calendar-core` (headless) + `calendar-react` split needs no change to the
  build/externals strategy — keep it exactly as is.** `external:['react','react/jsx-runtime']`
  in esbuild + `react` as a **peerDependency** is the whole dedupe contract. Any future
  React-consuming package in this pipeline (e.g. a separate `calendar-react` bundle) must
  follow the same two rules. Do **not** vendor React under time pressure (F0-10 risk #4).
- **The `$/public` local-asset path is confirmed as a real Vite module import**, so a
  richer F2 bundle (day/list views, i18n, theming) inherits the same dedupe automatically —
  no need to publish the core to npm just to get a stable `library=` (F0-10 risk #1's
  fallback is unnecessary).
- **Track the React 18 (dev) vs 19 (runtime host) gap.** It works today, but F2 should
  align the JS package's dev `react`/`@types/react` to 19 for type fidelity, and re-run this
  spike's positive+negative checks whenever Reflex bumps its pinned React
  (`_determine_react_version`) — a matching-React CI check is cheap insurance.
- **Windows dev friction is real but non-blocking**: `rx.asset(shared=True)` needs symlink
  privilege (Developer Mode) or a pre-placed copy. CI/Linux and the wheel path are
  unaffected (the wheel ships the built bundle; no symlink). Note it in the F2 contributor
  setup.
- **Bundle-drift guard remains F2 work** (unchanged from F0-10): a CI step that runs
  `pnpm build` + `git diff --exit-code` on the committed bundle. This spike re-confirmed the
  bundle is reproducible, so that guard is straightforward.

## Reproduction

1. `docs/spikes/f2-dr-react-dedupe/demo/` is a minimal Reflex app. From a checkout with the
   monorepo venv (`aethercal-ui[reflex]` installed): copy the built bundle into
   `assets/external/aethercal/ui/calendar/assets/aethercal-calendar.js` (Windows symlink
   workaround), then `python -m reflex export --frontend-only --no-zip`.
2. Inspect `.web/node_modules` (one react/react-dom) and `.web/build/client/assets`
   (`aethercal-calendar-*.js` imports React from the shared `chunk-*` that the host pages
   also use).
3. Runtime: copy `runtime/hooktest.mjs` into `.web/` and `node hooktest.mjs` (expect
   `HOOKS_RAN_OK true`, React 19.2.6). For the negative control, run
   `runtime/build-bundled.mjs` next to `packages/aethercal-ui/js/build.mjs`, then
   `hooktest-neg.mjs` (expect `Cannot read properties of null (reading 'useMemo')`).
