"""Verify the committed ``aethercal-ui`` JS bundle is present (the obsoleted ``fetch-js-bundle``).

``fetch-js-bundle`` used to *download* the bundle for source/editable installs, back when it was
gitignored. The bundle is now committed to the repo (docs/spikes/f0-10-reflex-tsx.md, "Resolution
at F0 integration"), so a fresh clone / ``uv sync`` already has it — there is nothing to fetch.

This replaces the old print-only stub with an honest check: it confirms the committed bundle is on
disk and non-empty (exit 0), and if it somehow is not, explains how to restore it (exit 1). So
``uv run poe fetch-js-bundle`` stays a meaningful "is my source tree complete?" probe for a
Node-less contributor, without pretending to fetch anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent / "src/aethercal/ui/assets/aethercal-calendar.js"


def main() -> int:
    if BUNDLE.is_file() and BUNDLE.stat().st_size > 0:
        print(f"OK: committed JS bundle present ({BUNDLE.stat().st_size} bytes) -> {BUNDLE}")
        print("Note: `fetch-js-bundle` is obsolete — the bundle is committed, nothing to fetch.")
        return 0
    print(
        f"FAIL: committed JS bundle missing/empty at {BUNDLE}. It is normally committed to the "
        "repo; restore it with `git checkout -- <path>`, or rebuild it with `pnpm build` in "
        "packages/aethercal-ui/js (needs Node).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
