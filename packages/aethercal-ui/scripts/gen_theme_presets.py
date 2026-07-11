"""Generate ``theme-presets.json`` from the Python :class:`aethercal.ui.theme.Theme` presets.

The theme presets are the CROSS-LANGUAGE token contract for the calendar's ``--ac-*`` color
tokens (AetherCal-06 §7). They are GENERATED from the single source of truth — the ``Theme``
presets the Python model declares — and committed into the JS React package
(``js/packages/react/src``), where a vitest test locks the TypeScript ``PRESETS`` against them. A
Python drift test re-runs this generator and fails if the committed file is stale, so the Python
and TypeScript sides can never silently diverge (same anti-drift lock as ``calendar-props.schema``).

Run ``uv run poe gen-theme`` to regenerate after changing a preset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Importing the ``aethercal.ui`` package runs ``rx.asset`` (in calendar.py) at module scope, which
# in a real Reflex app symlinks the bundle into the cwd; this repo root is not one, so skip that
# side effect (same guard as gen_calendar_schema.py and the test conftest).
os.environ.setdefault("REFLEX_BACKEND_ONLY", "1")

from aethercal.ui.theme import PRESET_NAMES, PRESETS

# The committed file lives beside the TS theming module that imports it, so esbuild bundles it in.
PRESETS_PATH = (
    Path(__file__).resolve().parents[1] / "js" / "packages" / "react" / "src" / "theme-presets.json"
)


def build_presets() -> dict[str, dict[str, str]]:
    """Build ``{ preset_name: { "--ac-token": value } }`` (preset- then field-declaration order)."""
    return {name: PRESETS[name].to_css_vars() for name in PRESET_NAMES}


def render() -> str:
    """Return the exact committed text (2-space indent + newline) for byte-for-byte drift."""
    # No sort_keys: the semantic order (preset order, then token declaration order) is more
    # legible than an alphabetized one, and the TS side compares by value/key-set, not order.
    return json.dumps(build_presets(), indent=2) + "\n"


def main() -> None:
    """Write the generated presets to their committed path."""
    PRESETS_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {PRESETS_PATH}")


if __name__ == "__main__":
    main()
