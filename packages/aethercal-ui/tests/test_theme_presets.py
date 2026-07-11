"""Drift guard for the cross-language theme-presets contract (AetherCal-06 §7, F2-E).

``theme-presets.json`` is generated from the Python :class:`aethercal.ui.theme.Theme` presets and
committed into the JS React package, where a vitest test locks the TS ``PRESETS`` against it. This
test asserts the committed file still equals a fresh generation, so a preset change that was not
re-generated (``uv run poe gen-theme``) fails CI instead of letting the two languages diverge.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from aethercal.ui.theme import PRESET_NAMES

_GENERATOR = Path(__file__).resolve().parents[1] / "scripts" / "gen_theme_presets.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_theme_presets", _GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_presets_match_the_python_theme() -> None:
    gen = _load_generator()
    committed = gen.PRESETS_PATH.read_text(encoding="utf-8")
    assert committed == gen.render(), (
        "theme-presets.json is stale relative to the Python Theme presets — "
        "run `uv run poe gen-theme` and commit the result."
    )


def test_presets_file_exists_at_the_path_vitest_imports() -> None:
    gen = _load_generator()
    assert gen.PRESETS_PATH.name == "theme-presets.json"
    assert gen.PRESETS_PATH.parent.name == "src"
    assert gen.PRESETS_PATH.exists()


def test_committed_presets_are_complete_and_well_formed() -> None:
    gen = _load_generator()
    data = json.loads(gen.PRESETS_PATH.read_text(encoding="utf-8"))
    # Every preset is present, and each is a full, --ac-*-only token map with identical key sets.
    assert set(data) == set(PRESET_NAMES)
    key_sets = [tuple(tokens) for tokens in data.values()]
    assert len(set(key_sets)) == 1, "all presets must expose the same token set"
    for tokens in data.values():
        assert all(name.startswith("--ac-") for name in tokens)
        assert all(isinstance(value, str) and value for value in tokens.values())
