# F0-10 Spike A: Reflex wrapper around a custom TSX calendar core

**Verdict: GO on the architecture, with one unverified item to close before F2 build-out
begins.**

The wrap-and-package pipeline (custom TSX core → built JS bundle → `rx.Component` wrapper →
bundle shipped inside the Python wheel) works end to end and is provably testable without a
frontend toolchain in the loop. Build the F2 `aethercal-ui` on this foundation. The one open
question — whether Reflex's own frontend build correctly deduplicates React for a *local*
(non-npm) component the way it does for npm-published ones — could not be verified inside this
spike's timebox (see "What this spike did not verify") and should be the first thing F2 confirms,
cheaply, before investing in the full month/week/day/list feature set.

## Resolution at F0 integration (bundle is committed)

The spike originally left the built bundle **gitignored**, on the model that a `fetch-js-bundle`
task (still a stub) would repopulate it for source/editable installs. Integration testing exposed
the gap: `rx.asset()` validates the bundle at **import time**, so on a fresh clone with no built JS
(exactly what CI does) `import aethercal.ui.calendar` raises `FileNotFoundError` and every
`aethercal-ui` test errors on collection — green locally, **red in CI**. Rather than ship that
trap, the ~3 KB bundle is now **committed** (`.gitignore` no longer excludes
`src/aethercal/ui/assets/`): CI, fresh clones and `pip install` all work with no Node toolchain,
and `rx.asset`'s import-time check passes. The trade-off is a tracked build artifact with a **drift
risk** (a TSX change that isn't rebuilt-and-recommitted ships a stale bundle; the Python wrapper
tests and the vitest suite would not catch it because neither exercises the built bundle). The F2
hardening is a CI step that runs `pnpm build` and `git diff --exit-code` on the bundle so a stale
commit fails the build — deferred here to keep the F0 CI free of a Node matrix.

## What was built

- `packages/aethercal-ui/js/` — an isolated pnpm project (own `package.json` +
  `pnpm-lock.yaml`, not wired into any workspace outside that folder) containing a minimal
  TSX calendar core: `AetherCalendar` renders a month grid (42 cells, Monday-first) or a week
  grid (7 columns) from the same `CalendarGrid` component, renders a list of `CalendarEvent`
  props grouped onto their day, and supports HTML5 drag-and-drop of an event onto a new day,
  emitting `onEventDrop({ id, start, end })` with the recomputed range (original duration and
  time-of-day preserved). Built with `esbuild` (`js/build.mjs`) to a single ESM bundle at
  `packages/aethercal-ui/src/aethercal/ui/assets/aethercal-calendar.js` (3.1 KB minified).
  `react`/`react/jsx-runtime` are kept **external**, not bundled in — see the React-duplication
  discussion below. 7 `vitest` unit tests cover the pure date-grid math and the drop
  recomputation (`js/src/__tests__/dateMath.test.ts`); `tsc --noEmit` is clean under `strict`.
- `packages/aethercal-ui/src/aethercal/ui/calendar.py` — an `rx.NoSSRComponent` subclass
  (`Calendar`) wrapping that bundle: typed props `view: Var[str]` (validated to `"month"`/
  `"week"` in `create()`), `events: Var[list[CalendarEvent]]`, and a wired
  `on_event_drop: EventHandler[...]` trigger whose signature passes the JS drop payload straight
  through to a Python handler. `library` is set from
  `rx.asset(path="assets/aethercal-calendar.js", shared=True).importable_path` — Reflex's own
  documented mechanism for a component author to ship a local, non-npm JS asset alongside their
  Python module.
- `packages/aethercal-ui/tests/test_calendar.py` — 10 pytest tests asserting on the component's
  **compiled/rendered form** (`Component._render()` → a `Tag` with `.name`/`.props`), per the
  spike's TDD instructions: the tag name, the `NoSSRComponent` base, the local (not npm) library
  path, that `view`/`events` values actually flow into the rendered props, that `on_event_drop`
  is a registered trigger, and that wiring/omitting a handler correctly adds/omits `onEventDrop`
  from the rendered props.
- `reflex>=0.9.6.post1` added to `aethercal-ui` as the `reflex` **extra** (see "Dependency
  decision" below), plus `packages/aethercal-ui/pyproject.toml`'s
  `[tool.hatch.build.targets.wheel].artifacts` entry that force-includes the gitignored, built
  JS bundle into the wheel (hatchling's default "vcs" file-selection strategy only includes
  git-tracked files, which would otherwise silently drop it).

## What worked

- **The TDD contract for a Reflex wrapper is real and cheap.** `Component._render()` returns a
  plain `Tag` dataclass (`name`, `props: Mapping[str, Var | EventChain]`); asserting on it — no
  browser, no Node, no `reflex run` — is fast (10 tests in 0.6s) and catches exactly the bugs
  that matter for a wrapper (wrong tag, prop not flowing, handler not wired, wrong camelCase key).
- **`rx.asset(path=..., shared=True)` is the correct, working mechanism for a packaged local
  asset**, confirmed by reading the actual installed source (`reflex/assets.py`) rather than
  guessing from generic docs — its `.importable_path` (`"$/public/<path>"`) is exactly what
  `NoSSRComponent._get_import_name` expects, and it fails loudly (`FileNotFoundError`) at
  **import time** if the JS bundle hasn't been built, which is the right failure mode for
  contributors working from source (see the silent counter-example below).
- **The wheel-packaging half is proven, not assumed**: `uv build --package aethercal-ui`
  produces a 5.5 KB wheel containing `aethercal/ui/assets/aethercal-calendar.js` — see Evidence.
  A `pip install aethercal-ui` (or `uv add aethercal-ui[reflex]`) needs no Node toolchain.
- **Strict pyright stayed green with zero config weakening**, following the same
  `serde.py`-style `Any`-boundary discipline requested by the brief: every reflex-facing symbol
  (`ObjectVar`, `EventHandler`, the ArgsSpec function, the `TypedDict` payloads) is imported and
  re-typed explicitly at the top of `calendar.py`; nothing untyped leaks into the module's own
  public surface. `packages/aethercal-ui/src` type-checks under the same
  `[tool.pyright] typeCheckingMode = "strict"` as `aethercal-core`.
- **Keeping the calendar a controlled component** (it never mutates its own `events`; it only
  emits `onEventDrop` and expects the caller to update the `events` prop from outside) kept both
  the TSX and the Python wrapper genuinely minimal, as instructed.

## What was awkward

- **The public `reflex`/`rx` namespace is not sufficient to author a component with documented
  props.** `rx.field` exists but is a *different, simpler* function
  (`default`/`default_factory`/`is_var` only) than the one every real Reflex add-on component
  uses to declare props with a docstring — that one
  (`default`/`default_factory`/`is_javascript_property`/`doc`) only exists on the internal
  `reflex_base.components.component` module. I found this by reading
  `reflex_components_react_player`'s actual source (a real, officially-shipped add-on package
  installed alongside `reflex` in this environment) rather than trusting the public API surface,
  and confirmed by comparing `inspect.signature()` of both — they are not the same object
  (`rx.field is not reflex_base...field`). `calendar.py` imports `field` from `reflex_base`
  directly, matching that convention; see "Concrete risks" below for why that's worth flagging,
  not hiding.
- **Getting the local-asset `library` convention right required reading Reflex's own source**,
  not just its docs: `NoSSRComponent._get_import_name`/`_get_dynamic_imports` (which prefix a
  `"/"`-leading `library` with `$` and dynamically `import()` it, vs. treating an npm-style
  string as a normal static import) and `jsconfig.json`'s `"$/*": ["*"]` path mapping are what
  actually explain how `rx.asset(shared=True).importable_path` is meant to be consumed. This is
  learnable, but it is *not* a five-minute integration for a contributor without prior Reflex
  internals exposure.
- **Hatchling's `artifacts` mechanism has a silent failure mode.** Tested directly (see
  Evidence): if the JS bundle is missing when `uv build --package aethercal-ui` runs, the build
  **succeeds** and produces a valid-looking wheel — just missing the asset, with no warning or
  error. This is the opposite failure mode from the dev-mode import (which fails loudly). A
  release pipeline for `aethercal-ui` needs an explicit "build JS bundle, then verify it exists,
  then build the wheel" step; hatchling will not catch a missing-bundle regression on its own.
- **Windows + `rx.asset(shared=True)` symlinking is fragile.** Outside of the
  `REFLEX_BACKEND_ONLY=1` guard this spike sets in `tests/conftest.py`, calling `rx.asset` from
  a plain `python -c` on this machine hit `OSError: [WinError 3]` creating the symlink (Windows
  symlink creation needs Developer Mode or elevation). Not a blocker — pytest never hits this
  path — but a real contributor demo app on Windows without Developer Mode enabled could hit it.

## Concrete risks for F2

1. **Unverified in a live browser: does Reflex's Vite build actually dedupe React for a *local*
   (non-npm) `NoSSRComponent`?** This is the one item this spike could not close. The JS bundle
   deliberately keeps `react`/`react/jsx-runtime` **external** (not bundled) rather than vendoring
   its own copy, because vendoring a second React instance is the well-known "Invalid hook call"
   failure mode when a host app's react-dom renders a component built against a different React
   module instance. Reading Reflex's `jsconfig.json` (`"$/*": ["*"]`, mirroring what its Vite
   config almost certainly aliases too) strongly suggests the dynamic `import('$/public/...')`
   *is* resolved through Vite's own module graph (which would dedupe `react` normally, the same
   as any bundled npm dependency) rather than being a raw, unprocessed static-asset fetch — but
   this spike did not compile a real Reflex app (`reflex init` + drop in the wheel + mount
   `<Calendar/>` + `reflex export`) to confirm zero hook errors in an actual browser, because
   that required standing up Reflex's full Node/Vite toolchain, which was out of this timebox.
   **Action for F2 (cheap, ~1-2 hours): build that minimal demo app first**, before investing in
   the full feature set, and treat a hook-call error there as a hard signal to reconsider (e.g.
   publish the calendar core to npm — even scoped/private — and use the ordinary
   `library="@aetherlogik/calendar@x.y.z"` path instead, which is the mechanism every other
   wrapped component in this Reflex version actually uses in practice).
2. **Reaching into `reflex_base` (not the public `reflex` package) for `field()`.** This works
   today and matches how Reflex's own shipped add-ons do it, but `reflex_base` is not the
   documented-stable public surface the way `reflex` is — a Reflex upgrade could rename or
   restructure it without the same semver discipline. Pin `reflex` narrowly (already
   `>=0.9.6.post1`; consider a `<0.10` upper bound in F2) and re-verify this import on any Reflex
   version bump.
3. **The `fetch-js-bundle` poe task is still a stub** (`root pyproject.toml`). Clarifying what it
   actually needs to do, now that the packaging half is proven: a **published** `aethercal-ui`
   wheel from PyPI already carries the bundle (no Node needed — this spike proved that half).
   `fetch-js-bundle` is only needed for **monorepo contributors** running `uv sync`
   (editable/workspace install, which reads `calendar.py` from source and therefore needs the
   physical file at `packages/aethercal-ui/src/aethercal/ui/assets/aethercal-calendar.js` to
   exist, but does not go through hatchling's wheel `artifacts` packaging at all) who don't have
   Node installed. The concrete F2 implementation should download the prebuilt bundle for the
   checked-out commit from a CI-published artifact (e.g. a GitHub Actions build) or a GitHub
   Release asset, and write it to that exact path — not attempt to run `pnpm`/`esbuild` itself.
4. **Bundle size will grow.** 3.1 KB is trivial for a 3-file spike; F2's real feature set (day/
   list views, optimistic reconciliation, theming, i18n) will be considerably larger. Keeping
   `react` external is what keeps this cheap — re-litigating that decision (e.g. under
   time-pressure "just bundle React, it's easier") would make the duplication risk in item 1
   certain instead of merely open.
5. **`Calendar.create()`'s `view` validation only catches literal strings**, not a `Var`-valued
   `view` bound to backend state (a legitimate, common F2 usage). That's fine for this spike
   (documented in the docstring) but F2 should decide whether server-side validation matters here
   or whether the TSX core's own default-view fallback is the intended single source of truth.

## What this spike did not verify (explicitly out of scope)

- Rendering `<Calendar/>` inside an actual running Reflex app in a browser (see risk 1).
- Any keyboard or touch fallback for drag (native HTML5 DnD is mouse-only by default).
- Multi-event stacking/overflow within a single day cell beyond a simple vertical list.
- Day or list views (ROADMAP.md scopes those, plus optimistic reconciliation and theming, to F2
  proper — this spike only needed month + week to prove the pipeline).

## Dependency decision: `reflex` as an extra, not a hard dependency

Followed the brief's strong preference: `reflex` is declared under
`aethercal-ui`'s `[project.optional-dependencies] reflex = [...]` (an extra), not in its bare
`dependencies`. A standalone `pip install aethercal-ui` (or `uv add aethercal-ui`) stays free of
`reflex` and everything it pulls in transitively (FastAPI-adjacent server bits, `granian`,
`python-socketio`, the `reflex_components_*` add-on family — 30 packages in total).

The wrinkle: this is a **single-venv uv workspace**, and `aethercal-ui`'s whole reason to exist
is to *be* the Reflex component — there is no meaningful bare install of it, and the repo's gate
is one unconditional `uv run poe check` that always type-checks and tests
`packages/aethercal-ui`. If the extra were left un-requested anywhere, a plain `uv sync` at the
workspace root would **not** install `reflex`, and `uv run poe check` would fail pyright/pytest
for `aethercal-ui` on a fresh clone — silently breaking the "one gate, always green" premise the
brief describes. The fix applied here: root `pyproject.toml`'s dev-workspace `dependencies` list
requests `"aethercal-ui[reflex]"` instead of the bare package name (the *only* other file this
spike touched besides `aethercal-ui/**` and `.gitignore`, as scoped). That keeps both properties
true at once — `uv sync` at the monorepo root always has what the gate needs (verified: a plain
`uv sync` after `uv lock` keeps `reflex` installed, see Evidence), while `aethercal-ui`'s own
published PyPI metadata keeps `reflex` genuinely optional for standalone consumers.

`uv.lock` impact: +631 lines (5 removed net-package-churn lines), 30 packages added on top of the
pre-spike lock. `packages/aethercal-ui/js/node_modules` is ~62 MB (pnpm-installed, gitignored,
irrelevant to the Python lock).

## Evidence

**Full gate, clean tail (`uv run poe check`):**

```
Poe => ruff format --check .
47 files already formatted
Poe => ruff check .
All checks passed!
Poe => pyright
0 errors, 0 warnings, 0 informations
Poe => lint-imports
...
Contracts: 2 kept, 0 broken.
Poe => pytest
...
============================ 131 passed in 40.07s ==============================
```

(121 pre-existing `aethercal-core` tests + 10 new `aethercal-ui` tests, all green. 7 additional
`vitest` tests for the TSX drag/grid math pass separately — see "What was built".)

**Wheel contains the built JS bundle** (`uv build --package aethercal-ui`, then
`python -m zipfile -l dist/aethercal_ui-0.0.0-py3-none-any.whl`):

```
File Name                                             Modified             Size
aethercal/ui/__init__.py                       2020-02-02 00:00:00          372
aethercal/ui/calendar.py                       2020-02-02 00:00:00         4794
aethercal/ui/py.typed                          2020-02-02 00:00:00            0
aethercal/ui/assets/aethercal-calendar.js      2020-02-02 00:00:00         3208
aethercal_ui-0.0.0.dist-info/METADATA          2020-02-02 00:00:00          356
aethercal_ui-0.0.0.dist-info/WHEEL             2020-02-02 00:00:00           87
aethercal_ui-0.0.0.dist-info/RECORD            2020-02-02 00:00:00          554
```

Confirmed empirically that this is not incidental: deleting the built bundle and rebuilding the
wheel *silently* drops that one line from the listing above (build still "succeeds") — the
"Hatchling's `artifacts` mechanism has a silent failure mode" risk above.

**Props-in / events-out tests** (`packages/aethercal-ui/tests/test_calendar.py`, all passing):
`test_calendar_compiles_to_the_aether_calendar_react_tag`,
`test_calendar_library_points_at_the_packaged_local_bundle_not_npm`,
`test_default_view_prop_flows_into_the_rendered_props`,
`test_explicit_view_prop_flows_into_the_rendered_props`,
`test_events_prop_flows_into_the_rendered_props`,
`test_on_event_drop_is_a_registered_event_trigger`,
`test_on_event_drop_handler_is_wired_into_the_rendered_props`,
`test_calendar_without_an_event_drop_handler_omits_the_prop`.
