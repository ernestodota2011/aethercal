// F2-A base for the "React that matches" CI check (F2-DR follow-up).
//
// The dedupe contract (docs/spikes/f2-dr-react-dedupe.md) rests on the calendar bundle keeping
// `react` EXTERNAL and declaring it as a *peerDependency*, so the host app's single React is the
// only one at runtime. Reflex pins its frontend React (today 19.2.6). F2-DR flagged one gap: the
// JS package built against React 18 dev types. F2-A aligns dev `react`/`@types/react` to 19; this
// script guards that alignment and that the peer range still admits the host's React.
//
// When Reflex bumps its pinned React (`reflex_base/constants/installer.py:_determine_react_version`),
// update REFLEX_PINNED_REACT here and re-run the F2-DR positive/negative checks (cheap insurance).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const REFLEX_PINNED_REACT = "19.2.6";
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
console.log(`  Reflex pinned React (reference) = ${REFLEX_PINNED_REACT}`);

if (problems.length > 0) {
  console.error("\nReact alignment problems:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log("OK: React is aligned and declared as a peer dependency.");
