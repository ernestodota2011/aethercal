// F2-A base for the "React that matches" CI check (F2-DR follow-up).
//
// The dedupe contract (docs/spikes/f2-dr-react-dedupe.md) rests on the calendar bundle keeping
// `react` EXTERNAL and declaring it as a *peerDependency*, so the host app's single React is the
// only one at runtime. Reflex pins its frontend React (today 19.2.6). F2-DR flagged one gap: the
// JS package built against React 18 dev types. F2-A aligns dev `react`/`@types/react` to 19; this
// script guards that alignment and that the peer range still admits the host's React.
//
// The React version Reflex pins is DERIVED from the installed Reflex (not hardcoded), so this check
// tracks a Reflex bump on its own instead of silently drifting against a stale literal. See
// resolveReflexPinnedReact() below for the resolution order and the documented fallback.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Last-known pin, used only when neither an explicit override nor the installed Reflex is reachable
// (e.g. a Node-only context with no Python/reflex on PATH). Bump alongside a Reflex upgrade.
const FALLBACK_REFLEX_PINNED_REACT = "19.2.6";

// `--require-derived`: fail (exit 2) instead of falling back if the pin can't be derived from an
// override or the installed Reflex. The CI job that HAS Reflex passes this, so a silent fallback
// there (which would let CI go green WITHOUT ever checking the real installed Reflex) becomes a hard
// error. Plain `pnpm check-react` (Node-only, no venv) omits it and keeps the structural checks.
const REQUIRE_DERIVED = process.argv.includes("--require-derived");
// Python interpreters to probe, overridable for tests (comma-separated). Default: python, python3.
const PROBE_PYTHONS = (process.env.AETHERCAL_REACT_PROBE_PYTHON ?? "python,python3")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

/**
 * Resolve the React version Reflex pins. Priority:
 *   1. REFLEX_PINNED_REACT env var — explicit override / escape hatch.
 *   2. The installed Reflex itself (`reflex_base.constants.installer`) — the real source of truth,
 *      so bumping Reflex auto-updates what this check enforces. Run under `uv run node ...` (or any
 *      context with the project venv on PATH) to exercise this path.
 *   3. FALLBACK_REFLEX_PINNED_REACT, with a loud warning — keeps the structural package checks
 *      running where Python/Reflex is not on PATH. Under `--require-derived` this path is a HARD
 *      error (exit 2) instead: a CI job that is supposed to verify against installed Reflex must
 *      never pass on the fallback.
 * @returns {{ version: string, source: string }}
 */
function resolveReflexPinnedReact() {
  const override = process.env.REFLEX_PINNED_REACT?.trim();
  if (override) return { version: override, source: "REFLEX_PINNED_REACT env override" };

  // Prefer the DEPENDENCIES dict Reflex actually writes to package.json; fall back to the
  // _determine_react_version() function. Either surviving a Reflex refactor keeps derivation working.
  const snippet = [
    "try:",
    "    from reflex_base.constants.installer import PackageJson",
    "    v = PackageJson.DEPENDENCIES['react']",
    "except Exception:",
    "    from reflex_base.constants.installer import _determine_react_version",
    "    v = _determine_react_version()",
    "print(v)",
  ].join("\n");
  for (const python of PROBE_PYTHONS) {
    try {
      const out = execFileSync(python, ["-c", snippet], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim();
      if (/^\d+\.\d+\.\d+/.test(out)) {
        return { version: out, source: `installed Reflex (${python})` };
      }
    } catch {
      // Interpreter absent or Reflex not importable — try the next one, then fall back.
    }
  }
  if (REQUIRE_DERIVED) {
    console.error(
      "ERROR: --require-derived was set but the React pin could not be derived from " +
        "REFLEX_PINNED_REACT or the installed Reflex. This job must verify against the real " +
        `installed Reflex (probed: ${PROBE_PYTHONS.join(", ")}); refusing to pass on the fallback.`,
    );
    process.exit(2);
  }
  console.warn(
    `WARN: could not derive React from the installed Reflex (no python/reflex on PATH); falling ` +
      `back to ${FALLBACK_REFLEX_PINNED_REACT}. Run under \`uv run node …\` or set ` +
      `REFLEX_PINNED_REACT to check against the real pin.`,
  );
  return { version: FALLBACK_REFLEX_PINNED_REACT, source: "fallback constant" };
}

const { version: REFLEX_PINNED_REACT, source: reactSource } = resolveReflexPinnedReact();
const here = path.dirname(fileURLToPath(import.meta.url));
const reactPkgPath = path.join(here, "..", "packages", "react", "package.json");

/** Parse the first "X[.Y[.Z]]" in a string into a [major, minor, patch] tuple (missing -> 0). */
function parseVersion(s) {
  const m = /(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(String(s ?? ""));
  if (!m) return null;
  return [Number(m[1]), Number(m[2] ?? 0), Number(m[3] ?? 0)];
}

function majorOf(s) {
  const v = parseVersion(s);
  return v ? v[0] : NaN;
}

/** Tuple compare: -1 if a<b, 0 if equal, 1 if a>b. */
function cmpVersion(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return 0;
}

/**
 * Resolve a semver range to an inclusive lower bound and an exclusive upper bound, for the range
 * shapes this repo actually uses (`>=a.b.c <d.e.f`, `^a.b.c`, `~a.b.c`, or a bare version).
 * Not a full semver implementation — deliberately handles only these forms and returns null on
 * anything it does not understand, so an unrecognized range fails closed (reported below).
 */
function resolveBounds(range) {
  const text = String(range ?? "").trim();
  const geMatch = /(?:>=)\s*(\d+\.\d+\.\d+)/.exec(text);
  const ltMatch = /<\s*(\d+\.\d+\.\d+)/.exec(text);
  if (geMatch || ltMatch) {
    return {
      lower: geMatch ? parseVersion(geMatch[1]) : [0, 0, 0],
      upper: ltMatch ? parseVersion(ltMatch[1]) : null,
    };
  }
  const caret = /^\^(\d+)\.(\d+)\.(\d+)/.exec(text);
  if (caret) {
    const [, maj, min, pat] = caret.map(Number);
    return { lower: [maj, min, pat], upper: [maj + 1, 0, 0] };
  }
  const tilde = /^~(\d+)\.(\d+)\.(\d+)/.exec(text);
  if (tilde) {
    const [, maj, min, pat] = tilde.map(Number);
    return { lower: [maj, min, pat], upper: [maj, min + 1, 0] };
  }
  const exact = parseVersion(text);
  // A bare version is an EXACT pin — it admits only itself, not everything at or above it.
  if (exact && /^\d/.test(text)) return { lower: exact, upper: null, exact: true };
  return null;
}

/** Does `range` admit `version`, comparing full major.minor.patch (not just the major)? */
function rangeAdmits(range, version) {
  const bounds = resolveBounds(range);
  const v = parseVersion(version);
  if (!bounds || !v) return false;
  if (bounds.exact) return cmpVersion(v, bounds.lower) === 0;
  if (cmpVersion(v, bounds.lower) < 0) return false;
  if (bounds.upper && cmpVersion(v, bounds.upper) >= 0) return false;
  return true;
}

const pkg = JSON.parse(readFileSync(reactPkgPath, "utf8"));
const devReact = pkg.devDependencies?.react;
const devTypes = pkg.devDependencies?.["@types/react"];
const peerReact = pkg.peerDependencies?.react;

const problems = [];
const pinnedMajor = majorOf(REFLEX_PINNED_REACT);

// The dev React range must actually admit the host's pinned React (not merely be "major >= 19"):
// a range like ^19.5.0 or ^20 would type-check against a React the Reflex host never runs.
if (!rangeAdmits(devReact, REFLEX_PINNED_REACT)) {
  problems.push(
    `dev react "${devReact}" does not admit Reflex's pinned React ${REFLEX_PINNED_REACT} (F2-DR alignment).`,
  );
}
// @types/react is versioned independently of react at the patch level, so match the MAJOR of the
// host React (types-vs-runtime major mismatch is the fidelity bug F2-DR flagged).
if (majorOf(devTypes) !== pinnedMajor) {
  problems.push(
    `dev @types/react "${devTypes}" is not major ${pinnedMajor} — align to the runtime React major.`,
  );
}
if (!peerReact) {
  problems.push("react must be declared as a peerDependency (never a dependency) — dedupe contract.");
} else if (!rangeAdmits(peerReact, REFLEX_PINNED_REACT)) {
  problems.push(
    `peer range "${peerReact}" does not admit Reflex's pinned React ${REFLEX_PINNED_REACT}.`,
  );
}
if (pkg.dependencies?.react || pkg.dependencies?.["react-dom"]) {
  problems.push("react/react-dom must NOT be a hard dependency (would risk a second React copy).");
}

console.log(`@aethercal/calendar-react react alignment:`);
console.log(`  peerDependencies.react = ${peerReact}`);
console.log(`  devDependencies.react  = ${devReact}`);
console.log(`  devDependencies.@types/react = ${devTypes}`);
console.log(`  Reflex pinned React (reference) = ${REFLEX_PINNED_REACT} [${reactSource}]`);

if (problems.length > 0) {
  console.error("\nReact alignment problems:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log("OK: React is aligned and declared as a peer dependency.");
