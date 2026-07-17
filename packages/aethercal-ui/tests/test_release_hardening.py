"""F2-H hardening guards for the packaged ``aethercal-ui`` release.

These are regression fences, not feature tests: each one fails LOUDLY inside the normal
cross-platform pytest matrix the day a known spike fragility (docs/spikes/f0-10-reflex-tsx.md)
regresses, instead of letting it reach production.

Covered:

- **reflex_base smoke test** — the ``field()`` used to declare component props lives on Reflex's
  INTERNAL ``reflex_base`` package, not the public ``reflex`` surface (F0-10 "What was awkward").
  A Reflex upgrade could rename or restructure it; this turns that from a runtime ``ImportError``
  in a deployed app into a red CI run on the version bump.
- **bundle budget** — the committed JS bundle must stay under a hard size ceiling. Keeping
  ``react`` external is what keeps it small; a regression that vendors React (or gross scope
  creep toward "a FullCalendar of our own") would blow past it (AetherCal-06 §10, §13).
- **wheel-without-asset gate** — hatchling silently produces a VALID wheel that is MISSING the JS
  bundle if the asset file is absent, with no warning (F0-10 "silent failure mode"). The release
  helpers refuse to package, or to accept, an asset-less wheel.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_PKG_DIR = Path(__file__).resolve().parents[1]
_ASSET_REL = Path("src/aethercal/ui/assets/aethercal-calendar.js")
_WHEEL_ARCNAME = "aethercal/ui/assets/aethercal-calendar.js"
_CHECK_REACT = _PKG_DIR / "js" / "scripts" / "check-react-alignment.mjs"


def _load_build_release() -> ModuleType:
    """Import the release helper module by path (scripts/ is tooling, not an installed package)."""
    path = _PKG_DIR / "scripts" / "build_release.py"
    spec = importlib.util.spec_from_file_location("aethercal_ui_build_release", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------------------
# Release helpers — verify_source_asset() + wheel_contains_asset() (item #5)
# --------------------------------------------------------------------------------------


def test_verify_source_asset_returns_path_when_present(tmp_path: Path) -> None:
    br = _load_build_release()
    asset = tmp_path / _ASSET_REL
    asset.parent.mkdir(parents=True)
    asset.write_text("// bundle\n", encoding="utf-8")
    assert br.verify_source_asset(tmp_path) == asset


def test_verify_source_asset_raises_when_missing(tmp_path: Path) -> None:
    br = _load_build_release()
    with pytest.raises(br.AssetMissingError, match="missing"):
        br.verify_source_asset(tmp_path)


def test_verify_source_asset_raises_when_empty(tmp_path: Path) -> None:
    br = _load_build_release()
    asset = tmp_path / _ASSET_REL
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"")
    with pytest.raises(br.AssetMissingError, match="empty"):
        br.verify_source_asset(tmp_path)


def _make_wheel(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_wheel_contains_asset_true_when_present(tmp_path: Path) -> None:
    br = _load_build_release()
    wheel = _make_wheel(
        tmp_path / "ok.whl",
        {"aethercal/ui/__init__.py": b"x = 1\n", _WHEEL_ARCNAME: b"// bundle\n"},
    )
    assert br.wheel_contains_asset(wheel) is True


def test_wheel_contains_asset_false_when_absent(tmp_path: Path) -> None:
    br = _load_build_release()
    # A "valid-looking" wheel that silently dropped the bundle — exactly hatchling's failure mode.
    wheel = _make_wheel(tmp_path / "noasset.whl", {"aethercal/ui/__init__.py": b"x = 1\n"})
    assert br.wheel_contains_asset(wheel) is False


def test_wheel_contains_asset_false_when_empty(tmp_path: Path) -> None:
    br = _load_build_release()
    wheel = _make_wheel(tmp_path / "empty.whl", {_WHEEL_ARCNAME: b""})
    assert br.wheel_contains_asset(wheel) is False


def _fake_uv_build(entries: dict[str, bytes]):
    """Return a ``subprocess.run`` stand-in that drops a wheel with ``entries`` into --out-dir."""

    def run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        staging = Path(cmd[cmd.index("--out-dir") + 1])
        _make_wheel(staging / "aethercal_ui-0.0.0-py3-none-any.whl", entries)
        return subprocess.CompletedProcess(cmd, 0)

    return run


def test_build_wheel_moves_a_valid_wheel_into_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    br = _load_build_release()
    out_dir = tmp_path / "dist"
    monkeypatch.setattr(br.subprocess, "run", _fake_uv_build({br.WHEEL_ARCNAME: b"// bundle\n"}))
    result = br.build_wheel(tmp_path, out_dir)
    assert result.parent == out_dir
    assert br.wheel_contains_asset(result)


def test_build_wheel_never_leaves_an_asset_less_wheel_in_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate hatchling's silent drop: uv build yields a wheel with NO bundle. The gate must raise
    # AND leave nothing behind in out_dir (the whole point — never publish the invalid artifact).
    br = _load_build_release()
    out_dir = tmp_path / "dist"
    monkeypatch.setattr(
        br.subprocess, "run", _fake_uv_build({"aethercal/ui/__init__.py": b"x = 1\n"})
    )
    with pytest.raises(br.AssetMissingError, match="MISSING"):
        br.build_wheel(tmp_path, out_dir)
    assert list(out_dir.glob("*.whl")) == []


# --------------------------------------------------------------------------------------
# reflex_base internal-import smoke test (item #3)
# --------------------------------------------------------------------------------------


def test_reflex_base_field_import_resolves() -> None:
    """The internal ``field()`` Reflex add-ons use to declare props must still import + be callable.

    If a Reflex upgrade renames/restructures ``reflex_base`` this fails here (a version bump), not
    as an ``ImportError`` when the calendar module is first imported in production.
    """
    try:
        module = importlib.import_module("reflex_base.components.component")
    except ImportError as exc:  # pragma: no cover - only on a breaking Reflex bump
        pytest.fail(
            "reflex_base.components.component no longer imports — Reflex likely changed its "
            f"internal layout. calendar.py depends on it (AetherCal-06 §2, deuda #1). Detail: {exc}"
        )
    field = getattr(module, "field", None)
    assert callable(field), (
        "reflex_base.components.component.field is gone or not callable — the prop-declaration "
        "helper calendar.py uses (AetherCal-06 §2, deuda #1) must exist."
    )


def test_calendar_module_binds_the_reflex_base_field() -> None:
    """calendar.py aliases the internal ``field`` as ``rx_field``; guard that binding is real."""
    component_module = importlib.import_module("reflex_base.components.component")
    calendar = importlib.import_module("aethercal.ui.calendar")
    assert calendar.rx_field is component_module.field


# --------------------------------------------------------------------------------------
# Bundle budget (item #2)
# --------------------------------------------------------------------------------------

# Hard ceiling for the committed, minified JS bundle. Current size ~88.7 KB: the four original
# surfaces (month, week/day, list) plus the RF-28 resource timeline, with the interaction and
# reconciliation machines, theming and i18n.
#
# Raised from 90 KB to 100 KB in RF-28 — deliberately, and with the growth accounted for. The
# timeline is a FIFTH surface (its own core geometry, view, gestures and stylesheet) and took the
# bundle from ~59.7 KB to ~88.7 KB. Confirmed at the time that React is still EXTERNAL (no React
# internals in the output): this is feature growth, not the failure the guard exists for.
#
# The ceiling still trips on that failure: an accidental React vendor (dropping the esbuild
# `external`) would add react-dom (~130 KB minified) and blow straight past this. Raise
# deliberately, with a rebuild, if the real feature set ever legitimately needs it — never to paper
# over a silently-bloated bundle.
_BUNDLE_BUDGET_BYTES = 100_000


def test_committed_bundle_exists_and_is_non_empty() -> None:
    bundle = _PKG_DIR / _ASSET_REL
    assert bundle.is_file(), f"committed JS bundle missing at {bundle}"
    assert bundle.stat().st_size > 0, f"committed JS bundle is empty at {bundle}"


def test_committed_bundle_within_budget() -> None:
    bundle = _PKG_DIR / _ASSET_REL
    size = bundle.stat().st_size
    assert size <= _BUNDLE_BUDGET_BYTES, (
        f"JS bundle is {size} bytes, over the {_BUNDLE_BUDGET_BYTES}-byte budget. If this is real "
        "feature growth, raise _BUNDLE_BUDGET_BYTES deliberately; if it jumped by ~130 KB, react "
        "is being vendored — keep it external (esbuild `external`, the F2-DR dedupe contract)."
    )


# --------------------------------------------------------------------------------------
# check-react-alignment.mjs derivation + strict mode (item #6)
# --------------------------------------------------------------------------------------

_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node not on PATH")
# A python name guaranteed not to resolve, to force the derivation probe to fail deterministically.
_BROKEN_PROBE = {"AETHERCAL_REACT_PROBE_PYTHON": "__aethercal_no_such_python__"}


def _run_check_react(
    *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    assert _NODE is not None
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [_NODE, str(_CHECK_REACT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@_needs_node
def test_check_react_derives_pin_from_installed_reflex() -> None:
    # Only meaningful where Reflex is importable (the real gate environment); the venv provides it.
    pytest.importorskip("reflex_base")
    result = _run_check_react("--require-derived")
    assert result.returncode == 0, result.stderr
    assert "installed Reflex" in result.stdout


@_needs_node
def test_check_react_strict_mode_fails_hard_when_not_derivable() -> None:
    # Both interpreters unresolvable + --require-derived -> must NOT pass on the fallback.
    result = _run_check_react("--require-derived", extra_env=_BROKEN_PROBE)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "could not be derived" in result.stderr


@_needs_node
def test_check_react_falls_back_when_not_strict() -> None:
    # Same unresolvable probe, but without --require-derived: warns and passes on the fallback so a
    # Node-only local run (`pnpm check-react`) still validates the package's structural ranges.
    result = _run_check_react(extra_env=_BROKEN_PROBE)
    assert result.returncode == 0, result.stderr
    assert "falling back" in result.stderr.lower()
